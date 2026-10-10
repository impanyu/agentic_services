from __future__ import annotations

import base64
import math
import os
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup

PANORAMAX_IMAGE_HOSTS = frozenset({
    'panoramax.ign.fr', 'panoramax.openstreetmap.fr',
    'panoramax-storage-public-fast.s3.gra.perf.cloud.ovh.net',
})

MAX_SCORED_IMAGES = 424
VIEWS_PER_PANORAMA = 8

HEADERS = {'User-Agent': 'AISoupPhotoScout/0.1 (https://aisoup.net/contact/)'}


def text(value):
    value=str(value or '')
    return (BeautifulSoup(value, 'html.parser').get_text(' ', strip=True) if '<' in value else value)[:800]


def distance(a, b):
    p, q = map(math.radians, (a[0], b[0]))
    dp, dl = q-p, math.radians((b[1]-a[1]+180)%360-180)
    return 6371000 * 2 * math.asin(min(1, math.sqrt(math.sin(dp/2)**2 + math.cos(p)*math.cos(q)*math.sin(dl/2)**2)))


def image_host(url):
    u = urlsplit(url)
    host = (u.hostname or '').lower()
    return u.scheme == 'https' and not u.username and not u.password and u.port in (None,443) and (host == 'upload.wikimedia.org' or host.endswith('.fbcdn.net') or host in PANORAMAX_IMAGE_HOSTS)


async def get_json(client, url, params=None, headers=None, *, method='GET', max_bytes=2_000_000):
    async with client.stream(method, url, params=params if method=='GET' else None, data=params if method=='POST' else None, headers=headers) as r:
        r.raise_for_status()
        data = bytearray()
        async for chunk in r.aiter_bytes():
            data.extend(chunk)
            if len(data) > max_bytes:
                raise ValueError('Source response exceeds limit')
    import json
    return json.loads(data)


async def commons(client, lat, lon, radius):
    import asyncio
    delta=radius*.65/111320
    points=[(lat,lon),(lat+delta,lon),(lat-delta,lon),
            (lat,lon+delta/max(.01,math.cos(math.radians(lat)))),
            (lat,lon-delta/max(.01,math.cos(math.radians(lat))))]
    async def search(point):
        return await get_json(client, 'https://commons.wikimedia.org/w/api.php', {
            'action':'query','format':'json','list':'geosearch','gsnamespace':6,
            'gscoord':f'{point[0]}|{point[1]}','gsradius':min(10000,max(100,round(radius*.6))),'gslimit':30})
    responses=await asyncio.gather(*(search(p) for p in points if abs(p[0])<90 and abs(p[1])<=180),return_exceptions=True)
    if all(isinstance(r,Exception) for r in responses): raise ValueError('Commons searches unavailable')
    seen=set(); geo=[]
    # Round robin across search areas avoids nearest-center saturation.
    lists=[r.get('query',{}).get('geosearch',[]) for r in responses if isinstance(r,dict)]
    for i in range(30):
        for rows in lists:
            if i<len(rows):
                g=rows[i]
                if g['pageid'] not in seen and distance((lat,lon),(g['lat'],g['lon']))<=radius:
                    seen.add(g['pageid']); geo.append(g)
    if not geo:
        return []
    sampled=[]
    for g in geo:
        if all(distance((g['lat'],g['lon']),(x['lat'],x['lon']))>=20 for x in sampled):
            sampled.append(g)
        if len(sampled)>=50: break
    geo=sampled
    info = await get_json(client, 'https://commons.wikimedia.org/w/api.php', {
        'action':'query','format':'json','pageids':'|'.join(str(g['pageid']) for g in geo),
        'prop':'imageinfo','iiprop':'url|extmetadata','iiurlwidth':1024})
    pages = info.get('query',{}).get('pages',{})
    found=[]
    for g in geo:
        row = pages.get(str(g['pageid']),{}).get('imageinfo',[{}])[0]
        meta=row.get('extmetadata',{})
        val=lambda k: text(meta.get(k,{}).get('value',''))
        lic=val('LicenseShortName'); licurl=val('LicenseUrl')
        image=row.get('thumburl','')
        # Noncommercial/no-derivatives/unknown files are not eligible.
        if not (lic.startswith(('CC BY-SA','CC BY ')) or lic in ('CC0','Public domain')):
            continue
        if not image_host(image):
            continue
        found.append({'id':f"commons:{g['pageid']}",'provider':'wikimedia-commons',
            'title':g['title'].removeprefix('File:'),'lat':g['lat'],'lon':g['lon'],
            'locationType':'file_geotag_not_verified_camera_position',
            'imageUrl':image,'sourceUrl':row.get('descriptionurl'),
            'author':val('Artist'),'license':lic,'licenseUrl':licurl,
            'sourceDate':val('DateTimeOriginal') or None,
            'capturedAt':(val('DateTimeOriginal') or None) if 'upload' not in val('DateTimeOriginal').lower() else None,
            'description':val('ImageDescription')})
    return found


