"""Real road routes and bounded, corridor-aware photographic discovery.

Routing, retrieval and scoring are separate: no invented straight-line routes,
no scores for uninspected views, and one global scoring budget per journey.
"""
from __future__ import annotations
import asyncio
import math
import os
from itertools import zip_longest
from typing import Literal
import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator
from fastapi import HTTPException
from .sources import distance, MAX_SCORED_IMAGES, diverse_sample

class RouteEndpoint(BaseModel):
    model_config = ConfigDict(extra='forbid')
    query: str | None = Field(default=None,min_length=1,max_length=200)
    lat: float | None = Field(default=None,ge=-85,le=85,allow_inf_nan=False)
    lon: float | None = Field(default=None,ge=-180,le=180,allow_inf_nan=False)
    label: str | None = Field(default=None,max_length=350)

    @model_validator(mode='after')
    def endpoint(self):
        if (self.lat is None)!=(self.lon is None):raise ValueError('Provide both endpoint coordinates')
        if not (self.query and self.query.strip()) and self.lat is None:raise ValueError('Provide an address or coordinates')
        return self

class RouteRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    origin: RouteEndpoint | None = None
    destination: RouteEndpoint
    travelMode: Literal['walk','drive'] = 'walk'
    corridorMeters: int = Field(default=50,ge=50,le=2000,description='Maximum distance from the route, not the circle search radius.')

async def resolve_endpoints(route,lat,lon,geocode):
    async def resolve(endpoint):
        if endpoint is None:return RouteEndpoint(lat=lat,lon=lon,label='Selected map location')
        # Named addresses are authoritative over any stale UI coordinates.
        if endpoint.query:
            rows=await geocode(endpoint.query)
            if not rows:raise HTTPException(422,'Could not locate a route endpoint. Include its city or address.')
            row=rows[0]
            return RouteEndpoint(lat=row['lat'],lon=row['lon'],label=row['label'])
        return endpoint.model_copy(update={'label':endpoint.label or 'Selected location'})
    start,end=await asyncio.gather(resolve(route.origin),resolve(route.destination))
    if distance((start.lat,start.lon),(end.lat,end.lon))<10:
        raise HTTPException(422,'Choose different route start and end points.')
    if distance((start.lat,start.lon),(end.lat,end.lon))>200000:
        raise HTTPException(422,'Route preview supports journeys up to 200 km. Try a shorter section.')
    return route.model_copy(update={'origin':start,'destination':end})

def decode_polyline(encoded):
    if not isinstance(encoded,str) or not encoded or len(encoded)>150000:
        raise ValueError('Invalid route polyline')
    position=0;last=[0,0];points=[]
    while position<len(encoded):
        for axis in range(2):
            shift=0;value=0
            while True:
                if position>=len(encoded) or shift>30:raise ValueError('Truncated route polyline')
                byte=ord(encoded[position])-63;position+=1
                if not 0<=byte<=63:raise ValueError('Invalid route polyline')
                value|=(byte&31)<<shift;shift+=5
                if byte<32:break
            last[axis]+=~(value>>1) if value&1 else value>>1
        lat,lon=last[0]/1e5,last[1]/1e5
        if abs(lat)>85 or abs(lon)>180:raise ValueError('Invalid route coordinates')
        points.append((lat,lon))
        if len(points)>20000:raise ValueError('Route geometry too large')
    if len(points)<2:raise ValueError('Route geometry too short')
    return points

async def compute_route(route):
    from .costs import record
    key=os.getenv('PHOTO_SCOUT_GOOGLE_ROUTES_API_KEY') or os.getenv('PHOTO_SCOUT_GOOGLE_PLACES_API_KEY') or os.getenv('PHOTO_SCOUT_GOOGLE_API_KEY')
    if not key:raise HTTPException(503,'Route provider is not configured.')
    waypoint=lambda p:{'location':{'latLng':{'latitude':p.lat,'longitude':p.lon}}}
    try:
        async with httpx.AsyncClient(timeout=25,follow_redirects=False) as client:
            record('routes-essentials', status='attempted')
            r=await client.post('https://routes.googleapis.com/directions/v2:computeRoutes',
                headers={'X-Goog-Api-Key':key,'X-Goog-FieldMask':'routes.distanceMeters,routes.duration,routes.polyline.encodedPolyline,routes.warnings'},
                json={'origin':waypoint(route.origin),'destination':waypoint(route.destination),
                      'travelMode':'WALK' if route.travelMode=='walk' else 'DRIVE','polylineQuality':'HIGH_QUALITY'})
            r.raise_for_status();routes=r.json().get('routes',[])
        if not routes:raise HTTPException(422,'No route was found for that travel mode. Try different endpoints or driving.')
        row=routes[0];points=decode_polyline(row['polyline']['encodedPolyline'])
        length=sum(distance(a,b) for a,b in zip(points,points[1:]))
        if length>200000:raise HTTPException(422,'This route is over 200 km. Search a shorter section.')
        return {'geometry':{'type':'LineString','coordinates':[[lon,lat] for lat,lon in points]},
                'origin':route.origin.model_dump(),'destination':route.destination.model_dump(),
                'travelMode':route.travelMode,'corridorMeters':route.corridorMeters,
                'distanceMeters':row.get('distanceMeters',round(length)),'duration':row.get('duration'),
                'warnings':row.get('warnings',[]),'provider':'google-routes','attribution':'Google Maps',
                'coverage':'Bounded samples along the computed route, not every view or a guarantee of safe access.'}
    except HTTPException:raise
    except (httpx.HTTPError,ValueError,KeyError,TypeError) as error:
        # Never include provider URLs/credentials or response bodies in user errors.
        raise HTTPException(503,'Route planning is temporarily unavailable. Please try again later.') from error

