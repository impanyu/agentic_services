"""Lowest-resolution Google panorama, projected locally into any requested view.

One zoom-0 tile covers the full sphere. Bytes live only in a bounded, short-lived
in-process cache, including single-flight fetches for concurrent eight-view batches.
No static-image fallback: a provider failure must not silently increase spend.
"""
from __future__ import annotations

import asyncio
import base64
from collections import OrderedDict
import hashlib
import io
import math
import os
import time

import httpx
import numpy as np
from PIL import Image, ImageDraw

BASE = 'https://tile.googleapis.com/v1'
_CACHE_SECONDS = 600
_CACHE_SIZE = 128
_sessions = {}
_session_tasks = {}
_panoramas = OrderedDict()
_pending = {}


def enabled():
    mode = os.getenv('PHOTO_SCOUT_GOOGLE_IMAGE_MODE', 'tiles-low')
    if mode not in ('tiles-low', 'static'):
        raise ValueError('Invalid Google image delivery mode')
    return mode == 'tiles-low'


def profile():
    return 'google-tiles-z0-v1' if enabled() else 'google-static-640-v1'


async def _json(client, method, path, *, params, body=None):
    response = await client.request(method, BASE + path, params=params, json=body)
    if response.status_code != 200:
        # Never include the credential-bearing URL or provider body in errors.
        raise ValueError(f'Street View Tiles request failed ({response.status_code})')
    return response.json()


async def _create_session(key):
    async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
        data = await _json(client, 'POST', '/createSession', params={'key': key},
            body={'mapType': 'streetview', 'language': 'en-US', 'region': 'US'})
    if not isinstance(data.get('session'), str):
        raise ValueError('Street View Tiles session unavailable')
    return data


async def _session(key, identity):
    saved = _sessions.get(identity)
    if saved and float(saved['expiry']) > time.time() + 60:
        return saved
    task = _session_tasks.get(identity)
    if task is None or task.get_loop() is not asyncio.get_running_loop():
        task = asyncio.create_task(_create_session(key))
        _session_tasks[identity] = task
    try:
        data = await asyncio.shield(task)
        _sessions[identity] = data
        return data
    finally:
        if task.done() and _session_tasks.get(identity) is task:
            _session_tasks.pop(identity, None)


async def _load_panorama(pano, key, identity):
    session = await _session(key, identity)
    params = {'key': key, 'session': session['session'], 'panoId': pano}
    async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
        meta = await _json(client, 'GET', '/streetview/metadata', params=params)
        width, height = int(meta['imageWidth']), int(meta['imageHeight'])
        if width <= 0 or height <= 0 or width < height:
            raise ValueError('Invalid Street View panorama dimensions')
        # Metadata and sessions are free. Count the actual attempted paid tile.
        from .sources import record_google_image_request
        record_google_image_request('streetview-tile-z0')
        async with client.stream('GET', BASE + '/streetview/tiles/0/0/0', params=params) as response:
            if response.status_code != 200:
                raise ValueError(f'Street View tile unavailable ({response.status_code})')
            blob = bytearray()
            async for chunk in response.aiter_bytes():
                blob.extend(chunk)
                if len(blob) > 1_000_000:
                    raise ValueError('Street View tile exceeds limit')
    with Image.open(io.BytesIO(blob)) as tile:
        tile.load()
        if tile.width > 1024 or tile.height > 1024:
            raise ValueError('Unexpected zoom-zero tile dimensions')
        # The metadata dimensions describe the highest native level, which is
        # lower than z5 for some contributor panoramas. Halve that pyramid
        # down to z0 instead of assuming every source has five levels.
        # UGC imagery can repeat horizontally outside that rectangle, while
        # vertical padding is black; neither belongs to the sphere.
        native_zoom = max(0, math.ceil(math.log2(width/int(meta.get('tileWidth', tile.width)))))
        if native_zoom > 5:
            raise ValueError('Unsupported Street View panorama pyramid')
        content_width, content_height = math.ceil(width/2**native_zoom), math.ceil(height/2**native_zoom)
        if not 1 <= content_width <= tile.width or not 1 <= content_height <= tile.height:
            raise ValueError('Unsupported lowest-resolution panorama dimensions')
        panorama = tile.convert('RGB').crop((0, 0, content_width, content_height))
    return panorama, meta