async def mapillary(client, lat, lon, radius):
    token=os.getenv('PHOTO_SCOUT_MAPILLARY_TOKEN','')
    if not token:
        return []
    dy=radius/111320; dx=dy/max(.01,math.cos(math.radians(lat)))
    # Skip polar/antimeridian bounding boxes rather than misrepresent coverage.
    if abs(lat)+dy>=90 or abs(lon)+dx>=180:
        return []
    data=await get_json(client,'https://graph.mapillary.com/images',{
        'bbox':f'{lon-dx},{lat-dy},{lon+dx},{lat+dy}','limit':30,
        'fields':'id,geometry,captured_at,creator,thumb_1024_url'},
        {'Authorization':f'OAuth {token}'})
    found=[]
    for row in data.get('data',[]):
        coords=row.get('geometry',{}).get('coordinates',[])
        creator=row.get('creator',{})
        if len(coords)!=2 or not isinstance(creator,dict) or not creator.get('username'):
            continue
        if not image_host(row.get('thumb_1024_url','')):
            continue
        found.append({'id':'mapillary:'+row['id'],'provider':'mapillary',
            'title':'Street-level view','lat':coords[1],'lon':coords[0],
            'locationType':'camera_geotag','imageUrl':row['thumb_1024_url'],
            'sourceUrl':'https://www.mapillary.com/app/?pKey='+row['id'],
            'author':creator['username'],'license':'CC BY-SA 4.0',
            'licenseUrl':'https://creativecommons.org/licenses/by-sa/4.0/',
            'capturedAt':row.get('captured_at'),'description':''})
    return found


async def panoramax(client, lat, lon, radius):
    """Federated STAC catalog; accept only verified image hosts and open licenses."""
    import asyncio
    import uuid
    dy=radius/111320; dx=dy/max(.01,math.cos(math.radians(lat)))
    if abs(lat)+dy>=90 or abs(lon)+dx>=180:
        return []
    points=[(lat,lon),(lat+dy*.6,lon),(lat-dy*.6,lon),
            (lat,lon+dx*.6),(lat,lon-dx*.6)]
    async def search(p):
        return await get_json(client,'https://api.panoramax.xyz/api/search',{
            'bbox':f'{p[1]-dx*.5},{p[0]-dy*.5},{p[1]+dx*.5},{p[0]+dy*.5}',
            'limit':8})
    responses=await asyncio.gather(*(search(p) for p in points),return_exceptions=True)
    if all(isinstance(r,Exception) for r in responses):
        raise ValueError('Panoramax catalog unavailable')
    rows=[]; seen=set()
    licenses={'CC-BY-SA-4.0':('CC BY-SA 4.0','https://creativecommons.org/licenses/by-sa/4.0/'),
              'CC-BY-4.0':('CC BY 4.0','https://creativecommons.org/licenses/by/4.0/'),
              'CC0-1.0':('CC0','https://creativecommons.org/publicdomain/zero/1.0/'),
              'etalab-2.0':('Etalab Open License 2.0','https://ia.numerique.gouv.fr/licence-ouverte-open-licence/')}
    for response in responses:
        if not isinstance(response,dict): continue
        for f in response.get('features',[])[:8]:
            try:
                identifier=str(uuid.UUID(f['id']))
                geom=f.get('geometry',{}); coords=geom.get('coordinates',[])
                if geom.get('type')!='Point' or len(coords)<2: continue
                lon2,lat2=map(float,coords[:2])
                if not math.isfinite(lat2) or not math.isfinite(lon2) or abs(lat2)>90 or abs(lon2)>180: continue
                if distance((lat,lon),(lat2,lon2))>radius: continue
                props=f.get('properties',{}); license_info=licenses.get(props.get('license'))
                if not license_info or identifier in seen: continue
                # Metadata from unknown/private federated instances is not followed.
                asset=f.get('assets',{}).get('sd',{})
                image=asset.get('href','')
                if urlsplit(image).hostname not in ('panoramax.ign.fr','panoramax.openstreetmap.fr') or not image_host(image): continue
                if props.get('geovisio:visibility','anyone')!='anyone': continue
                authors=[text(p.get('name')) for p in f.get('providers',[]) if 'producer' in p.get('roles',[])]
                authors=[a for a in authors if a]
                if not authors: continue
                seen.add(identifier)
                rows.append({'id':'panoramax:'+identifier,'provider':'panoramax',
                    'title':'Geolocated street-level photograph','lat':lat2,'lon':lon2,
                    'locationType':'camera_geotag','imageUrl':image,
                    'sourceUrl':'https://panoramax.xyz/#pic='+identifier,
                    'author':' / '.join(dict.fromkeys(authors)),
                    'license':license_info[0],'licenseUrl':license_info[1],
                    'sourceDate':props.get('datetime'),'capturedAt':props.get('datetime'),
                    'viewHeadingDegrees':props.get('view:azimuth'),
                    'description':'Street-level camera position; not a verified safe standing point.'})
            except (ValueError,TypeError,KeyError):
                continue
    return rows


