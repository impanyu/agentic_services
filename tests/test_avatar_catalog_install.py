import importlib.util
import json
from pathlib import Path

import pytest
from PIL import Image


def installer():
    source = Path(__file__).parents[1] / 'scripts/install-photo-scout-avatar-expansion.py'
    spec = importlib.util.spec_from_file_location('avatar_install', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def setup(tmp_path, transparent=True):
    directory = tmp_path / 'avatars/characters'
    directory.mkdir(parents=True)
    image = Image.new('RGBA', (64, 96), (0, 0, 0, 0 if transparent else 255))
    for x in range(20, 44):
        for y in range(10, 86):
            image.putpixel((x, y), (180, 120, 80, 255))
    image.save(directory / 'test-person.png')
    catalog = tmp_path / 'avatars/presets.json'
    catalog.write_text('{"items": []}\n')
    report = tmp_path / 'results.json'
    report.write_text(json.dumps({'items': [{'name': 'Test person', 'slug': 'test-person',
        'category': 'World history', 'visualStyle': 'Watercolor', 'state': 'complete'}]}))
    return catalog, report


def test_minimum_failure_preserves_live_catalog(tmp_path):
    catalog, report = setup(tmp_path)
    before = catalog.read_bytes()
    with pytest.raises(ValueError, match='distinct characters'):
        installer().install(tmp_path, report, 500)
    assert catalog.read_bytes() == before


def test_valid_artwork_gets_thumbnail_and_manifest(tmp_path):
    catalog, report = setup(tmp_path)
    data = json.loads(report.read_text())
    data['items'][0]['featured'] = True
    report.write_text(json.dumps(data))
    result = installer().install(tmp_path, report, 1)
    assert result['total'] == result['distinctCharacters'] == 1
    item = json.loads(catalog.read_text())['items'][0]
    assert item['featured'] is True
    assert (tmp_path / item['thumbnailUrl'][2:]).is_file()
    assert installer().install(tmp_path, report, 1)['added'] == 0


def test_opaque_artwork_cannot_enter_catalog(tmp_path):
    catalog, report = setup(tmp_path, transparent=False)
    with pytest.raises(ValueError, match='transparent'):
        installer().install(tmp_path, report, 1)
    assert json.loads(catalog.read_text())['items'] == []


def test_alternative_name_order_is_one_person():
    module = installer()
    assert module.person_key('Fukuzawa Yukichi') == module.person_key('Yukichi Fukuzawa')
    assert module.person_key('Albert Einstein · Documentary') == module.person_key('Albert Einstein')


def add_variant(tmp_path, report, slug, style):
    image = Image.open(tmp_path / 'avatars/characters/test-person.png')
    image.putpixel((25, 25), (20, 90, 160, 255))
    image.save(tmp_path / f'avatars/characters/{slug}.png')
    report.write_text(json.dumps({'items': [{'name': 'Test person · Variant', 'slug': slug,
        'category': 'World history', 'visualStyle': style, 'state': 'complete'}]}))


def test_same_character_with_different_medium_is_preserved(tmp_path):
    catalog, report = setup(tmp_path)
    module = installer()
    module.install(tmp_path, report, 1)
    add_variant(tmp_path, report, 'clay-person', 'Clay stop-motion')
    result = module.install(tmp_path, report, 1)
    assert result['added'] == 1
    assert result['total'] == 2 and result['distinctCharacters'] == 1
    assert [item['visualStyle'] for item in json.loads(catalog.read_text())['items']] == ['Watercolor', 'Clay stop-motion']


def test_same_character_and_medium_does_not_gain_a_pose_variant(tmp_path):
    catalog, report = setup(tmp_path)
    module = installer()
    module.install(tmp_path, report, 1)
    add_variant(tmp_path, report, 'another-pose', 'Watercolor')
    result = module.install(tmp_path, report, 1)
    assert result['added'] == 0
    assert result['rejected'][0]['reason'] == 'duplicate_character_style'


def test_unknown_existing_medium_requires_review_instead_of_name_only_deletion(tmp_path):
    catalog, report = setup(tmp_path)
    module = installer()
    module.install(tmp_path, report, 1)
    data = json.loads(catalog.read_text())
    del data['items'][0]['visualStyle']
    catalog.write_text(json.dumps(data))
    add_variant(tmp_path, report, 'clay-person', 'Clay stop-motion')
    result = module.install(tmp_path, report, 1)
    assert result['rejected'][0]['reason'] == 'style_review_required'
    assert result['total'] == 1


def test_photographic_genre_names_are_not_distinct_media():
    module = installer()
    assert module.style_key('Photorealistic likeness') == module.style_key('Fashion editorial') == module.style_key('Photorealistic')