def route_points(route):
    return [(lat,lon) for lon,lat in route['geometry']['coordinates']]

def sample_route(points,count):
    lengths=[distance(a,b) for a,b in zip(points,points[1:])];total=sum(lengths)
    if not total:return [points[0]]
    result=[];segment=0;offset=0
    for i in range(count):
        target=total*i/(count-1)
        while segment<len(lengths)-1 and offset+lengths[segment]<target:
            offset+=lengths[segment];segment+=1
        factor=(target-offset)/max(.001,lengths[segment]);a,b=points[segment:segment+2]
        result.append((a[0]+(b[0]-a[0])*factor,((a[1]+(((b[1]-a[1]+180)%360)-180)*factor+180)%360)-180))
    return result

def along_route(point,points):
    """Return nearest offset and progress in meters; wrap the date line safely."""
    best=(float('inf'),0);progress=0;lat,lon=point
    scale=111320*math.cos(math.radians(lat))
    for a,b in zip(points,points[1:]):
        ax=((a[1]-lon+180)%360-180)*scale;ay=(a[0]-lat)*111320
        bx=ax+((b[1]-a[1]+180)%360-180)*scale;by=(b[0]-lat)*111320
        dx,dy=bx-ax,by-ay;t=max(0,min(1,-(ax*dx+ay*dy)/max(.001,dx*dx+dy*dy)))
        separation=math.hypot(ax+t*dx,ay+t*dy);length=distance(a,b)
        if separation<best[0]:best=(separation,progress+t*length)
        progress+=length
    return best

