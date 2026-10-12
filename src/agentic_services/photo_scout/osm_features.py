"""Bounded OSM attribute queries; missing numeric attributes remain unknown."""
from __future__ import annotations
import asyncio,hashlib,json,math,re,sqlite3,time
from typing import Literal
import httpx
from pydantic import BaseModel,ConfigDict,Field,model_validator
from .sources import HEADERS,get_json,distance,text
from .spatial_sampling import spread_points

class TagFilter(BaseModel):
    model_config=ConfigDict(extra='forbid')
    key:str=Field(min_length=1,max_length=80,pattern=r'^[A-Za-z0-9_:.-]+$')
    value:str|None=Field(default=None,max_length=160)
    required:bool=True

class NumericFilter(BaseModel):
    model_config=ConfigDict(extra='forbid')
    key:str=Field(min_length=1,max_length=80,pattern=r'^[A-Za-z0-9_:.-]+$')
    minimum:float|None=Field(default=None,allow_inf_nan=False)
    maximum:float|None=Field(default=None,allow_inf_nan=False)
    @model_validator(mode='after')
    def bounds(self):
        if self.minimum is None and self.maximum is None:raise ValueError('Numeric bounds required')
        if self.minimum is not None and self.maximum is not None and self.minimum>self.maximum:raise ValueError('Invalid numeric bounds')
        return self

class OSMFeatureQuery(BaseModel):
    model_config=ConfigDict(extra='forbid')
    label:str=Field(min_length=1,max_length=120)
    kind:Literal['tagged','intersection']='tagged'
    filters:list[TagFilter]=Field(default_factory=list,max_length=6)
    numericFilters:list[NumericFilter]=Field(default_factory=list,max_length=4)
    proximityMeters:int=Field(default=150,ge=0,le=500)
    selectionHints:list[Literal['large-footprint','small-footprint','more-storeys']]=Field(default_factory=list,max_length=3,
        description='Soft candidate priorities only; never hard constraints. Building footprint/levels do not establish visual style or luxury.')
    @model_validator(mode='after')
    def bounded(self):
        if self.kind=='tagged' and not any(f.required for f in self.filters):raise ValueError('At least one tag filter required')
        if self.kind=='intersection' and (self.filters or self.numericFilters):raise ValueError('Intersections use road topology')
        if self.selectionHints and not any(f.required and f.key=='building' for f in self.filters):raise ValueError('Building selection hints require a building filter')
        return self

def number(value,key):
    if not isinstance(value,str):return None
    match=re.fullmatch(r'\s*(-?\d+(?:\.\d+)?)\s*(m|meters?|ft|feet|\')?\s*',value)
    if not match:return None
    n=float(match[1]);unit=match[2]
    if not math.isfinite(n):return None
    if unit and key not in ('height','min_height','width'):return None
    return n*.3048 if unit in ('ft','feet',"'") else n

def numeric_match(tags,filters):
    unknown=[]
    for f in filters:
        n=number(tags.get(f.key),f.key)
        if n is None:unknown.append(f.key);continue
        if f.minimum is not None and n<f.minimum:return False,unknown
        if f.maximum is not None and n>f.maximum:return False,unknown
    return True,unknown

def make_query(lat,lon,radius,queries):
    dy=radius/111320;dx=dy/max(.01,math.cos(math.radians(lat)))
    if lon-dx < -180 or lon+dx > 180:raise ValueError('Dateline region unsupported')
    bbox=f'({max(-85,lat-dy)},{lon-dx},{min(85,lat+dy)},{lon+dx})'
    parts=[]
    for i,q in enumerate(queries):
        if q.kind=='intersection':
            parts.append(f'way{bbox}["highway"~"^(residential|service|unclassified|tertiary|secondary|primary|living_street|pedestrian)$"];out geom 1800;')
        else:
            output='out center tags'+(' geom' if any(h in q.selectionHints for h in ('large-footprint','small-footprint')) else '')
            selectors=''.join('['+json.dumps(f.key,ensure_ascii=False)+('='+json.dumps(f.value,ensure_ascii=False) if f.value is not None else '')+']' for f in q.filters if f.required)
            dense = radius >= 1000 and any(f.required and f.key == 'building' for f in q.filters)
            tiles = 4 if radius > 5000 else 2
            if dense:
                # Bound the total output as before, but request a share from each
                # cell instead of accepting only the first OSM IDs in a city.
                for y in range(tiles):
                    for x in range(tiles):
                        south=max(-85,lat-dy+2*dy*y/tiles);north=min(85,lat-dy+2*dy*(y+1)/tiles)
                        west=lon-dx+2*dx*x/tiles;east=lon-dx+2*dx*(x+1)/tiles
                        tile=f'({south},{west},{north},{east})'
                        parts.append(f'nwr{tile}{selectors};{output} {1200//(tiles*tiles)};')
            else:
                parts.append(f'nwr{bbox}{selectors};{output} 1200;')
    return '[out:json][timeout:8][maxsize:16777216];'+''.join(dict.fromkeys(parts))

