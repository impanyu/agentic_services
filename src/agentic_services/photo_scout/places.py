"""Free-text Google Places discovery, bounded to the selected search region."""
import asyncio
import math
import os
import httpx

from .sources import distance, text
from .costs import reserve_places_request, record, reuse_request

FIELDS='places.id,places.displayName,places.location,places.primaryType,places.googleMapsUri,places.attributions,nextPageToken'

async def search_text(query, *, center=None, radius=None, limit=20):
    # Sharing is scoped to one task, including parallel branches and geocoding.
    key=('places',' '.join(query.split()).casefold(),center,radius,limit)
    return await reuse_request(key,lambda:_search_text(query,center=center,radius=radius,limit=limit))

async def _search_text(query, *, center=None, radius=None, limit=20):
    key=os.getenv('PHOTO_SCOUT_GOOGLE_PLACES_API_KEY') or os.getenv('PHOTO_SCOUT_GOOGLE_API_KEY')
    if not key:raise ValueError('Google Places credential unavailable')
    limit=max(1,min(60,limit))
    body={'textQuery':query,'pageSize':min(20,limit),'languageCode':'en'}
    if center is not None and radius is not None:
        lat,lon=center;dy=radius/111320;dx=dy/max(.01,math.cos(math.radians(lat)))
        west,east=lon-dx,lon+dx
        if west>=-180 and east<=180:
            body['locationRestriction']={'rectangle':{
                'low':{'latitude':max(-90,lat-dy),'longitude':west},
                'high':{'latitude':min(90,lat+dy),'longitude':east}}}
        else:
            body['locationBias']={'circle':{'center':{'latitude':lat,'longitude':lon},'radius':radius}}
    async with httpx.AsyncClient(timeout=25,follow_redirects=False) as client:
        rows=[];seen_tokens=set()
        for _ in range(math.ceil(limit/20)):
            try:
                data=await _page(client,body,key)
            except (httpx.HTTPError,ValueError):
                if rows:break
                raise
            rows.extend(data.get('places',[]))
            token=data.get('nextPageToken')
            if len(rows)>=limit or not token or token in seen_tokens:break
            seen_tokens.add(token);body['pageToken']=token
        return rows[:limit]

async def _page(client,body,key):
    async def fetch():
        reserve_places_request()
        record('places-text', status='attempted')
        response=await client.post('https://places.googleapis.com/v1/places:searchText',
            headers={'X-Goog-Api-Key':key,'X-Goog-FieldMask':FIELDS},json=body)
        response.raise_for_status()
        return response.json()
    import json
    return await reuse_request(('places-page',json.dumps(body,sort_keys=True)),fetch)

async def geocode_address(query):
    rows=await search_text(query,limit=1)
    locations=[]
    for row in rows:
        pos=row.get('location',{});lat,lon=pos.get('latitude'),pos.get('longitude')
        if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in (lat,lon)):continue
        if abs(lat)>85 or abs(lon)>180:continue
        locations.append({'lat':lat,'lon':lon,'label':text(row.get('displayName',{}).get('text') or query),
                          'source':'google-places'})
    return locations

