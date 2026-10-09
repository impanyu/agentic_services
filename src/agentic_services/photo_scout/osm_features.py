"""Bounded OSM attribute queries; missing numeric attributes remain unknown."""
from __future__ import annotations
import asyncio,hashlib,json,math,re,sqlite3,time
from typing import Literal
import httpx
from pydantic import BaseModel,ConfigDict,Field,model_validator
from .sources import HEADERS,get_json,distance,text

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
    @model_validator(mode='after')
    def bounded(self):
        if self.kind=='tagged' and not any(f.required for f in self.filters):raise ValueError('At least one tag filter required')
        if self.kind=='intersection' and (self.filters or self.numericFilters):raise ValueError('Intersections use road topology')
        return self

def number(value,key):
    if not isinstance(value,str):return None
    match=re.fullmatch(r'\s*(-?\d+(?:\.\d+)?)\s*(m|meters?|ft|feet|\')?\s*',value)
    if not match:return None
    n=float(match[1]);unit=match[2]
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
            selectors=''.join('['+json.dumps(f.key,ensure_ascii=False)+('='+json.dumps(f.value,ensure_ascii=False) if f.value is not None else '')+']' for f in q.filters if f.required)
            parts.append(f'nwr{bbox}{selectors};out center tags 1200;')
    return '[out:json][timeout:8][maxsize:16777216];'+''.join(dict.fromkeys(parts))

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
            coord=e if e.get('type')=='node' else e.get('center',{})
            if not accepted or not all(isinstance(coord.get(k),(int,float)) and math.isfinite(coord[k]) for k in ('lat','lon')):continue
            d=distance((lat,lon),(coord['lat'],coord['lon']))
            if d>radius:continue
            ident=f"osm:{e['type']}:{e['id']}"
            if ident in seen:continue
            seen.add(ident)
            out.append({'id':ident,'name':text(tags.get('name') or q.label),'lat':coord['lat'],'lon':coord['lon'],'category':'map-feature','categoryGroups':[],
                'distanceMeters':round(d),'provider':'openstreetmap-features','sourceUrl':f"https://www.openstreetmap.org/{e['type']}/{e['id']}",
                'visuallyAnalyzed':False,'osmTags':tags,'unknownAttributes':unknown,'featureQueryIndex':i})
        groups.append(sorted(out,key=lambda p:(bool(p['unknownAttributes']),p['distanceMeters']))[:120])
    return groups

async def fetch_features(lat,lon,radius,queries,database_path):
    if not queries:return [],{'status':'not_requested','provider':'openstreetmap-features'}
    key=hashlib.sha256(json.dumps([lat,lon,radius,[q.model_dump() for q in queries]],sort_keys=True).encode()).hexdigest()
    with sqlite3.connect(database_path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS photo_osm_feature_cache (key TEXT PRIMARY KEY,data TEXT NOT NULL,expires REAL NOT NULL)')
        row=db.execute('SELECT data FROM photo_osm_feature_cache WHERE key=? AND expires>?',(key,time.time())).fetchone()
        if row:return json.loads(row[0]),{'status':'ok','provider':'openstreetmap-features','cached':True,'bounded':True}
    try:
        query=make_query(lat,lon,radius,queries);data=None
        async with asyncio.timeout(10):
            async with httpx.AsyncClient(timeout=4.5,headers=HEADERS,follow_redirects=False) as client:
                for endpoint in ('https://overpass-api.de/api/interpreter','https://overpass.private.coffee/api/interpreter'):
                    try:
                        candidate=await get_json(client,endpoint,{'data':query},method='POST',max_bytes=16_000_000)
                        if candidate.get('remark') or not isinstance(candidate.get('elements'),list):raise ValueError('Incomplete OSM response')
                        data=candidate;break
                    except (httpx.HTTPError,ValueError):continue
        if data is None:raise ValueError('OSM unavailable')
        groups=decode(data,queries,lat,lon,radius)
        with sqlite3.connect(database_path) as db:
            db.execute('DELETE FROM photo_osm_feature_cache WHERE expires<?',(time.time(),))
            db.execute('INSERT OR REPLACE INTO photo_osm_feature_cache VALUES(?,?,?)',(key,json.dumps(groups),time.time()+86400))
        return groups,{'status':'ok','provider':'openstreetmap-features','cached':False,'bounded':True,'groupCounts':[len(g) for g in groups]}
    except (httpx.HTTPError,ValueError,TimeoutError):
        return [],{'status':'unavailable','provider':'openstreetmap-features'}

def matches_features(place,groups,queries):
    # Multiple feature requirements are AND, while matches within each group are OR.
    return all(any(distance((place['lat'],place['lon']),(p['lat'],p['lon']))<=q.proximityMeters for p in group) for group,q in zip(groups,queries)) and len(groups)==len(queries)
