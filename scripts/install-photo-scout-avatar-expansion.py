#!/usr/bin/env python3
"""Validate generated avatar assets and atomically extend the preset catalog.

No generation or network calls. Existing avatars are preserved. Only successful,
decodable, nonduplicate PNG assets with actual transparency can enter the catalog.
"""
import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image


def person_key(name):
    key = name.split(' · ')[0].casefold()
    return {'fukuzawa yukichi': 'yukichi fukuzawa'}.get(key, key)


def install(root, report, minimum):
    catalog_path = root / 'avatars' / 'presets.json'
    catalog = json.loads(catalog_path.read_text())
    items = catalog['items']
    known_ids = {item['id'] for item in items}
    known_people = {person_key(item['name']) for item in items}
    seen = {hashlib.sha256((root / item['imageUrl'][2:]).read_bytes()).hexdigest()
            for item in items}
    added, rejected = [], []
    for row in json.loads(report.read_text())['items']:
        if row['state'] != 'complete':
            rejected.append({'name': row['name'], 'reason': 'generation_failed'})
            continue
        identifier = 'preset:' + row['slug']
        if identifier in known_ids:
            continue
        if person_key(row['name']) in known_people:
            rejected.append({'name': row['name'], 'reason': 'duplicate_character'})
            continue
        path = root / 'avatars' / 'characters' / (row['slug'] + '.png')
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest in seen:
                raise ValueError('Duplicate artwork')
            with Image.open(path) as image:
                image.load()
                if image.format != 'PNG' or image.mode != 'RGBA':
                    raise ValueError('Expected RGBA PNG')
                alpha = image.getchannel('A')
                transparent_fraction = sum(alpha.histogram()[:16]) / (image.width * image.height)
                if alpha.getextrema()[1] < 64 or transparent_fraction < 0.02:
                    raise ValueError('Missing usable transparent background or subject')
                preview = image.copy()
                preview.thumbnail((192, 192))
                thumbnail = root / 'avatars' / 'thumbnails' / (row['slug'] + '.webp')
                thumbnail.parent.mkdir(parents=True, exist_ok=True)
                preview.save(thumbnail, 'WEBP', quality=85, method=6)
            if thumbnail.stat().st_size >= 100_000:
                raise ValueError('Thumbnail too large')
        except (ValueError, OSError) as error:
            rejected.append({'name': row['name'], 'reason': str(error)})
            continue
        added.append({'id': identifier, 'name': row['name'], 'category': row['category'],
            'visualStyle': row['visualStyle'], 'aliases': row.get('aliases', []),
            **({'featured': True} if row.get('featured') is True else {}),
            'imageUrl': './avatars/characters/' + row['slug'] + '.png',
            'thumbnailUrl': './avatars/thumbnails/' + row['slug'] + '.webp'})
        known_ids.add(identifier)
        known_people.add(person_key(row['name']))
        seen.add(digest)
    if len(known_people) < minimum:
        raise ValueError(f'Only {len(known_people)} distinct characters; need {minimum}. '
                         f'Catalog not changed. Rejected: {rejected}')
    catalog['items'].extend(added)
    temporary = catalog_path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(catalog_path)
    return {'added': len(added), 'total': len(catalog['items']),
            'distinctCharacters': len(known_people), 'rejected': rejected}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--site', type=Path, default=Path('photo-scout-site'))
    parser.add_argument('--minimum', type=int, default=500)
    args = parser.parse_args()
    print(json.dumps(install(args.site, args.report, args.minimum), ensure_ascii=False))
