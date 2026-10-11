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
        if not req.url.path.endswith('/media'):
            assert req.headers['X-Goog-FieldMask']=='id,photos'
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
    assert photo['capabilities']=={'viewable':True,'scorable':True,'selfieBackground':True,'adjustableView':False}
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


def test_place_photo_candidates_keep_poi_but_not_a_camera_heading(monkeypatch):
    async def fetch(client,place_id,**kwargs):
        return {'title':'Cafe','sourceUrl':'https://maps.google.com/place','photos':[
            {'photoReference':'google-place-photo://abc/'+'a'*64,'id':'photo:1',
             'authorAttributions':[{'displayName':'Photographer'}],
             'locationType':'place_association_not_verified_camera_position'}]}
    monkeypatch.setattr(place_photos,'place_photos',fetch)
    poi={'id':'google:abc','name':'Cafe','lat':40,'lon':-96}
    rows=asyncio.run(place_photos.candidates(None,[poi,{'id':'osm:1'}]))
    assert len(rows)==1 and rows[0]['poi']==poi
    assert rows[0]['viewHeadingDegrees'] is None and rows[0]['cacheable'] is False
    assert rows[0]['imageUrl'].startswith('google-place-photo://')
    assert 'googleusercontent' not in json.dumps(rows)


def test_place_photo_selector_does_not_silently_switch_to_another_photo(monkeypatch):
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_API_KEY','fixture')
    calls=[]
    def handle(req):
        calls.append(req)
        return httpx.Response(200,json={'id':'abc','photos':[{'name':'places/abc/photos/changed'}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
            return await place_photos.place_photos(c,'abc',selector='a'*64)
    assert asyncio.run(run())['photos']==[] and len(calls)==1


def test_new_background_does_not_restrict_existing_google_street_view():
    from agentic_services.photo_scout.portraits import background_reference
    assert background_reference('google-street-view','https://www.google.com/maps/@?map_action=pano&pano=abc&heading=90')=='google-streetview://abc/90'
    reference='google-place-photo://abc/'+'a'*64
    assert background_reference('google-places-photos',reference)==reference


def test_place_photo_signed_preview_and_portrait_submission(tmp_path,monkeypatch):
    import base64,io
    from PIL import Image
    from fastapi.testclient import TestClient
    from agentic_services.config import Settings
    from agentic_services.main import create_app
    from agentic_services.photo_scout import routes
    reference='google-place-photo://abc/'+'a'*64
    image=io.BytesIO();Image.new('RGB',(32,32),'green').save(image,format='PNG')
    data='data:image/png;base64,'+base64.b64encode(image.getvalue()).decode()
    calls=[]
    async def fetch(ref):calls.append(ref);return data
    monkeypatch.setattr(routes,'image_data',fetch)
    settings=Settings(openai_api_key='fixture',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test',service_api_key='private')
    client=TestClient(create_app(settings=settings),base_url='https://api.test')
    auth={'Authorization':'Bearer private'}
    response=client.post('/photo-scout/v1/thumbnails',json={'sourceUrls':[reference]},headers=auth)
    assert response.status_code==200
    url=response.json()['imageUrls'][0]
    assert 'googleusercontent' not in url
    preview=client.get(url,headers=auth)
    assert preview.status_code==200 and preview.content==image.getvalue()
    assert preview.headers['content-type']=='image/png' and calls==[reference]
    assert client.get(url.replace('signature=','signature=x'),headers=auth).status_code==403
    job=client.post('/photo-scout/v1/portraits',headers=auth,json={
        'portrait':data,'background':reference,'provider':'google-places-photos','place':'Cafe'})
    assert job.status_code==202


def test_photo_selector_survives_resource_rotation_but_rejects_ambiguity(monkeypatch):
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_API_KEY','fixture')
    photo={'name':'places/abc/photos/old','widthPx':800,'heightPx':600,
           'authorAttributions':[{'displayName':'Photographer','uri':'https://maps.google.com/contrib/1'}]}
    selector=place_photos.photo_selector(photo)
    changed={**photo,'name':'places/abc/photos/new'}
    assert place_photos.photo_selector(changed)==selector
    def handle(req):
        if req.url.path.endswith('/media'):return httpx.Response(200,json={'photoUri':'https://lh3.googleusercontent.com/photo'})
        return httpx.Response(200,json={'id':'abc','photos':[changed]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
            return await place_photos.place_photos(c,'abc',selector=selector)
    assert len(asyncio.run(run())['photos'])==1
    def ambiguous(req):return httpx.Response(200,json={'id':'abc','photos':[changed,{**changed,'name':'places/abc/photos/another'}]})
    async def run_ambiguous():
        async with httpx.AsyncClient(transport=httpx.MockTransport(ambiguous)) as c:
            return await place_photos.place_photos(c,'abc',selector=selector)
    assert asyncio.run(run_ambiguous())['photos']==[]


def test_regional_photo_downloads_only_retained_image_with_failure_fallback(monkeypatch):
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_API_KEY','test')
    calls=[];fail_first=False
    async def get_json(client,url,params=None,headers=None):
        calls.append(url)
        if url.endswith('/abc'):
            assert headers['X-Goog-FieldMask']=='id,photos'
            return {'id':'abc','photos':[
                {'name':'places/abc/photos/'+str(i),'widthPx':400+i,'heightPx':300}
                for i in range(2)]}
        if fail_first and '/0/media' in url:raise ValueError('Temporary media failure')
        return {'photoUri':'https://lh3.googleusercontent.com/image'}
    monkeypatch.setattr(place_photos,'get_json',get_json)
    poi={'id':'google:abc','name':'Original name','lat':40,'lon':-96,
         'sourceUrl':'https://www.google.com/maps/search/?api=1&query_place_id=abc'}
    rows=asyncio.run(place_photos.candidates(None,[poi]))
    assert len(rows)==1 and len(calls)==2
    assert rows[0]['title']=='Original name' and rows[0]['sourceUrl']==poi['sourceUrl']
    calls.clear();fail_first=True
    rows=asyncio.run(place_photos.candidates(None,[poi]))
    assert len(rows)==1 and len(calls)==3
    assert rows[0]['id']!='' and rows[0]['poi']==poi
