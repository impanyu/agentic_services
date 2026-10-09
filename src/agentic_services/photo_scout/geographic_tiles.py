"""Cached OSM vector geometry fallback when live Overpass is unavailable.

Map geometry is generalized, not an exhaustive raw OSM inventory. Adjacent
polygons are stitched before shore extraction, avoiding artificial tile edges.
"""
import asyncio
import hashlib
import json
import math
import sqlite3
import time

import httpx
import mapbox_vector_tile
import numpy as np
from shapely import transform, set_precision
from shapely.geometry import shape, mapping
from shapely.ops import unary_union
from shapely.validation import make_valid
from .sources import HEADERS, get_json, text

MANIFEST='https://tiles.openfreemap.org/planet'
_manifest=None


def tile_bounds(lat,lon,radius):
    # Extra border keeps the requested region well inside downloaded geometry,
    # so clipped outer tile edges cannot masquerade as nearby shores.
    dy=(radius+1500)/111320;dx=dy/max(.01,math.cos(math.radians(lat)))
    if lon-dx < -180 or lon+dx > 180:raise ValueError('Dateline region')
    for z in range(14,7,-1):
        n=2**z
        def xy(y,x):return int((x+180)/360*n),int((1-math.asinh(math.tan(math.radians(max(-85,min(85,y)))))/math.pi)/2*n)
        x1,y1=xy(lat+dy,lon-dx);x2,y2=xy(lat-dy,lon+dx)
        coords=[(z,x,y) for x in range(max(0,x1),min(n-1,x2)+1) for y in range(max(0,y1),min(n-1,y2)+1)]
        if len(coords)<=16:return coords
    raise ValueError('Excessive tile region')


def decode_tile(blob,z,x,y):
    layers=mapbox_vector_tile.decode(blob)
    output=[]
    for name in ('water','waterway','landcover','mountain_peak','transportation','water_name'):
        layer=layers.get(name,{})
        extent=layer.get('extent',4096);n=2**z
        def coords(a,b):
            return (x+a/extent)/n*360-180,np.degrees(np.arctan(np.sinh(math.pi*(1-2*(y+1-b/extent)/n))))
        for f in layer.get('features',[]):
            props=f.get('properties',{});cls=props.get('class');sub=props.get('subclass')
            kinds=[]
            if name=='water':
                if cls=='lake':kinds=['lake','waterside']
                elif cls=='ocean':kinds=['sea','waterside']
                elif cls=='river':kinds=['river','waterside']
            elif name=='waterway' and cls in ('river','stream'):kinds=['river','waterside']
            elif name=='landcover':
                if cls=='wood':kinds=['forest']
                elif sub=='beach':kinds=['sea','waterside']
            elif name=='mountain_peak':kinds=['peak']
            elif name=='transportation':
                if cls not in ('minor','service','path','track','tertiary','secondary','primary') or props.get('access') in ('private','no') or props.get('foot')=='no':continue
            elif name!='water_name':continue
            if name not in ('transportation','water_name') and not kinds:continue
            g=make_valid(transform(shape(f['geometry']),coords,interleaved=False))
            if not g.is_empty:output.append({'layer':name,'properties':props,'kinds':kinds,'geometry':g})
    return output


def stitch(rows,kinds):
    groups={};paths=[];labels=[r for r in rows if r['layer'] in ('mountain_peak','waterway')]
    for row in rows:
        g=row['geometry'];p=row['properties']
        if row['layer']=='transportation':
            if g.geom_type not in ('LineString','MultiLineString'):continue
            paths.append({'id':'tile-path:'+hashlib.sha256(g.wkb).hexdigest()[:16],
                'name':text(p.get('name','')),'geometry':json.loads(json.dumps(mapping(g)))})
        elif row['layer']=='water_name':labels.append(row)
        else:
            relevant=tuple(k for k in row['kinds'] if k in kinds)
            if relevant:groups.setdefault(relevant,[]).append(set_precision(g,1e-7))
    features=[]
    for matched,parts in groups.items():
        joined=unary_union(parts)
        objects=list(joined.geoms) if joined.geom_type in ('MultiPolygon','MultiPoint','MultiLineString','GeometryCollection') else [joined]
        for g in objects:
            if g.is_empty:continue
            label=next((text(r['properties'].get('name:en') or r['properties'].get('name')) for r in labels if g.covers(r['geometry'])), '')
            center=g.representative_point()
            features.append({'id':'osm-vector:'+hashlib.sha256(g.wkb).hexdigest()[:20], 'name':label,
                'sourceUrl':f'https://www.openstreetmap.org/#map=16/{center.y:.6f}/{center.x:.6f}',
                'kinds':list(matched),'geometry':json.loads(json.dumps(mapping(g)))})
    return features,paths


async def fetch_tiles(lat,lon,radius,kinds,database_path):
    global _manifest
    coords=tile_bounds(lat,lon,radius)
    with sqlite3.connect(database_path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS photo_geo_tiles (url TEXT PRIMARY KEY, data BLOB NOT NULL, expires REAL NOT NULL)')
        db.execute('DELETE FROM photo_geo_tiles WHERE expires<?',(time.time(),))
    async with httpx.AsyncClient(timeout=4,headers=HEADERS,follow_redirects=False) as client:
        if not _manifest or _manifest[0]<time.time():
            data=await get_json(client,MANIFEST)
            template=data['tiles'][0]
            if not template.startswith('https://tiles.openfreemap.org/planet/') or not template.endswith('/{z}/{x}/{y}.pbf'):raise ValueError('Unexpected vector tile endpoint')
            _manifest=(time.time()+3600,template)
        template=_manifest[1];slots=asyncio.Semaphore(8);hits=[]
        async def load(coord):
            z,x,y=coord;url=template.replace('{z}',str(z)).replace('{x}',str(x)).replace('{y}',str(y))
            with sqlite3.connect(database_path) as db:
                saved=db.execute('SELECT data FROM photo_geo_tiles WHERE url=? AND expires>?',(url,time.time())).fetchone()
            if saved:blob=saved[0];hits.append(1)
            else:
                async with slots:
                    async with client.stream('GET',url) as response:
                        response.raise_for_status();data=bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data)>3_000_000:raise ValueError('Vector tile too large')
                        blob=bytes(data)
                with sqlite3.connect(database_path) as db:db.execute('INSERT OR REPLACE INTO photo_geo_tiles VALUES(?,?,?)',(url,blob,time.time()+604800))
            return await asyncio.to_thread(decode_tile,blob,z,x,y)
        batches=await asyncio.gather(*(load(c) for c in coords))
    features,paths=stitch([r for batch in batches for r in batch],kinds)
    return features,paths,{'status':'ok','provider':'openfreemap-osm-vector','geometryQuality':'generalized-map-geometry',
        'tiles':len(coords),'cachedTiles':len(hits),'features':len(features),'paths':len(paths),
        'attribution':'OpenFreeMap · OpenMapTiles · OpenStreetMap contributors', 'sourceUrl':'https://openfreemap.org/'}
