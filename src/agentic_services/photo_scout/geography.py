"""Geographic discovery: geometry -> accessible candidate positions, no agent loop."""
from __future__ import annotations
import asyncio
import hashlib
import json
import math
import sqlite3
import time
from typing import Literal

import httpx
from shapely.geometry import Point, LineString, Polygon, shape, mapping
from shapely.ops import polygonize, unary_union
from shapely import transform
from shapely.validation import make_valid

from .sources import HEADERS, get_json, distance, text

GeographicKind = Literal['lake', 'sea', 'river', 'peak', 'forest', 'waterside']
FILTERS = {
    'lake': '["natural"="water"]["water"~"^(lake|reservoir|pond)$"]',
    'sea': '["natural"~"^(coastline|beach)$"]',
    'river': '["waterway"~"^(river|stream)$"]',
    'peak': '["natural"="peak"]',
    'forest': '["natural"="wood"]',
    'waterside': '["natural"~"^(water|coastline|beach)$"]',
}
# Discovery proximity is only a hypothesis; the vision scorer verifies visibility.
PROXIMITY = {'lake': 150, 'sea': 150, 'river': 100, 'peak': 300, 'forest': 0, 'waterside': 150}
MAX_PLACES = 30

class Region:
    def __init__(self, lat, lon):
        self.lat, self.lon = lat, lon
        self.sx = 111320 * max(.01, math.cos(math.radians(lat)))
    def project(self, g):
        return transform(g,lambda x,y: ((x-self.lon)*self.sx,(y-self.lat)*111320),interleaved=False)
    def unproject(self, g):
        return transform(g,lambda x,y: (x/self.sx+self.lon,y/111320+self.lat),interleaved=False)

def geometry(element):
    """Reconstruct relation rings, preserving holes instead of bridging members."""
    if element['type'] == 'node':
        return Point(element['lon'], element['lat'])
    def line(coords):
        valid=[(p['lon'],p['lat']) for p in coords if 'lat' in p and 'lon' in p]
        return LineString(valid) if len(valid)>1 else None
    if element['type'] == 'way':
        g=line(element.get('geometry',[]))
        if g is not None and g.is_ring and len(g.coords)>=4 and not element.get('tags',{}).get('highway') and element.get('tags',{}).get('natural')!='coastline':
            return make_valid(Polygon(g))
        return g
    outer=[];inner=[]
    for member in element.get('members',[]):
        g=line(member.get('geometry',[]))
        if g is not None:
            (inner if member.get('role')=='inner' else outer).append(g)
    if not outer:return None
    rings=list(polygonize(unary_union(outer)))
    if not rings:return None  # Incomplete area: never invent containment or shoreline.
    area=unary_union(rings)
    holes=list(polygonize(unary_union(inner))) if inner else []
    return make_valid(area.difference(unary_union(holes))) if holes else make_valid(area)

def kind_matches(tags, kind):
    natural=tags.get('natural');water=tags.get('water')
    return {'lake':natural=='water' and water in ('lake','reservoir','pond',None),
            'sea':natural in ('coastline','beach'), 'river':tags.get('waterway') in ('river','stream'),
            'peak':natural=='peak', 'forest':natural=='wood' or tags.get('landuse')=='forest',
            'waterside':natural in ('water','coastline','beach') or tags.get('waterway') in ('river','stream')}[kind]

def decode(data, kinds):
    features=[];paths=[]
    for e in data.get('elements',[]):
        tags=e.get('tags',{})
        try:g=geometry(e)
        except (ValueError,KeyError,TypeError):continue
        if g is None or g.is_empty:continue
        source={'id':f"osm:{e['type']}:{e['id']}", 'name':text(tags.get('name','')),
                'sourceUrl':f"https://www.openstreetmap.org/{e['type']}/{e['id']}", 'geometry':json.loads(json.dumps(mapping(g)))}
        if tags.get('highway'):
            if tags.get('access') not in ('private','no') and tags.get('foot')!='no' and tags['highway'] not in ('motorway','motorway_link','trunk','trunk_link'):
                paths.append(source)
        else:
            matched=[k for k in kinds if kind_matches(tags,k)]
            if matched:features.append({**source,'kinds':matched})
    return features,paths

