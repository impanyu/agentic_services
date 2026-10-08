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


async def get_json(client, url, params=None, headers=None):
    async with client.stream('GET', url, params=params, headers=headers) as r:
        r.raise_for_status()
        data = bytearray()
        async for chunk in r.aiter_bytes():
            data.extend(chunk)
            if len(data) > 2_000_000:
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
            'gscoord':f'{point[0]}|{point[1]}','gsradius':max(100,round(radius*.6)),'gslimit':30})
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


def diverse_sample(rows,limit=12):
    """Rotate sources and retain distinct images at shared points for comparison."""
    from itertools import zip_longest
    groups={}
    for row in sorted(rows,key=lambda r:r['distanceMeters']):
        groups.setdefault(row['provider'],[]).append(row)
    selected=[]
    for batch in zip_longest(*groups.values()):
        for row in batch:
            if row and all(x['provider']!=row['provider'] or distance((row['lat'],row['lon']),(x['lat'],x['lon']))>=35 for x in selected):
                selected.append(row)
                if len(selected)==limit: return selected
    return selected


async def candidates(lat,lon,radius):
    import asyncio
    statuses={}; rows=[]
    async with httpx.AsyncClient(timeout=25,headers=HEADERS,follow_redirects=False) as client:
        providers=[('wikimedia-commons',commons)]
        if os.getenv('PHOTO_SCOUT_PANORAMAX_ENABLED','1')=='1':
            providers.append(('panoramax',panoramax))
        if os.getenv('PHOTO_SCOUT_MAPILLARY_TOKEN'):
            providers.append(('mapillary',mapillary))
        results=await asyncio.gather(*(fn(client,lat,lon,radius) for _,fn in providers),return_exceptions=True)
        for (name,_),result in zip(providers,results):
            if isinstance(result,Exception):
                statuses[name]={'status':'unavailable','errorType':type(result).__name__}
            else:
                statuses[name]={'status':'ok','eligibleImages':len(result)}; rows+=result
    valid=[]
    for row in rows:
        d=distance((lat,lon),(row['lat'],row['lon']))
        if d<=radius:
            row['distanceMeters']=round(d); valid.append(row)
    return diverse_sample(valid),statuses


async def image_data(url):
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


async def nearby_pois(lat,lon,radius):
    query=f'[out:json][timeout:12];nwr(around:{radius},{lat},{lon})["name"]["tourism"~"^(attraction|viewpoint|artwork|museum)$"];out center 40;'
    try:
        async with httpx.AsyncClient(timeout=18,headers=HEADERS,follow_redirects=False) as client:
            data=await get_json(client,'https://overpass-api.de/api/interpreter',{'data':query})
        result=[]
        for row in data.get('elements',[])[:40]:
            coord=row.get('center',row);tags=row.get('tags',{})
            if 'lat' not in coord or 'lon' not in coord: continue
            d=distance((lat,lon),(coord['lat'],coord['lon']))
            if d>radius: continue
            result.append({'name':text(tags.get('name')),'lat':coord['lat'],'lon':coord['lon'],
                'category':tags.get('tourism'),'distanceMeters':round(d),
                'sourceUrl':f"https://www.openstreetmap.org/{row['type']}/{row['id']}",
                'license':'ODbL 1.0','attribution':'OpenStreetMap contributors',
                'visuallyAnalyzed':False})
        return result,{'status':'ok','count':len(result)}
    except Exception as e:
        return [],{'status':'unavailable','errorType':type(e).__name__}
