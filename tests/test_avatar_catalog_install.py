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
    result = installer().install(tmp_path, report, 1)
    assert result['total'] == result['distinctCharacters'] == 1
    item = json.loads(catalog.read_text())['items'][0]
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