async def fetch_region(lat,lon,radius,kinds,database_path):
    """One bounded OSM request; successful geometry cached in SQLite for 24 hours."""
    if not kinds:return [],[],{'status':'not_requested','provider':'openstreetmap-geography'}
    key=hashlib.sha256(json.dumps([lat,lon,radius,sorted(set(kinds))]).encode()).hexdigest()
    with sqlite3.connect(database_path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS photo_geography_cache (key TEXT PRIMARY KEY, data TEXT NOT NULL, expires REAL NOT NULL)')
        saved=db.execute('SELECT data FROM photo_geography_cache WHERE key=? AND expires>?',(key,time.time())).fetchone()
        if saved:
            data=json.loads(saved[0]);features,paths=data[:2]
            status=data[2] if len(data)>2 else {'status':'ok','provider':'openstreetmap-geography','features':len(features),'paths':len(paths)}
            return features,paths,{**status,'cached':True}
    dy=(radius+350)/111320;dx=dy/max(.01,math.cos(math.radians(lat)))
    if lon-dx < -180 or lon+dx > 180:
        return [],[],{'status':'unavailable','reason':'dateline_region','provider':'openstreetmap-geography'}
    bbox=f'{max(-85,lat-dy)},{lon-dx},{min(85,lat+dy)},{lon+dx}'
    area=f'({bbox})'
    selectors=[]
    for kind in sorted(set(kinds)):
        selectors.append('nwr'+area+FILTERS[kind]+';')
        if kind=='lake':selectors.append('nwr'+area+'["natural"="water"][!"water"];')
        if kind=='forest':selectors.append('nwr'+area+'["landuse"="forest"];')
        if kind=='waterside':selectors.append('way'+area+'["waterway"~"^(river|stream)$"];')
    # Fetch paths only in feature proximity, rather than every road in a 20 km city.
    # around measures polygon-boundary/line proximity; forest paths need a separate
    # bounding region because paths can be well inside its polygon.
    feature_radius=300 if 'peak' in kinds else 160
    road_area=area if 'forest' in kinds else f'(around.features:{feature_radius})'
    query='[out:json][timeout:6][maxsize:16777216];('+''.join(selectors)+')->.features;.features out geom;way'+road_area+'["highway"~"^(residential|service|unclassified|tertiary|secondary|primary|living_street|footway|path|pedestrian|steps|cycleway|track)$"];out geom;'
    try:
        data=None
        async with asyncio.timeout(4):
            async with httpx.AsyncClient(timeout=3.5,headers=HEADERS,follow_redirects=False) as client:
                for endpoint in ('https://overpass.private.coffee/api/interpreter','https://maps.mail.ru/osm/tools/overpass/api/interpreter'):
                    try:
                        candidate=await get_json(client,endpoint,{'data':query},method='POST',max_bytes=16_000_000)
                        if candidate.get('remark') or not isinstance(candidate.get('elements'),list):raise ValueError('Incomplete geometry')
                        data=candidate;break
                    except (httpx.HTTPError,ValueError):continue
        if data is None:raise ValueError('Geographic sources unavailable')
        features,paths=decode(data,kinds)
        with sqlite3.connect(database_path) as db:
            db.execute('DELETE FROM photo_geography_cache WHERE expires<?',(time.time(),))
            db.execute('INSERT OR REPLACE INTO photo_geography_cache VALUES(?,?,?)',(key,json.dumps([features,paths]),time.time()+86400))
        return features,paths,{'status':'ok','provider':'openstreetmap-geography','cached':False,'features':len(features),'paths':len(paths)}
    except (httpx.HTTPError,ValueError,TimeoutError):
        from .geographic_tiles import fetch_tiles
        try:
            async with asyncio.timeout(5):
                features,paths,status=await fetch_tiles(lat,lon,radius,kinds,database_path)
            # Keep fallback provenance with geometry in the same cache.
            with sqlite3.connect(database_path) as db:
                db.execute('INSERT OR REPLACE INTO photo_geography_cache VALUES(?,?,?)',(key,json.dumps([features,paths,status]),time.time()+86400))
            return features,paths,status
        except (httpx.HTTPError,ValueError,TimeoutError,KeyError):
            return [],[],{'status':'unavailable','provider':'openstreetmap-geography'}

def areas(features,kinds,region):
    by_kind={k:[] for k in kinds}
    for f in features:
        g=region.project(shape(f['geometry']))
        for kind in f['kinds']:
            if kind not in by_kind:continue
            # Lake shore, not lake interior or the polygon's center.
            target=g.boundary if kind in ('lake','sea','river','waterside') and g.geom_type in ('Polygon','MultiPolygon') else g
            by_kind[kind].append(target.buffer(PROXIMITY[kind]) if PROXIMITY[kind] else target)
    return {k:unary_union(v) for k,v in by_kind.items()}

def matches_position(lat,lon,features,kinds,region=None):
    region=region or Region(lat,lon)
    p=region.project(Point(lon,lat))
    return all(g.covers(p) for g in areas(features,kinds,region).values())

def filter_places(pois,features,kinds,lat,lon):
    region=Region(lat,lon);constraints=areas(features,kinds,region)
    return [p for p in pois if all(g.covers(region.project(Point(p['lon'],p['lat']))) for g in constraints.values())]

def geographic_places(lat,lon,radius,features,paths,kinds,limit=MAX_PLACES):
    """Sample feature-adjacent paths, then distribute candidates spatially."""
    region=Region(lat,lon);constraints=areas(features,kinds,region)
    if not constraints or any(g.is_empty for g in constraints.values()):return []
    allowed=Point(0,0).buffer(radius)
    for g in constraints.values():allowed=allowed.intersection(g)
    choices=[];cells=set()
    def collect(line,path):
        if line.is_empty:return
        if line.geom_type=='LineString':
            # Candidate spacing is independent of total feature size; cap work per path.
            count=min(80,max(1,math.ceil(line.length/150)))
            for i in range(count+1):
                p=line.interpolate(i/count,normalized=True)
                cell=(round(p.x/70),round(p.y/70))
                if cell not in cells and len(choices)<1200:
                    cells.add(cell);choices.append((p,path))
        elif hasattr(line,'geoms'):
            for part in line.geoms:collect(part,path)
    for path in paths:
        collect(region.project(shape(path['geometry'])).intersection(allowed),path)
        if len(choices)>=1200:break
    choices=[(p,path,p.x,p.y) for p,path in choices]
    selected=[]
    while choices and len(selected)<limit:
        item=min(choices,key=lambda x:x[2]**2+x[3]**2) if not selected else max(choices,key=lambda x:min((x[2]-s[2])**2+(x[3]-s[3])**2 for s in selected))
        choices.remove(item);selected.append(item)
    result=[]
    for point,path,_,_ in selected:
        position=region.unproject(point);lon2,lat2=position.x,position.y
        feature=min(features,key=lambda f:region.project(shape(f['geometry'])).distance(point))
        name=feature['name'] or {'lake':'Lakeside','sea':'Seaside','river':'Riverside','peak':'Near summit','forest':'Forest','waterside':'Waterside'}[kinds[0]]
        result.append({'id':f"geo:{feature['id']}:{lat2:.5f}:{lon2:.5f}",'name':name+(f" · {path['name']}" if path['name'] else ' viewpoint'),
            'lat':lat2,'lon':lon2,'provider':'openstreetmap-geography','category':'photo-location','categoryGroups':[],
            'sourceUrl':feature['sourceUrl'],'distanceMeters':round(distance((lat,lon),(lat2,lon2))),
            'attribution':'OpenStreetMap contributors','license':'ODbL 1.0','geographicKinds':kinds,'visuallyAnalyzed':False,
            'accessNote':'Mapped road/path; current public access and safe standing point are unverified.'})
    return result

def merge_places(named,geographic,limit=MAX_PLACES):
    from itertools import zip_longest
    result=[]
    for group in zip_longest(named,geographic):
        for p in group:
            if p and not any(p['id']==q['id'] or distance((p['lat'],p['lon']),(q['lat'],q['lon']))<50 for q in result):result.append(p)
            if len(result)>=limit:return result
    return result