def google_enabled():
    return os.getenv('PHOTO_SCOUT_GOOGLE_ENABLED') == '1' and bool(os.getenv('PHOTO_SCOUT_GOOGLE_API_KEY'))


def google_sampling_spacing(radius):
    return round(max(80,min(250,radius*.2)))


def bearing(origin, target):
    a,b=map(math.radians,(origin[0],target[0]))
    delta=math.radians(target[1]-origin[1])
    return round(math.degrees(math.atan2(math.sin(delta)*math.cos(b),
        math.cos(a)*math.sin(b)-math.sin(a)*math.cos(b)*math.cos(delta))))%360


def google_query_points(lat,lon,radius):
    """Bounded circular grid, spread over the whole requested area."""
    step=max(80,radius/5)
    count=math.ceil(radius/step)
    offsets=[(x*step,y*step) for x in range(-count,count+1) for y in range(-count,count+1)
        if math.hypot(x*step,y*step)<=radius]
    chosen=[(0,0)]; offsets.remove((0,0))
    while offsets and len(chosen)<50:
        point=max(offsets,key=lambda p:min(math.hypot(p[0]-q[0],p[1]-q[1]) for q in chosen))
        chosen.append(point); offsets.remove(point)
    result=[]
    for x,y in chosen:
        p=(lat+y/111320,lon+x/(111320*max(.01,math.cos(math.radians(lat)))))
        if abs(p[0])<=85 and abs(p[1])<=180: result.append(p)
    return result