def footprint_area(element):
    geometry=element.get('geometry',[])
    if element.get('type')!='way' or len(geometry)<3:return None
    if any(not all(isinstance(p.get(k),(int,float)) and math.isfinite(p[k]) for k in ('lat','lon')) for p in geometry):return None
    lat0=geometry[0]['lat'];lon0=geometry[0]['lon']
    points=[((p['lon']-lon0)*111320*math.cos(math.radians(lat0)),(p['lat']-lat0)*111320) for p in geometry]
    area=abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(points,points[1:]+points[:1])))/2
    return round(area,1) if math.isfinite(area) and area>0 else None

def element_position(element):
    if element.get('type')=='node':return element
    if element.get('center'):return element['center']
    # Geometric Overpass output can omit center. Derive its bounding-box
    # center instead of discarding otherwise usable mapped buildings.
    points=list(element.get('geometry',[]))
    for member in element.get('members',[]):
        points.extend(member.get('geometry',[]))
        if member.get('type')=='node':points.append(member)
    points=[p for p in points if all(isinstance(p.get(k),(int,float)) and math.isfinite(p[k]) for k in ('lat','lon'))]
    if not points:return {}
    origin=points[0]['lon'];longitudes=[origin+(p['lon']-origin+180)%360-180 for p in points]
    return {'lat':(min(p['lat'] for p in points)+max(p['lat'] for p in points))/2,
            'lon':((min(longitudes)+max(longitudes))/2+180)%360-180}

def prioritize(rows,query):
    if not query.selectionHints:return rows
    for row in rows:
        area=row.get('footprintAreaMeters2');levels=number(row.get('osmTags',{}).get('building:levels'),'building:levels')
        rank=[]
        for hint in query.selectionHints:
            if hint in ('large-footprint','small-footprint'):
                rank.append((0,-area if hint=='large-footprint' else area) if area else (1,0))
            else:rank.append((0,-levels) if levels is not None else (1,0))
        row['selectionPriority']=rank
    return rows

def signature(queries,*,hints=True):
    # Empty new fields must not invalidate existing exact-query caches.
    return [q.model_dump(exclude={'selectionHints'} if not hints or not q.selectionHints else set()) for q in queries]

def decode(data,queries,lat,lon,radius):
    elements=data.get('elements',[]);groups=[]
    for i,q in enumerate(queries):
        rows=[]
        if q.kind=='intersection':
            nodes={}
            for e in elements:
                if e.get('type')!='way' or e.get('tags',{}).get('highway') not in ('residential','service','unclassified','tertiary','secondary','primary','living_street','pedestrian'):continue
                ids=e.get('nodes',[]);coords=e.get('geometry',[])
                for j,(ref,coord) in enumerate(zip(ids,coords)):
                    if 'lat' not in coord or 'lon' not in coord:continue
                    entry=nodes.setdefault(ref,{'lat':coord['lat'],'lon':coord['lon'],'neighbors':set()})
                    if j:entry['neighbors'].add(ids[j-1])
                    if j+1<len(ids):entry['neighbors'].add(ids[j+1])
            rows=[{'type':'node','id':ref,'lat':p['lat'],'lon':p['lon'],'tags':{}} for ref,p in nodes.items() if len(p['neighbors'])>=3]
        else:
            rows=[e for e in elements if all(f.key in e.get('tags',{}) and (f.value is None or e['tags'][f.key]==f.value) for f in q.filters if f.required)]
        out=[];seen=set()
        for e in rows:
            tags=e.get('tags',{});accepted,unknown=numeric_match(tags,q.numericFilters)
            for f in q.filters:
                if f.key not in tags and not f.required:unknown.append(f.key)
                elif f.value is not None and tags.get(f.key)!=f.value:accepted=False
            coord=element_position(e)
            if not accepted or not all(isinstance(coord.get(k),(int,float)) and math.isfinite(coord[k]) for k in ('lat','lon')):continue
            d=distance((lat,lon),(coord['lat'],coord['lon']))
            if d>radius:continue
            ident=f"osm:{e['type']}:{e['id']}"
            if ident in seen:continue
            seen.add(ident)
            out.append({'id':ident,'name':text(tags.get('name') or q.label),'lat':coord['lat'],'lon':coord['lon'],'category':'map-feature','categoryGroups':[],
                'distanceMeters':round(d),'provider':'openstreetmap-features','sourceUrl':f"https://www.openstreetmap.org/{e['type']}/{e['id']}",
                'visuallyAnalyzed':False,'osmTags':tags,'unknownAttributes':unknown,'featureQueryIndex':i,
                **({'footprintAreaMeters2':footprint_area(e)} if q.selectionHints else {})})
        groups.append(spread_points(prioritize(out,q),lat,lon,radius)[:120])
    return groups