async def discover_route(payload,route,lookup,images):
    """Retrieve in parallel along the polyline, then impose a global 50-place cap."""
    points=route_points(route);corridor=route['corridorMeters'];length=route['distanceMeters']
    sections=max(1,min(6,math.ceil(length/4000)))
    anchors=sample_route(points,sections*2+1)[1::2]
    # Circles are only provider query windows. Exact corridor filtering follows.
    local_radius=min(20000,max(500,math.ceil(length/(2*sections)+corridor)))
    from .search import compile_search, SearchParameters
    from .styles import discovery_queries
    parameters=SearchParameters.model_validate(payload.model_dump(include=set(SearchParameters.model_fields)))
    strategy=compile_search(parameters).mergeStrategy
    required_retrieval=any(r.strength=='required' and r.route!='visual' for r in payload.requirements)
    if payload.searchProgram:
        required_retrieval=required_retrieval or any(s.tool=='search_places' and not s.discoveryHints for s in payload.searchProgram.steps)
    visual=strategy=='area-imagery' or not (payload.poiQueries or payload.geographicKinds or payload.osmFeatures or required_retrieval or payload.searchBranches)
    async def retrieve(anchor):
        local=payload.model_copy(update={'route':None,'lat':anchor[0],'lon':anchor[1],'radius':local_radius,
                                        'selectedPoiIds':None,'poiCatalogToken':None})
        pois,status=await lookup(local,include_geometry=True)
        if strategy=='area-imagery' and not required_retrieval:
            # Area programs normally obtain mood hints inside the image collector.
            # Retrieve those hints here so the entire journey shares one 50-target budget.
            hints=local.model_copy(update={'searchProgram':None,'searchBranches':[],
                'poiQueries':discovery_queries([],payload.photoStyles),'photoStyles':None})
            try:
                extra,hint_status=await lookup(hints,include_geometry=True)
                pois=list({p['id']:p for p in [*pois,*extra]}.values())
                status['moodHints']={k:v for k,v in hint_status.items() if not k.startswith('_')}
            except HTTPException:pass
        return local,pois,status
    retrieved=await asyncio.gather(*(retrieve(a) for a in anchors),return_exceptions=True)
    groups=[];source_status=[];seen=set()
    for value in retrieved:
        if isinstance(value,Exception):continue
        local,pois,status=value;group=[];source_status.append(status)
        for poi in pois:
            offset,progress=along_route((poi['lat'],poi['lon']),points)
            if offset>corridor or poi['id'] in seen:continue
            seen.add(poi['id']);group.append({**poi,'routeOffsetMeters':round(offset),'routeProgressMeters':round(progress)})
        groups.append(group)
    if not source_status:raise HTTPException(503,'Sources along this route are temporarily unavailable.')
    # Mandatory named/category/spatial searches must not be diluted by generic road sampling.
    road_count=20 if visual and not required_retrieval else 0
    selected=[p for batch in zip_longest(*groups) for p in batch if p][:50-road_count]
    if road_count:
        for i,(lat,lon) in enumerate(sample_route(points,road_count)):
            selected.append({'id':f'route:{lat:.6f}:{lon:.6f}','name':f'Route viewpoint {i+1}',
                'lat':lat,'lon':lon,'category':'photo-location','routeSample':True,
                'routeProgressMeters':round(length*i/(road_count-1))})
    # Assign each target to one window, never fetch all angles at every section.
    batches=[[] for _ in anchors]
    for poi in selected:
        index=min(range(len(anchors)),key=lambda i:distance(anchors[i],(poi['lat'],poi['lon'])))
        batches[index].append(poi)
    async def fetch(i,targets):
        rows,statuses=await images(anchors[i][0],anchors[i][1],local_radius,targets)
        # Preserve each local program's geographic/feature/branch filter.
        values=retrieved[i]
        execution=values[2].get('_programExecution') if not isinstance(values,Exception) else None
        if execution is not None:
            road_views=[r for r in rows if r.get('poi',{}).get('routeSample')] if visual and not required_retrieval else []
            rows=list({r['id']:r for r in [*execution.filter_images(rows),*road_views]}.values())
        if not isinstance(values,Exception):
            from .search import filter_branch_images
            from .geography import filter_places
            state=values[2]
            if state.get('_branchContexts'):rows=filter_branch_images(rows,state['_branchContexts'])
            if payload.geographicKinds:
                rows=filter_places(rows,state.get('_features',[]),payload.geographicKinds,
                    anchors[i][0],anchors[i][1],combination=payload.geographicCombination)
        for row in rows:
            if visual:row['allowUnlistedPlace']=True
            if row.get('poi',{}).get('routeSample'):
                row.pop('poi',None);row.pop('poiCandidates',None);row['allowUnlistedPlace']=True
        return rows,statuses
    fetched=await asyncio.gather(*(fetch(i,p) for i,p in enumerate(batches) if p),return_exceptions=True)
    statuses={};unique={}
    for value in fetched:
        if isinstance(value,Exception):continue
        rows,states=value
        for name,state in states.items():
            prior=statuses.setdefault(name,{'status':'unavailable','eligibleImages':0,'sampledImages':0,'queriedLocations':0})
            if state.get('status')=='ok':prior['status']='ok'
            for key in ('eligibleImages','queriedLocations'):prior[key]+=state.get(key,0) or 0
        for row in rows:
            offset,progress=along_route((row['lat'],row['lon']),points)
            if offset<=corridor:
                if row['id'] in unique:
                    prior=unique[row['id']]
                    possible=prior.get('poiCandidates',[])+row.get('poiCandidates',[])
                    if possible:prior['poiCandidates']=list({p['id']:p for p in possible}.values())
                else:unique[row['id']]={**row,'routeOffsetMeters':round(offset),'routeProgressMeters':round(progress)}
    if selected and not any(s.get('status')=='ok' for s in statuses.values()):
        raise HTTPException(503,'Image sources along this route are temporarily unavailable.')
    google=[r for r in unique.values() if r['provider']=='google-street-view']
    others=diverse_sample([r for r in unique.values() if r['provider']!='google-street-view'],24)
    rows=(google+others)[:MAX_SCORED_IMAGES]
    for name,state in statuses.items():state['sampledImages']=sum(r['provider']==name for r in rows)
    statuses['google-routes']={'successfulSections':len(source_status),'failedSections':sections-len(source_status),'status':'ok','distanceMeters':length,'travelMode':route['travelMode'],
        'sections':sections,'queriedLocations':len(selected),'corridorMeters':corridor}
    route.update(sampledLocations=len(selected),samplingSpacingMeters=round(length/max(1,road_count-1)) if road_count else None)
    return rows,statuses,[p for p in selected if not p.get('routeSample')]
