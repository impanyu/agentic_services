"""Budget-resolution Google panorama, projected locally into any requested view.

Fetch a complete sphere at the configured tier once. Retain original tiles through their
provider freshness deadline, including across application restarts.
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
from pathlib import Path
import re
import sqlite3
import time

import httpx
import numpy as np
from PIL import Image, ImageDraw

BASE = 'https://tile.googleapis.com/v1'
_CACHE_SIZE = 512
_DISK_BYTES = 64 * 1024 * 1024
_sessions = {}
_session_tasks = {}
_panoramas = OrderedDict()
_pending = {}


def _fresh_until(headers):
    directives = headers.get('cache-control', '').lower()
    if re.search(r'(?:^|,)\s*(?:no-store|no-cache)(?:\s|,|$)', directives):
        return 0
    match = re.search(r'(?:^|,)\s*max-age\s*=\s*"?(\d+)', directives)
    if not match:
        return 0  # Do not invent a retention period absent provider permission.
    try:
        age = max(0, int(headers.get('age', '0')))
    except ValueError:
        age = 0
    return time.time() + max(0, int(match[1]) - age)


def _tile_store(identity, pano, value=None):
    # This is the backend API client's private response store, never a public
    # HTTP/CDN cache. Keep original bytes; projections are computed separately.
    path = Path(os.getenv('WEB_EVIDENCE_DB', 'data/web-evidence.db'))
    path.parent.mkdir(parents=True, exist_ok=True)
    import json
    with sqlite3.connect(path, timeout=15) as db:
        db.execute('CREATE TABLE IF NOT EXISTS photo_scout_panorama_responses '
                   '(identity TEXT, pano TEXT, expires REAL NOT NULL, used REAL NOT NULL, '
                   'body BLOB NOT NULL, metadata TEXT NOT NULL, PRIMARY KEY(identity,pano))')
        now = time.time()
        db.execute('DELETE FROM photo_scout_panorama_responses WHERE expires<=?', (now,))
        if value is not None:
            blob, meta = value
            expires = meta['_tileExpiresAt']
            if expires > now:
                db.execute('INSERT OR REPLACE INTO photo_scout_panorama_responses VALUES(?,?,?,?,?,?)',
                           (identity, pano, expires, now, blob, json.dumps(meta)))
                size = db.execute('SELECT coalesce(sum(length(body)),0) FROM photo_scout_panorama_responses').fetchone()[0]
                if size > _DISK_BYTES:
                    for old_identity, old_pano, length in db.execute(
                            'SELECT identity,pano,length(body) FROM photo_scout_panorama_responses ORDER BY used').fetchall():
                        db.execute('DELETE FROM photo_scout_panorama_responses WHERE identity=? AND pano=?', (old_identity, old_pano))
                        size -= length
                        if size <= _DISK_BYTES:
                            break
            return None
        saved = db.execute('SELECT body,metadata FROM photo_scout_panorama_responses WHERE identity=? AND pano=?', (identity, pano)).fetchone()
        if saved:
            db.execute('UPDATE photo_scout_panorama_responses SET used=? WHERE identity=? AND pano=?', (now, identity, pano))
            return saved[0], json.loads(saved[1])
    return None


def enabled():
    mode = os.getenv('PHOTO_SCOUT_GOOGLE_IMAGE_MODE', 'tiles-low')
    if mode not in ('tiles-low', 'static'):
        raise ValueError('Invalid Google image delivery mode')
    return mode == 'tiles-low'


def tile_zoom():
    zoom = int(os.getenv('PHOTO_SCOUT_GOOGLE_TILE_ZOOM', '1'))
    if zoom not in (0, 1):
        raise ValueError('Google panorama tier must be 0 or 1')
    return zoom


def profile():
    return f'google-tiles-z{tile_zoom()}-v2' if enabled() else 'google-static-640-v1'



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


def _dimensions(meta):
    width, height = int(meta['imageWidth']), int(meta['imageHeight'])
    tw, th = int(meta.get('tileWidth', 512)), int(meta.get('tileHeight', 512))
    if width <= 0 or height <= 0 or width < height or not 1 <= tw <= 1024 or not 1 <= th <= 1024:
        raise ValueError('Invalid Street View panorama dimensions')
    native = max(0, math.ceil(math.log2(width/tw)))
    if native > 5:
        raise ValueError('Unsupported Street View panorama pyramid')
    zoom = min(tile_zoom(), native)
    scale = 2**(native-zoom)
    return zoom, math.ceil(width/scale), math.ceil(height/scale), tw, th


async def _load_panorama(pano, key, identity):
    # Namespace by requested tier; old z0 bytes must never stand in for z1.
    namespace = f'{pano}:z{tile_zoom()}'
    first = await asyncio.to_thread(_tile_store, identity, namespace+':0:0')
    session = None
    async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
        if first:
            meta = first[1].copy()
        else:
            session = await _session(key, identity)
            meta = await _json(client, 'GET', '/streetview/metadata',
                params={'key': key, 'session': session['session'], 'panoId': pano})
        zoom, width, height, tw, th = _dimensions(meta)
        positions = [(x,y) for y in range(math.ceil(height/th)) for x in range(math.ceil(width/tw))]
        saved = {}
        for x,y in positions:
            value = first if (x,y)==(0,0) else await asyncio.to_thread(_tile_store, identity, f'{namespace}:{x}:{y}')
            if value:
                saved[x,y] = value
        if len(saved) < len(positions):
            session = session or await _session(key, identity)
        async def fetch_tile(x, y):
            if (x,y) in saved:
                return x, y, *saved[x,y]
            from .sources import record_google_image_request
            record_google_image_request(f'streetview-tile-z{zoom}')
            params = {'key': key, 'session': session['session'], 'panoId': pano}
            async with client.stream('GET', BASE+f'/streetview/tiles/{zoom}/{x}/{y}', params=params) as response:
                if response.status_code != 200:
                    raise ValueError(f'Street View tile unavailable ({response.status_code})')
                tile_meta = {**meta, '_tileExpiresAt': _fresh_until(response.headers)}
                blob = bytearray()
                async for chunk in response.aiter_bytes():
                    blob.extend(chunk)
                    if len(blob) > 1_000_000:
                        raise ValueError('Street View tile exceeds limit')
            # Validate before persisting; retain each original provider response.
            _decode_tile(blob, tw, th)
            await asyncio.to_thread(_tile_store, identity, f'{namespace}:{x}:{y}', (bytes(blob), tile_meta))
            return x, y, bytes(blob), tile_meta
        parts = await asyncio.gather(*(fetch_tile(x,y) for x,y in positions))
    image = Image.new('RGB', (width,height))
    for x,y,blob,tile_meta in parts:
        image.paste(_decode_tile(blob, tw, th), (x*tw,y*th))
    meta['_tileExpiresAt'] = min(part[3]['_tileExpiresAt'] for part in parts)
    return image, meta


def _decode_tile(blob, width, height):
    with Image.open(io.BytesIO(blob)) as tile:
        if tile.size != (width,height):
            raise ValueError('Unexpected Street View tile dimensions')
        tile.load()
        return tile.convert('RGB')


async def panorama(pano):
    key = os.getenv('PHOTO_SCOUT_GOOGLE_TILES_API_KEY') or os.environ['PHOTO_SCOUT_GOOGLE_API_KEY']
    identity = hashlib.sha256(key.encode()).hexdigest()
    cache_key = (identity, pano, tile_zoom())
    now = time.time()
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
        _panoramas[cache_key] = (result[1]['_tileExpiresAt'], result)
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
    size = 512
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
    draw.rectangle((0, size-20, size, size), fill=(30, 30, 30))
    draw.text((3, size-18), label[:75], fill='white', font_size=13)
    out = io.BytesIO()
    image.save(out, format='JPEG', quality=80, optimize=True)
    return 'data:image/jpeg;base64,' + base64.b64encode(out.getvalue()).decode()


async def image_data(pano, heading, pitch, fov):
    image, meta = await panorama(pano)
    return await asyncio.to_thread(project, image, meta, heading, pitch, fov)


async def panorama_payload(pano):
    image, meta = await panorama(pano)
    def encode():
        out = io.BytesIO()
        image.save(out, format='JPEG', quality=90, optimize=True)
        return 'data:image/jpeg;base64,' + base64.b64encode(out.getvalue()).decode()
    return {'image': await asyncio.to_thread(encode),
            'heading': meta.get('heading', 0), 'tilt': meta.get('tilt', 90),
            'roll': meta.get('roll', 0), 'copyright': meta.get('copyright', 'Google'),
            'expiresAt': meta['_tileExpiresAt']}
