import asyncio
import base64
import io
import json
import math
import sqlite3

import httpx
import numpy as np
from PIL import Image
import pytest

from agentic_services.photo_scout import sources, streetview_tiles as tiles


def fixture_tile(width=512,height=256):
    colors = [(220, 30, 30), (220, 140, 30), (220, 220, 30), (30, 220, 30),
              (30, 220, 220), (30, 30, 220), (140, 30, 220), (220, 30, 140)]
    image = Image.new('RGB', (512, 512), (255, 0, 255))
    panorama = Image.new('RGB', (width,height))
    for x, color in enumerate(colors):
        panorama.paste(color, (round(x*width/8),0,round((x+1)*width/8),height))
    for offset in range(0,512,width):
        image.paste(panorama,(offset,0))
    out = io.BytesIO(); image.save(out, 'PNG')
    return out.getvalue()


@pytest.fixture
def provider(tmp_path, monkeypatch, request):
    width,height = getattr(request,'param',(512,256))
    tiles._sessions.clear(); tiles._session_tasks.clear(); tiles._panoramas.clear(); tiles._pending.clear()
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_API_KEY', 'secret-fixture')
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_ENABLED', '1')
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_IMAGE_MODE', 'tiles-low')
    monkeypatch.setenv('WEB_EVIDENCE_DB', str(tmp_path/'db'))
    monkeypatch.delenv('PHOTO_SCOUT_GOOGLE_TILES_API_KEY', raising=False)
    monkeypatch.delenv('PHOTO_SCOUT_GOOGLE_DAILY_IMAGE_LIMIT', raising=False)
    calls = []
    async def handler(request):
        calls.append(request)
        await asyncio.sleep(.002)
        assert request.url.host == 'tile.googleapis.com'
        if request.url.path.endswith('/createSession'):
            assert json.loads(request.content)['mapType'] == 'streetview'
            return httpx.Response(200, json={'session':'test-session','expiry':9999999999,'tileWidth':512,'tileHeight':512})
        if request.url.path.endswith('/metadata'):
            return httpx.Response(200, json={'imageWidth':width*32,'imageHeight':height*32,'tileWidth':512,'tileHeight':512,'heading':90,'tilt':90,'roll':0,'copyright':'Fixture provider'})
        assert request.url.path.endswith('/tiles/0/0/0')
        native_zoom=math.ceil(math.log2(width*32/512))
        return httpx.Response(200, content=fixture_tile(math.ceil(width*32/2**native_zoom),math.ceil(height*32/2**native_zoom)))
    real = httpx.AsyncClient
    monkeypatch.setattr(tiles.httpx, 'AsyncClient', lambda **kw: real(transport=httpx.MockTransport(handler)))
    return calls, tmp_path/'db'


def decode(data):
    image = Image.open(io.BytesIO(base64.b64decode(data.split(',',1)[1])))
    image.load()
    return image


@pytest.mark.parametrize('provider',[(512,256),(416,208),(168,84)],indirect=True)
def test_eight_headings_and_later_framing_fetch_one_tile(provider):
    calls, db = provider
    async def run():
        images = await asyncio.gather(*(sources.google_image_data(f'google-streetview://fixture/{h}/0/120') for h in range(0,360,45)))
        await sources.google_image_data('google-streetview://fixture/135/25/45')
        return images
    images = asyncio.run(run())
    assert len(calls) == 3  # one free session + free metadata + one paid tile
    assert all(decode(image).size == (256,256) for image in images)
    assert len(set(images)) == 8
    # Correct heading calibration: requested east equals the panorama center.
    pixel = decode(images[2]).getpixel((135,128))
    assert pixel[1] > 170 and pixel[2] > 170 and pixel[0] < 80
    # The magenta padding below the sphere never enters rendered scene pixels.
    array = np.asarray(decode(images[2]))[:240]
    assert not ((array[:,:,0]>230)&(array[:,:,1]<20)&(array[:,:,2]>230)).any()
    with sqlite3.connect(db) as connection:
        assert connection.execute('SELECT requests FROM photo_scout_google_budget').fetchone()[0] == 1
        assert connection.execute('SELECT kind,requests FROM photo_scout_google_image_usage').fetchone() == ('streetview-tile-z0',1)


def test_tile_failure_does_not_fall_back_to_expensive_static(provider, monkeypatch):
    calls, db = provider
    async def fail(*args):
        raise ValueError('Provider unavailable')
    monkeypatch.setattr(tiles, '_load_panorama', fail)
    async def run():
        return await asyncio.gather(*(sources.google_image_data(f'google-streetview://fixture/{h}') for h in range(0,360,45)), return_exceptions=True)
    assert all(isinstance(e, ValueError) for e in asyncio.run(run()))
    assert not calls
    assert not tiles._pending


def test_expired_panorama_refetches_once_for_concurrent_views(provider):
    calls, _ = provider
    asyncio.run(sources.google_image_data('google-streetview://fixture/90'))
    key = next(iter(tiles._panoramas)); _, image = tiles._panoramas[key]
    tiles._panoramas[key] = (0, image)
    async def run():
        return await asyncio.gather(*(sources.google_image_data(f'google-streetview://fixture/{h}') for h in (0,45,90)))
    asyncio.run(run())
    assert sum(r.url.path.endswith('/tiles/0/0/0') for r in calls) == 2
    assert sum(r.url.path.endswith('/createSession') for r in calls) == 1


def test_projection_wraps_seam_and_handles_poles():
    panorama = Image.new('RGB', (512,256), (30,100,160))
    meta = {'heading':0,'tilt':90,'roll':0,'copyright':'Fixture'}
    for heading in (0,179,180,359):
        for pitch in (-90,0,90):
            data = tiles.project(panorama, meta, heading, pitch, 120)
            assert decode(data).getpixel((128,128)) == pytest.approx((30,100,160), abs=3)


def test_invalid_reference_makes_no_provider_requests(provider):
    calls, _ = provider
    with pytest.raises(ValueError):
        asyncio.run(sources.google_image_data('google-streetview://fixture/360/0/120'))
    assert not calls
