"""Free-text Google Places discovery, bounded to the selected search region."""
import asyncio
import math
import os
import httpx

from .sources import distance, text

FIELDS='places.id,places.displayName,places.location,places.primaryType,places.googleMapsUri,places.attributions,nextPageToken'

async def search_text(query, *, center=None, radius=None, limit=20):
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
                response=await client.post('https://places.googleapis.com/v1/places:searchText',
                    headers={'X-Goog-Api-Key':key,'X-Goog-FieldMask':FIELDS},json=body)
                response.raise_for_status()
                data=response.json()
            except (httpx.HTTPError,ValueError):
                if rows:break
                raise
            rows.extend(data.get('places',[]))
            token=data.get('nextPageToken')
            if len(rows)>=limit or not token or token in seen_tokens:break
            seen_tokens.add(token);body['pageToken']=token
        return rows[:limit]

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

async def nearby_places(lat,lon,radius,queries,*,limit=30):
    limit=max(1,min(60,limit))
    queries=list(dict.fromkeys(q.strip() for q in queries if q.strip()))[:4]
    if not queries:queries=['scenic places and tourist attractions']
    responses=await asyncio.gather(*(search_text(q,center=(lat,lon),radius=radius,limit=limit) for q in queries),return_exceptions=True)
    if all(isinstance(r,Exception) for r in responses):
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
                    'foundPois':len(seen),'queries':queries,'coverage':'bounded-text-search'}