async def panorama(pano):
    key = os.getenv('PHOTO_SCOUT_GOOGLE_TILES_API_KEY') or os.environ['PHOTO_SCOUT_GOOGLE_API_KEY']
    identity = hashlib.sha256(key.encode()).hexdigest()
    cache_key = (identity, pano)
    now = time.monotonic()
    for expired in [k for k, (expiry, _) in _panoramas.items() if expiry <= now]:
        _panoramas.pop(expired, None)
    saved = _panoramas.get(cache_key)
    if saved:
        _panoramas.move_to_end(cache_key)
        return saved[1]
    task = _pending.get(cache_key)
    if task is None or task.get_loop() is not asyncio.get_running_loop():
        task = asyncio.create_task(_load_panorama(pano, key, identity))
        _pending[cache_key] = task
    try:
        result = await asyncio.shield(task)
        _panoramas[cache_key] = (time.monotonic() + _CACHE_SECONDS, result)
        _panoramas.move_to_end(cache_key)
        while len(_panoramas) > _CACHE_SIZE:
            _panoramas.popitem(last=False)
        return result
    finally:
        if task.done() and _pending.get(cache_key) is task:
            _pending.pop(cache_key, None)


def _basis(heading, pitch, roll=0):
    h, p, r = map(math.radians, (heading, pitch, roll))
    forward = np.array([math.sin(h)*math.cos(p), math.sin(p), math.cos(h)*math.cos(p)])
    right = np.array([math.cos(h), 0, -math.sin(h)])
    up = np.cross(forward, right)
    return forward, right*math.cos(r)+up*math.sin(r), up*math.cos(r)-right*math.sin(r)


def project(panorama, meta, heading, pitch, fov):
    """Perspective projection, preserving compass orientation and panorama seam."""
    size = 256
    axis = ((np.arange(size) + .5) / size * 2 - 1) * math.tan(math.radians(fov)/2)
    x, y = np.meshgrid(axis, -axis)
    forward, right, up = _basis(heading, pitch)
    rays = forward + x[..., None]*right + y[..., None]*up
    rays /= np.linalg.norm(rays, axis=-1)[..., None]
    pf, pr, pu = _basis(float(meta.get('heading', 0)), 90-float(meta.get('tilt', 90)), float(meta.get('roll', 0)))
    px, py, pz = rays @ pr, rays @ pu, rays @ pf
    u = (np.arctan2(px, pz)/(2*math.pi) + .5) % 1
    v = .5 - np.arcsin(np.clip(py, -1, 1))/math.pi
    pixels = np.asarray(panorama)
    w, h = panorama.size
    # Bilinear sampling wraps horizontally; it must not interpolate across the
    # padding below the panoramic image or clamp at the 0/360-degree seam.
    sx, sy = u*w-.5, np.clip(v*h-.5, 0, h-1)
    ix, iy = np.floor(sx).astype(int), np.floor(sy).astype(int)
    fx, fy = (sx-ix)[..., None], (sy-iy)[..., None]
    a = pixels[iy, ix % w]*(1-fx) + pixels[iy, (ix+1) % w]*fx
    b = pixels[np.minimum(iy+1, h-1), ix % w]*(1-fx) + pixels[np.minimum(iy+1, h-1), (ix+1) % w]*fx
    image = Image.fromarray(np.clip(a*(1-fy)+b*fy, 0, 255).astype('uint8'))
    # Locally rendered previews still retain source attribution.
    draw = ImageDraw.Draw(image)
    label = 'Google · ' + str(meta.get('copyright', 'Google'))
    draw.rectangle((0, size-14, size, size), fill=(30, 30, 30))
    draw.text((3, size-13), label[:75], fill='white', font_size=10)
    out = io.BytesIO()
    image.save(out, format='JPEG', quality=65, optimize=True)
    return 'data:image/jpeg;base64,' + base64.b64encode(out.getvalue()).decode()


async def image_data(pano, heading, pitch, fov):
    image, meta = await panorama(pano)
    return await asyncio.to_thread(project, image, meta, heading, pitch, fov)