async def google_streetview(client, lat, lon, radius, targets=None, area_sampling=False):
    """Bounded panorama discovery, including provider-supported indoor views. Never put the credential in candidate URLs."""
    import asyncio
    import re
    from urllib.parse import urlencode
    key=os.getenv('PHOTO_SCOUT_GOOGLE_API_KEY','')
    if area_sampling:
        # Reserve most samples for the region, plus a few named POI anchors.
        anchors=list(targets or [])[:5]
        area_points=google_query_points(lat,lon,radius)[:50-len(anchors)]
        points=area_points+[(p['lat'],p['lon']) for p in anchors]
        target_rows=[None]*len(area_points)+anchors
    else:
        points=[(p['lat'],p['lon']) for p in targets] if targets is not None else google_query_points(lat,lon,radius)
        target_rows=list(targets) if targets is not None else [None]*len(points)
    slots=asyncio.Semaphore(max(1,min(32,int(os.getenv('PHOTO_SCOUT_STREETVIEW_CONCURRENCY','24')))))
    async def search(p):
        async with slots:
            return await get_json(client,'https://maps.googleapis.com/maps/api/streetview/metadata',
                {'location':f'{p[0]},{p[1]}','radius':min(200,max(50,radius//3)),'key':key})
    responses=await asyncio.gather(*(search(p) for p in points),return_exceptions=True)
    rows=[]; seen=set(); locations=[]; successful=False
    spacing=google_sampling_spacing(radius)
    for index,data in enumerate(responses):
        if not isinstance(data,dict): continue
        if data.get('status') in ('OK','ZERO_RESULTS'): successful=True
        if data.get('status')!='OK': continue
        pano=data.get('pano_id',''); loc=data.get('location',{})
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',pano): continue
        try: lat2,lon2=float(loc['lat']),float(loc['lng'])
        except (KeyError,TypeError,ValueError): continue
        if not math.isfinite(lat2) or not math.isfinite(lon2) or abs(lat2)>85 or abs(lon2)>180: continue
        if distance((lat,lon),(lat2,lon2))>radius: continue
        if pano in seen:
            if target_rows[index] is not None:
                poi=target_rows[index]
                for row in rows:
                    if row['imageUrl'].startswith(f'google-streetview://{pano}/'):
                        if all(p['id']!=poi['id'] for p in row.get('poiCandidates',[])):
                            row.setdefault('poiCandidates',[]).append(poi);row.setdefault('poi',poi)
            continue
        if target_rows[index] is None and any(distance((lat2,lon2),p)<spacing for p in locations): continue
        seen.add(pano)
        locations.append((lat2,lon2))
        poi=target_rows[index]
        # Eight compass headings cover 360 degrees with overlapping views.
        views=[(heading,0) for heading in range(0,360,45)]
        for heading,pitch in views:
            rows.append({'id':f'google:{pano}:{heading}:f120','provider':'google-street-view',
                'title':f'Street View facing {heading} degrees, pitch {pitch} degrees','lat':lat2,'lon':lon2,
                'locationType':'camera_geotag','imageUrl':f'google-streetview://{pano}/{heading}/0/120',
                'sourceUrl':'https://www.google.com/maps/@?'+urlencode({'api':1,'map_action':'pano','pano':pano,
                    'viewpoint':f'{lat2},{lon2}','heading':heading,'pitch':pitch,'fov':120}),
                'author':text(data.get('copyright')) or 'Google Street View',
                'license':'Google Maps Platform terms; not an open license',
                'licenseUrl':'https://cloud.google.com/maps-platform/terms',
                'sourceDate':data.get('date'),'capturedAt':data.get('date'),
                'viewHeadingDegrees':heading,'viewPitchDegrees':pitch,'viewFovDegrees':120,
                'description':'Street View camera position; access and safe standing point unverified.',
                **({'poi':poi,'poiCandidates':[poi],'poiDistanceMeters':round(distance((lat2,lon2),(poi['lat'],poi['lon'])))} if poi else {})})
    if not successful: raise ValueError('Google Street View metadata unavailable')
    return rows


def diverse_sample(rows,limit=12):
    """Rotate sources and retain distinct images at shared points for comparison."""
    from itertools import zip_longest
    groups={}
    for row in sorted(rows,key=lambda r:r['distanceMeters']):
        groups.setdefault(row['provider'],[]).append(row)
    if 'google-street-view' in groups:
        # One view from each spatially sampled panorama before any second view.
        panoramas={}
        for row in groups['google-street-view']:
            panoramas.setdefault(row['imageUrl'].split('/')[2],[]).append(row)
        remaining=list(panoramas.values()); spread=[remaining.pop(0)]
        while remaining:
            group=max(remaining,key=lambda g:min(distance((g[0]['lat'],g[0]['lon']),
                (s[0]['lat'],s[0]['lon'])) for s in spread))
            spread.append(group); remaining.remove(group)
        groups['google-street-view']=[r for batch in zip_longest(*spread) for r in batch if r]
    selected=[]
    for batch in zip_longest(*groups.values()):
        for row in batch:
            if row and all(x['provider']!=row['provider'] or
                (row['provider']=='google-street-view' and x['imageUrl']!=row['imageUrl']) or
                distance((row['lat'],row['lon']),(x['lat'],x['lon']))>=35 for x in selected):
                selected.append(row)
                if len(selected)==limit: return selected
    return selected


async def candidates(lat,lon,radius,pois=None,visual_exploration=False,point_only=False):
    import asyncio
    statuses={}; rows=[]
    if point_only:
        # Resolve one nearest panorama at the address, not a grid of nearby spots.
        radius=50
        pois=[{'id':f'address:{lat:.6f}:{lon:.6f}','name':'Selected address','lat':lat,'lon':lon}]
    if pois == [] and not visual_exploration: return [],statuses
    async with httpx.AsyncClient(timeout=25,headers=HEADERS,follow_redirects=False) as client:
        providers=[] if point_only else [('wikimedia-commons',commons)]
        if google_enabled():
            async def google(client,lat,lon,radius):
                return await google_streetview(client,lat,lon,radius,targets=pois,**({'area_sampling':True} if visual_exploration and not point_only else {}))
            providers.append(('google-street-view',google))
        if not point_only and os.getenv('PHOTO_SCOUT_PANORAMAX_ENABLED','1')=='1':
            providers.append(('panoramax',panoramax))
        if not point_only and os.getenv('PHOTO_SCOUT_MAPILLARY_TOKEN'):
            providers.append(('mapillary',mapillary))
        results=await asyncio.gather(*(fn(client,lat,lon,radius) for _,fn in providers),return_exceptions=True)
        for (name,_),result in zip(providers,results):
            if isinstance(result,Exception):
                statuses[name]={'status':'unavailable','errorType':type(result).__name__}
            else:
                statuses[name]={'status':'ok','eligibleImages':len(result)}; rows+=result
                if name=='google-street-view':
                    statuses[name].update(samplingSpacingMeters=google_sampling_spacing(radius) if (visual_exploration and not point_only) or pois is None else None,maxViewsPerLocation=VIEWS_PER_PANORAMA,
                        queriedLocations=1 if point_only else (min(50,len(google_query_points(lat,lon,radius))+min(5,len(pois or []))) if visual_exploration else len(pois) if pois is not None else len(google_query_points(lat,lon,radius))),samplingMode='point' if point_only else 'area-and-poi' if visual_exploration else 'poi' if pois is not None else 'area')
    valid=[]
    for row in rows:
        d=distance((lat,lon),(row['lat'],row['lon']))
        if d<=radius:
            if visual_exploration:row['allowUnlistedPlace']=True
            if pois is not None and not row.get('poi'):
                nearby=[p for p in pois if distance((row['lat'],row['lon']),(p['lat'],p['lon']))<=250]
                if not nearby and not visual_exploration:continue
                if nearby:
                    nearest=min(nearby,key=lambda p:distance((row['lat'],row['lon']),(p['lat'],p['lon'])))
                    row['poi']=nearest;row['poiCandidates']=nearby;row['poiDistanceMeters']=round(distance((row['lat'],row['lon']),(nearest['lat'],nearest['lon'])))
            row['distanceMeters']=round(d); valid.append(row)
    # Keep every angle of each discovered Google panorama; a global 24-image
    # cut previously discarded most alternate views before the model saw them.
    google_rows=[r for r in valid if r['provider']=='google-street-view']
    other_rows=[r for r in valid if r['provider']!='google-street-view']
    sampled=(google_rows+diverse_sample(other_rows,24))[:MAX_SCORED_IMAGES]
    for name,status in statuses.items():
        if status['status']=='ok': status['sampledImages']=sum(r['provider']==name for r in sampled)
    return sampled,statuses


async def image_data(url):
    if url.startswith('google-streetview://'):
        return await google_image_data(url)
    if not image_host(url):
        raise ValueError('Image provider host is not allowed')
    async with httpx.AsyncClient(timeout=25,headers=HEADERS,follow_redirects=False) as client:
        for hop in range(3):
            async with client.stream('GET',url) as r:
                if r.is_redirect:
                    from urllib.parse import urljoin
                    target=urljoin(url,r.headers.get('location',''))
                    # Only verified Panoramax hosts can redirect to its verified CDN.
                    if urlsplit(url).hostname not in PANORAMAX_IMAGE_HOSTS or urlsplit(target).hostname not in PANORAMAX_IMAGE_HOSTS or not image_host(target):
                        raise ValueError('Image redirect is not allowed')
                    url=target
                    continue
                r.raise_for_status(); data=bytearray()
                async for chunk in r.aiter_bytes():
                    data.extend(chunk)
                    if len(data)>3_000_000: raise ValueError('Image too large')
                break
        else:
            raise ValueError('Image redirect limit exceeded')
    if data.startswith(b'\xff\xd8\xff'): mime='image/jpeg'
    elif data.startswith(b'\x89PNG\r\n\x1a\n'): mime='image/png'
    elif data[:4]==b'RIFF' and data[8:12]==b'WEBP': mime='image/webp'
    else: raise ValueError('Unsupported image type')
    return f'data:{mime};base64,'+base64.b64encode(data).decode()


async def google_image_data(reference):
    import re
    import sqlite3
    from datetime import datetime, timezone
    from pathlib import Path
    match=re.fullmatch(r'google-streetview://([A-Za-z0-9_-]{1,200})/(\d{1,3})(?:/(-?\d{1,2}))?(?:/(\d{2,3}))?',reference)
    if not match or int(match[2])>=360 or not -90<=int(match[3] or 0)<=90 or not 30<=int(match[4] or 120)<=120 or not google_enabled(): raise ValueError('Google imagery unavailable')
    # Keep request accounting; a positive limit is an optional operator setting.
    # Development is uncapped by default. The signed image proxy remains protected.
    path=Path(os.getenv('WEB_EVIDENCE_DB','data/web-evidence.db'))
    path.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(path,timeout=15) as db:
        db.execute('CREATE TABLE IF NOT EXISTS photo_scout_google_budget (day TEXT PRIMARY KEY, requests INTEGER NOT NULL)')
        db.execute('BEGIN IMMEDIATE')
        day=datetime.now(timezone.utc).date().isoformat()
        db.execute('INSERT OR IGNORE INTO photo_scout_google_budget VALUES(?,0)',(day,))
        count=db.execute('SELECT requests FROM photo_scout_google_budget WHERE day=?',(day,)).fetchone()[0]
        limit=int(os.getenv('PHOTO_SCOUT_GOOGLE_DAILY_IMAGE_LIMIT','0'))
        if limit>0 and count>=limit:
            raise ValueError('Google image budget exhausted')
        db.execute('UPDATE photo_scout_google_budget SET requests=requests+1 WHERE day=?',(day,))
    async with httpx.AsyncClient(timeout=25,follow_redirects=False) as client:
        async with client.stream('GET','https://maps.googleapis.com/maps/api/streetview',params={
            'pano':match[1],'heading':match[2],'pitch':int(match[3] or 0),'fov':int(match[4] or 120),'size':'640x640',
            'return_error_code':'true','key':os.environ['PHOTO_SCOUT_GOOGLE_API_KEY']}) as response:
            if response.status_code!=200: raise ValueError('Google image request failed')
            data=bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data)>3_000_000: raise ValueError('Google image exceeds limit')
    if not data.startswith(b'\xff\xd8\xff'): raise ValueError('Google image format unsupported')
    return 'data:image/jpeg;base64,'+base64.b64encode(data).decode()


POI_CATEGORY_FILTERS = {
    'viewpoint': {'tourism': ['viewpoint']},
    'park': {'leisure': ['park','garden','nature_reserve']},
    'attraction': {'tourism': ['attraction']},
    'museum': {'tourism': ['museum']},
    'artwork': {'tourism': ['artwork']},
    'historic': {'historic': None},
    'nature': {'natural': ['beach','peak','water','wood']},
    'recreation': {'leisure': ['recreation_ground']},
    'cafe': {'amenity': ['cafe']},
    'restaurant': {'amenity': ['restaurant','fast_food']},
    'bar': {'amenity': ['bar','pub','biergarten']},
    'shop': {'shop': None},
}


async def nearby_pois(lat,lon,radius,categories=None):
    # Apply category filters at the source, before the bounded POI selection.
    allowed=[c for c in POI_CATEGORY_FILTERS if c not in {'cafe','restaurant','bar','shop'}] if categories is None else categories
    points=[(lat,lon)]
    if radius>5000:
        dy=radius*.65/111320;dx=dy/max(.01,math.cos(math.radians(lat)))
        points.extend([(lat+dy,lon),(lat-dy,lon),(lat,lon+dx),(lat,lon-dx)])
        points=[(y,((x+180)%360)-180) for y,x in points if abs(y)<=85]
    sample_radius=min(radius,5000)
    blocks=[]
    for y,x in points:
        area=f'(around:{sample_radius},{y},{x})["name"]'
        clauses=[]
        for category in allowed:
            for tag,values in POI_CATEGORY_FILTERS[category].items():
                selector=f'["{tag}"]' if values is None else f'["{tag}"~"^({"|".join(values)})$"]'
                clauses.append(f'nwr{area}{selector};')
        # Multiple bounded outputs in one request, so city searches sample regions
        # without many parallel requests against public Overpass servers.
        blocks.append('('+''.join(clauses)+f');out center {40 if len(points)>1 else 80};')
    query=f'[out:json][timeout:{30 if len(points)>1 else 10}][maxsize:{67108864 if len(points)>1 else 16777216}];'+''.join(blocks)
    errors=[];data=None
    endpoints=('https://overpass-api.de/api/interpreter','https://overpass.private.coffee/api/interpreter')
    async with httpx.AsyncClient(timeout=35 if len(points)>1 else 12,headers=HEADERS,follow_redirects=False) as client:
        for endpoint in endpoints:
            try:
                data=await get_json(client,endpoint,{'data':query})
                if not isinstance(data.get('elements'),list) or data.get('remark'):
                    raise ValueError('Incomplete Overpass response')
                break
            except Exception as e:
                errors.append({'endpoint':urlsplit(endpoint).hostname,'errorType':type(e).__name__,
                    **({'httpStatus':e.response.status_code} if isinstance(e,httpx.HTTPStatusError) else {})})
    if data is None or len(errors)==len(endpoints):
        return [],{'status':'unavailable','attempts':errors}
    result=[]
    for row in data['elements'][:max(80,40*len(points))]:
        coord=row.get('center',row);tags=row.get('tags',{})
        if 'lat' not in coord or 'lon' not in coord: continue
        lat2,lon2=coord['lat'],coord['lon']
        if not math.isfinite(lat2) or not math.isfinite(lon2): continue
        d=distance((lat,lon),(lat2,lon2))
        if d>radius: continue
        groups=[key for key in allowed if any(tag in tags and (values is None or tags[tag] in values) for tag,values in POI_CATEGORY_FILTERS[key].items())]
        if not groups: continue
        name=text(tags.get('name'))
        category=next(tags[tag] for key in groups for tag,values in POI_CATEGORY_FILTERS[key].items() if tag in tags and (values is None or tags[tag] in values))
        # Deduplicate named node/area representations of the same place.
        if any(name.casefold()==x['name'].casefold() and distance((lat2,lon2),(x['lat'],x['lon']))<150 for x in result): continue
        result.append({'id':f"osm:{row['type']}:{row['id']}",'name':name,'lat':lat2,'lon':lon2,
            'category':category,'categoryGroups':groups,'distanceMeters':round(d),'provider':'openstreetmap',
            'sourceUrl':f"https://www.openstreetmap.org/{row['type']}/{row['id']}",
            'license':'ODbL 1.0','attribution':'OpenStreetMap contributors','visuallyAnalyzed':False})
    # Prefer viewpoints, then rotate categories so dense artwork clusters cannot crowd out parks.
    from itertools import zip_longest
    groups={}
    for p in sorted(result,key=lambda p:(p['category']!='viewpoint',p['distanceMeters'])):
        region=min(range(len(points)),key=lambda i:distance(points[i],(p['lat'],p['lon'])))
        groups.setdefault((region,p['category']),[]).append(p)
    selected=[p for batch in zip_longest(*groups.values()) for p in batch if p][:50]
    return selected,{'status':'ok','count':len(selected),'foundPois':len(result),
        'endpoint':urlsplit(endpoint).hostname,'attempts':errors,
        'sampledAreas':len(points),'areaRadiusMeters':sample_radius,'coverage':'bounded-area-sample'}