async def fetch_features(lat,lon,radius,queries,database_path):
    if not queries:return [],{'status':'not_requested','provider':'openstreetmap-features'}
    version='geometry-v3' if any(set(q.selectionHints)&{'large-footprint','small-footprint'} for q in queries) else 'spatial-v2'
    key=hashlib.sha256(json.dumps([version,lat,lon,radius,signature(queries)],sort_keys=True).encode()).hexdigest()
    legacy_key=hashlib.sha256(json.dumps([lat,lon,radius,signature(queries,hints=False)],sort_keys=True).encode()).hexdigest()
    legacy=None
    with sqlite3.connect(database_path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS photo_osm_feature_cache (key TEXT PRIMARY KEY,data TEXT NOT NULL,expires REAL NOT NULL)')
        row=db.execute('SELECT data FROM photo_osm_feature_cache WHERE key=? AND expires>?',(key,time.time())).fetchone()
        if row:return json.loads(row[0]),{'status':'ok','provider':'openstreetmap-features','cached':True,'bounded':True}
        fallback_keys=[legacy_key]
        if any(q.selectionHints for q in queries):
            fallback_keys.insert(0,hashlib.sha256(json.dumps(['spatial-v2',lat,lon,radius,signature(queries,hints=False)],sort_keys=True).encode()).hexdigest())
        older=next((row for fallback in fallback_keys if (row:=db.execute('SELECT data FROM photo_osm_feature_cache WHERE key=? AND expires>?',(fallback,time.time())).fetchone())),None)
        if older:legacy=[prioritize(group,q) for group,q in zip(json.loads(older[0]),queries)]
    failures=[]
    try:
        query=make_query(lat,lon,radius,queries);data=None
        async with asyncio.timeout(10):
            async with httpx.AsyncClient(timeout=4.5,headers=HEADERS,follow_redirects=False) as client:
                for endpoint in ('https://overpass-api.de/api/interpreter','https://overpass.private.coffee/api/interpreter'):
                    try:
                        candidate=await get_json(client,endpoint,{'data':query},method='POST',max_bytes=16_000_000)
                        if candidate.get('remark') or not isinstance(candidate.get('elements'),list):raise ValueError('Incomplete OSM response')
                        data=candidate;break
                    except (httpx.HTTPError,ValueError) as error:
                        from urllib.parse import urlsplit
                        failures.append({'host':urlsplit(endpoint).hostname,'errorType':type(error).__name__,
                            **({'httpStatus':error.response.status_code} if isinstance(error,httpx.HTTPStatusError) else {})})
                        continue
        if data is None:raise ValueError('OSM unavailable')
        groups=decode(data,queries,lat,lon,radius)
        with sqlite3.connect(database_path) as db:
            db.execute('DELETE FROM photo_osm_feature_cache WHERE expires<?',(time.time(),))
            db.execute('INSERT OR REPLACE INTO photo_osm_feature_cache VALUES(?,?,?)',(key,json.dumps(groups),time.time()+86400))
        return groups,{'status':'ok','provider':'openstreetmap-features','cached':False,'bounded':True,'groupCounts':[len(g) for g in groups]}
    except (httpx.HTTPError,ValueError,TimeoutError):
        if legacy is not None:
            return legacy,{'status':'ok','provider':'openstreetmap-features','cached':True,'bounded':True,'spatialFallback':'legacy_cache','groupCounts':[len(g) for g in legacy]}
        return [],{'status':'unavailable','provider':'openstreetmap-features','attempts':failures}

def matches_features(place,groups,queries,combination="all"):
    # Multiple feature requirements are AND, while matches within each group are OR.
    return (any if combination=="any" else all)(any(distance((place['lat'],place['lon']),(p['lat'],p['lon']))<=q.proximityMeters for p in group) for group,q in zip(groups,queries)) and len(groups)==len(queries)