async def nearby_places(lat,lon,radius,queries,*,limit=50):
    limit=max(1,min(60,limit))
    queries=list(dict.fromkeys(q.strip() for q in queries if q.strip()))[:4]
    if not queries:queries=['scenic places and tourist attractions']
    # Fetch each query fairly, then continue only if its retained share or the
    # global distinct in-radius target is still missing. Never stop because a
    # raw page is full: out-of-radius results and duplicate IDs do not count.
    key=os.getenv('PHOTO_SCOUT_GOOGLE_PLACES_API_KEY') or os.getenv('PHOTO_SCOUT_GOOGLE_API_KEY')
    if not key:return [],{'status':'unavailable','provider':'google-places','queries':queries}
    lat0,lon0=lat,lon;dy=radius/111320;dx=dy/max(.01,math.cos(math.radians(lat)))
    bodies=[]
    for query in queries:
        body={'textQuery':query,'pageSize':20,'languageCode':'en'}
        if lon-dx>=-180 and lon+dx<=180:
            body['locationRestriction']={'rectangle':{
                'low':{'latitude':max(-90,lat-dy),'longitude':lon-dx},
                'high':{'latitude':min(90,lat+dy),'longitude':lon+dx}}}
        else:body['locationBias']={'circle':{'center':{'latitude':lat,'longitude':lon},'radius':radius}}
        bodies.append(body)
    responses=[[] for _ in queries];tokens=[set() for _ in queries]
    active=list(range(len(queries)));pages=[0 for _ in queries];failed=set()
    def usable(groups):
        seen=set();counts=[]
        for group in groups:
            count=0
            for row in group:
                pos=row.get('location',{});a,b=pos.get('latitude'),pos.get('longitude')
                name=text(row.get('displayName',{}).get('text',''));identity=row.get('id')
                if (not identity or not name or identity in seen or
                    not all(isinstance(v,(int,float)) and math.isfinite(v) for v in (a,b)) or
                    abs(a)>85 or abs(b)>180 or distance((lat0,lon0),(a,b))>radius):continue
                seen.add(identity);count+=1
            counts.append(count)
        return counts
    async with httpx.AsyncClient(timeout=25,follow_redirects=False) as client:
        while active:
            results=await asyncio.gather(*(_page(client,bodies[i],key) for i in active),return_exceptions=True)
            continuing=[]
            for i,data in zip(active,results):
                pages[i]+=1
                if isinstance(data,Exception):failed.add(i);continue
                responses[i].extend(data.get('places',[]))
                token=data.get('nextPageToken')
                if token and token not in tokens[i] and pages[i]<math.ceil(limit/20):
                    tokens[i].add(token);bodies[i]['pageToken']=token;continuing.append(i)
            counts=usable(responses)
            # Enough globally is not sufficient if a query's fair share is still
            # missing: preserve coverage of distinct categories in multi-query plans.
            share=math.ceil(limit/len(queries))
            active=[i for i in continuing if sum(counts)<limit or counts[i]<share]
    if len(failed)==len(queries) and not any(responses):
        return [],{'status':'unavailable','provider':'google-places','queries':queries}
    groups=[];seen=set()
    for response in responses:
        if isinstance(response,Exception):continue
        group=[]
        for row in response:
            pos=row.get('location',{});lat2,lon2=pos.get('latitude'),pos.get('longitude')
            if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in (lat2,lon2)):continue
            if abs(lat2)>85 or abs(lon2)>180:continue
            separation=distance((lat,lon),(lat2,lon2))
            place_id=row.get('id');name=text(row.get('displayName',{}).get('text',''))
            if separation>radius or not place_id or not name or place_id in seen:continue
            seen.add(place_id)
            group.append({'id':'google:'+place_id,'name':name,'lat':lat2,'lon':lon2,
                          'category':row.get('primaryType','place'),'categoryGroups':[],
                          'distanceMeters':round(separation),'provider':'google-places',
                          'sourceUrl':row.get('googleMapsUri') or 'https://www.google.com/maps/search/?api=1&query='+str(lat2)+','+str(lon2)+'&query_place_id='+place_id,
                          'attribution':'Google Maps','attributions':row.get('attributions',[]),
                          'visuallyAnalyzed':False})
        groups.append(group)
    from itertools import zip_longest
    selected=[p for batch in zip_longest(*groups) for p in batch if p][:limit]
    return selected,{'status':'ok','provider':'google-places','count':len(selected),
                    'foundPois':len(seen),'queries':queries,'coverage':'bounded-text-search',
                    'pagesFetched':sum(pages),'queryPages':dict(zip(queries,pages)),
                    'paginationPolicy':'distinct-candidate-coverage'}
