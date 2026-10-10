import asyncio,json
import httpx
import pytest
from agentic_services.photo_scout import sources,place_photos


def test_mapillary_pagination_preserves_capture_metadata_and_rejects_unusable_images(monkeypatch):
    monkeypatch.setenv('PHOTO_SCOUT_MAPILLARY_TOKEN','secret-test-token')
    calls=[]
    def row(id,lat=40,**extra):
        return {'id':str(id),'geometry':{'coordinates':[-96,lat]},'creator':{'username':'Photographer'},
                'thumb_1024_url':'https://images.fbcdn.net/photo.jpg','camera_type':'perspective',
                'compass_angle':405,'captured_at':1700000000000,'sequence':'seq',**extra}
    def handle(req):
        calls.append(req)
        assert req.url.host=='graph.mapillary.com'
        assert req.headers['Authorization']=='OAuth secret-test-token'
        if not req.url.params.get('after'):
            return httpx.Response(200,json={'data':[row(1),row(2,41),row(3,camera_type='spherical'),row(4,thumb_1024_url='https://evil.test/a')],
                'paging':{'cursors':{'after':'second'},'next':'https://evil.test/?access_token=secret-test-token'}})
        return httpx.Response(200,json={'data':[row(1),row(5)],'paging':{}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
            return await sources.mapillary(c,40,-96,1000)
    rows=asyncio.run(run())
    assert [r['id'] for r in rows]==['mapillary:1','mapillary:5']
    assert rows[0]['viewHeadingDegrees']==45
    assert rows[0]['capturedAt'].startswith('2023-11-14')
    assert rows[0]['sequenceId']=='seq'
    assert 'secret-test-token' not in json.dumps(rows)
    assert len(calls)==2


def test_mapillary_missing_token_does_not_call_provider(monkeypatch):
    monkeypatch.delenv('PHOTO_SCOUT_MAPILLARY_TOKEN',raising=False)
    assert asyncio.run(sources.mapillary(None,40,-96,1000))==[]


def test_place_photos_fetches_fresh_names_and_retains_authorship_without_leaking_key(monkeypatch):
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_API_KEY','secret-test-key')
    calls=[]
    def handle(req):
        calls.append(req)
        assert req.headers['X-Goog-Api-Key']=='secret-test-key'
        assert 'secret-test-key' not in str(req.url)
        if req.url.path.endswith('/media'):
            assert req.url.params['skipHttpRedirect']=='true'
            return httpx.Response(200,json={'photoUri':'https://lh3.googleusercontent.com/valid'})
        return httpx.Response(200,json={'id':'abc','displayName':{'text':'A place'},'googleMapsUri':'https://maps.google.com/place',
            'photos':[{'name':'places/abc/photos/fresh','widthPx':3000,'heightPx':2000,
                'authorAttributions':[{'displayName':'A photographer','uri':'//maps.google.com/contrib/1'}]}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
            return [await place_photos.place_photos(c,'google:abc') for _ in range(2)]
    data=asyncio.run(run())
    assert len(calls)==4 # Even the second invocation obtains fresh photo names.
    photo=data[0]['photos'][0]
    assert photo['authorAttributions']==[{'displayName':'A photographer','uri':'https://maps.google.com/contrib/1'}]
    assert photo['locationType']=='place_association_not_verified_camera_position'
    assert photo['capabilities']=={'viewable':True,'scorable':False,'selfieBackground':False,'adjustableView':False}
    assert data[0]['cachePolicy']=='no-store'
    assert 'secret-test-key' not in json.dumps(data)


@pytest.mark.parametrize('url',['http://lh3.googleusercontent.com/a','https://googleusercontent.com.evil.test/a','https://user@lh3.googleusercontent.com/a','https://lh3.googleusercontent.com:8443/a','https://lh3.googleusercontent.com:bad/a'])
def test_place_photo_media_host_rejects_untrusted_urls(url):
    assert not place_photos.photo_media_host(url)


def test_place_photo_rejects_cross_place_resource_and_unsafe_media(monkeypatch):
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_API_KEY','fixture')
    def handle(req):
        if req.url.path.endswith('/media'):return httpx.Response(200,json={'photoUri':'https://evil.test/image'})
        return httpx.Response(200,json={'id':'abc','photos':[{'name':'places/other/photos/wrong'},{'name':'places/abc/photos/unsafe'}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
            return await place_photos.place_photos(c,'abc')
    assert asyncio.run(run())['photos']==[]
    with pytest.raises(ValueError,match='Invalid Google place ID'):
        asyncio.run(place_photos.place_photos(None,'../api'))
