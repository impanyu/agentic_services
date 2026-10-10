"""On-demand Places photo probe/display adapter, separate from scoring and composites.

Photo resources and media URLs are transient. Fetch names fresh on every invocation;
keep media URLs and resource names out of durable reports and portrait jobs. A POI association
is not the camera position. Reports retain only a place ID and a hashed photo selector, resolved fresh.
"""
import asyncio
import hashlib
import os
import re
import json
from urllib.parse import urlsplit

from .sources import get_json, text


def photo_media_host(url):
    try:
        u=urlsplit(url)
        host=(u.hostname or '').lower()
        return (u.scheme=='https' and not u.username and not u.password
                and u.port in (None,443)
                and (host=='googleusercontent.com' or host.endswith('.googleusercontent.com')))
    except ValueError:
        return False


def attribution_url(url):
    if str(url).startswith('//'):url='https:'+url
    try:
        u=urlsplit(str(url))
        return url if u.scheme=='https' and not u.username and not u.password and u.port in (None,443) else None
    except ValueError:
        return None


def photo_selector(photo):
    # Resource names are short-lived request tokens, not persistent image IDs.
    metadata={'width':photo.get('widthPx'),'height':photo.get('heightPx'),
        'authors':sorted((str(a.get('displayName','')),str(attribution_url(a.get('uri')) or ''))
                         for a in photo.get('authorAttributions',[]) if isinstance(a,dict))}
    return hashlib.sha256(json.dumps(metadata,sort_keys=True).encode()).hexdigest()


async def place_photos(client, place_id, *, limit=3, width=800, selector=None):
    """Fetch a fresh, bounded photo selection with attribution. No API key in output."""
    place_id=place_id.removeprefix('google:')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',place_id):
        raise ValueError('Invalid Google place ID')
    key=os.getenv('PHOTO_SCOUT_GOOGLE_PLACES_API_KEY') or os.getenv('PHOTO_SCOUT_GOOGLE_API_KEY')
    if not key:raise ValueError('Google Places credential unavailable')
    limit=max(1,min(10,limit));width=max(1,min(1600,width))
    headers={'X-Goog-Api-Key':key}
    data=await get_json(client,'https://places.googleapis.com/v1/places/'+place_id,
        headers={**headers,'X-Goog-FieldMask':'id,displayName,googleMapsUri,photos'})
    if data.get('id')!=place_id:raise ValueError('Photo response place mismatch')
    async def media(photo):
        name=photo.get('name','')
        if not re.fullmatch(r'places/'+re.escape(place_id)+r'/photos/[A-Za-z0-9_-]{1,2000}',name):return None
        response=await get_json(client,'https://places.googleapis.com/v1/'+name+'/media',
            {'maxWidthPx':width,'skipHttpRedirect':'true'},headers)
        url=response.get('photoUri','')
        if not photo_media_host(url):return None
        authors=[{'displayName':text(a.get('displayName','')),'uri':attribution_url(a.get('uri'))}
                 for a in photo.get('authorAttributions',[]) if isinstance(a,dict)]
        return {'id':'google-place-photo:'+photo_selector(photo)[:24],
            'photoReference':'google-place-photo://'+place_id+'/'+photo_selector(photo),
            'provider':'google-places-photos','imageUrl':url,'authorAttributions':authors,
            'originalWidth':photo.get('widthPx'),'originalHeight':photo.get('heightPx'),
            'locationType':'place_association_not_verified_camera_position',
            'capabilities':{'viewable':True,'scorable':True,'selfieBackground':True,'adjustableView':False}}
    selected=[p for p in data.get('photos',[]) if isinstance(p,dict) and (selector is None or photo_selector(p)==selector)]
    if selector is not None and len(selected)!=1:selected=[]
    # Ambiguous metadata cannot safely identify a photo across refreshed requests.
    counts={photo_selector(p):sum(photo_selector(x)==photo_selector(p) for x in selected) for p in selected}
    selected=[p for p in selected if counts[photo_selector(p)]==1][:limit]
    photos=await asyncio.gather(*(media(p) for p in selected),return_exceptions=True)
    return {'placeId':place_id,'title':text(data.get('displayName',{}).get('text')),
        'sourceUrl':attribution_url(data.get('googleMapsUri')),
        'photos':[p for p in photos if isinstance(p,dict)],'availablePhotos':len(data.get('photos',[])),
        'cachePolicy':'no-store','attribution':'Google Maps','usage':'photo-candidate'}


async def candidates(client,pois):
    """Bounded POI photos; no invented camera position or heading."""
    slots=asyncio.Semaphore(4)
    async def fetch(poi):
        async with slots:
            async with asyncio.timeout(12):
                data=await place_photos(client,poi['id'],limit=2)
        return [{**photo,'imageUrl':photo['photoReference'],'provider':'google-places-photos',
            'title':data['title'],'lat':poi['lat'],'lon':poi['lon'],'poi':poi,'poiCandidates':[poi],
            'author':'; '.join(a['displayName'] for a in photo['authorAttributions']),
            'license':'Google Maps Platform terms','licenseUrl':'https://cloud.google.com/maps-platform/terms',
            'sourceUrl':data['sourceUrl'],'cacheable':False,'viewHeadingDegrees':None,
            'description':'Contributor photo associated with this POI. Camera coordinates and direction are unknown; inspect the image for relevance.'}
            for photo in data['photos']]
    results=await asyncio.gather(*(fetch(p) for p in pois[:8] if str(p.get('id','')).startswith('google:')),return_exceptions=True)
    if results and all(isinstance(r,Exception) for r in results):raise ValueError('Place photo lookups unavailable')
    return [row for rows in results if isinstance(rows,list) for row in rows]


async def image_data(reference):
    import base64,httpx
    match=re.fullmatch(r'google-place-photo://([A-Za-z0-9_-]{1,200})/([a-f0-9]{64})',reference)
    if not match:raise ValueError('Invalid place photo reference')
    async with httpx.AsyncClient(timeout=25,follow_redirects=False) as client:
        data=await place_photos(client,match[1],limit=1,selector=match[2])
        if not data['photos']:raise ValueError('Selected place photo is no longer available')
        async with client.stream('GET',data['photos'][0]['imageUrl']) as response:
            response.raise_for_status();raw=bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw)>3_000_000:raise ValueError('Place photo exceeds limit')
    if raw.startswith(b'\xff\xd8\xff'):mime='image/jpeg'
    elif raw.startswith(b'\x89PNG\r\n\x1a\n'):mime='image/png'
    elif raw[:4]==b'RIFF' and raw[8:12]==b'WEBP':mime='image/webp'
    else:raise ValueError('Unsupported place photo type')
    return f'data:{mime};base64,'+base64.b64encode(raw).decode()
