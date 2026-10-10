"""Bounded real-source smoke probe. Prints safe counts, never keys or media URLs.

No model calls, no image persistence, no changes to active production providers.
Run in the configured service environment; existing source credentials are reused.
"""
import argparse
import asyncio
import io
import json
import os
import httpx
from PIL import Image
from agentic_services.photo_scout.places import search_text
from agentic_services.photo_scout.place_photos import place_photos,photo_media_host
from agentic_services.photo_scout.sources import mapillary


async def main(args):
    async with httpx.AsyncClient(timeout=20,follow_redirects=False) as client:
        if not os.getenv('PHOTO_SCOUT_MAPILLARY_TOKEN'):
            print(json.dumps({'provider':'mapillary','status':'not_configured'}))
        else:
            try:
                rows=await mapillary(client,args.lat,args.lon,args.radius)
                print(json.dumps({'provider':'mapillary','status':'ok','eligibleImages':len(rows),
                    'imagesWithDirection':sum(r.get('viewHeadingDegrees') is not None for r in rows)}))
            except Exception as e:print(json.dumps({'provider':'mapillary','status':'unavailable','errorType':type(e).__name__}))
        try:
            locations=await search_text(args.query,limit=1)
            if not locations:
                print(json.dumps({'provider':'google-places-photos','status':'no_place'}));return
            row=locations[0]
            data=await place_photos(client,row['id'],limit=1)
            out={'provider':'google-places-photos','status':'ok','place':data['title'],
                'availablePhotos':data['availablePhotos'],'fetchedPhotos':len(data['photos']),
                'usage':data['usage'],'cachePolicy':data['cachePolicy']}
            if data['photos']:
                photo=data['photos'][0]
                if not photo_media_host(photo['imageUrl']):raise ValueError('Unexpected media host')
                async with client.stream('GET',photo['imageUrl']) as response:
                    response.raise_for_status();raw=bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw)>3_000_000:raise ValueError('Probe image exceeds limit')
                with Image.open(io.BytesIO(raw)) as im:out.update(imageDimensions=list(im.size),imageFormat=im.format)
                out['authors']=[a['displayName'] for a in photo['authorAttributions']]
                out['capabilities']=photo['capabilities']
            print(json.dumps(out))
        except Exception as e:
            print(json.dumps({'provider':'google-places-photos','status':'unavailable','errorType':type(e).__name__}))
            raise SystemExit(1) from None

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--query',default='Battery Spencer, California')
    p.add_argument('--lat',type=float,default=37.82776);p.add_argument('--lon',type=float,default=-122.48167)
    p.add_argument('--radius',type=int,default=5000)
    asyncio.run(main(p.parse_args()))
