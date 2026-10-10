"""On-demand Places photo probe/display adapter, separate from scoring and composites.

Photo resources and media URLs are transient. Fetch names fresh on every invocation;
never put them in durable reports, score caches, or portrait jobs. A POI association
is not the camera position. This adapter is not registered as a scoring source.
"""
import asyncio
import hashlib
import os
import re
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


async def place_photos(client, place_id, *, limit=3, width=800):
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
        return {'id':'google-place-photo:'+hashlib.sha256(url.encode()).hexdigest()[:24],
            'provider':'google-places-photos','imageUrl':url,'authorAttributions':authors,
            'originalWidth':photo.get('widthPx'),'originalHeight':photo.get('heightPx'),
            'locationType':'place_association_not_verified_camera_position',
            'capabilities':{'viewable':True,'scorable':False,'selfieBackground':False,'adjustableView':False}}
    photos=await asyncio.gather(*(media(p) for p in data.get('photos',[])[:limit] if isinstance(p,dict)),return_exceptions=True)
    return {'placeId':place_id,'title':text(data.get('displayName',{}).get('text')),
        'sourceUrl':attribution_url(data.get('googleMapsUri')),
        'photos':[p for p in photos if isinstance(p,dict)],'availablePhotos':len(data.get('photos',[])),
        'cachePolicy':'no-store','attribution':'Google Maps','usage':'display-evaluation-only'}
