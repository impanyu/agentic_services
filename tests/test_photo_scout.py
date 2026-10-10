import pytest
pytestmark = pytest.mark.usefixtures("stub_photo_route_intent")
import asyncio
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from agentic_services.config import Settings
from agentic_services.main import create_app
from agentic_services.photo_scout.scoring import VisualChoice,VisualResult,validate_result
from agentic_services.photo_scout.routes import ExploreRequest,PhotoStore,create_photo_router
from agentic_services.photo_scout.sources import commons,distance,image_host
from agentic_services.storage import VerificationStore


def test_coordinates_and_image_hosts():
    for changes in ({'lat':float('nan')},{'lon':181},{'radius':20001},{'lat':86}):
        with pytest.raises(ValueError): ExploreRequest(**({'lat':0,'lon':0}|changes))
    assert distance((0,179.999),(0,-179.999))<230
    assert image_host('https://upload.wikimedia.org/a.jpg')
    assert not image_host('https://upload.wikimedia.org.evil.test/a.jpg')
    assert not image_host('http://127.0.0.1/a.jpg')
    assert not image_host('https://user@upload.wikimedia.org/a.jpg')


def test_unseen_images_and_duplicate_spots_rejected():
    rows=[{'id':str(i),'lat':0,'lon':i*.0001} for i in range(3)]
    choices=[VisualChoice(image_id=str(i),name='Place',score=90-i,visible_evidence='Trees',photo_tip='Frame trees',uncertainty='Access unknown',confidence='medium') for i in range(3)]
    r=VisualResult(spots=choices,summary='Nearby')
    assert [s['id'] for s in validate_result(r,rows,{'1','2'},3)]==['1']
    assert not validate_result(r,rows,set(),3)


def test_commons_keeps_attribution_rejects_unknown_license():
    def handler(request):
        if request.url.params.get('list')=='geosearch':
            return httpx.Response(200,json={'query':{'geosearch':[{'pageid':i,'title':'File:Park.jpg','lat':0,'lon':i*.001} for i in [1,2]]}})
        return httpx.Response(200,json={'query':{'pages':{str(i):{'imageinfo':[{'thumburl':'https://upload.wikimedia.org/park.jpg','descriptionurl':'https://commons.wikimedia.org/wiki/File:Park.jpg','extmetadata':{'LicenseShortName':{'value':'CC BY-SA 4.0' if i==1 else 'Unknown'},'Artist':{'value':'<b>Artist</b>'},'LicenseUrl':{'value':'https://creativecommons.org/licenses/by-sa/4.0/'}}}]} for i in [1,2]}}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c: return await commons(c,0,0,1000)
    rows=asyncio.run(run());assert len(rows)==1;assert rows[0]['author']=='Artist'


def test_discovery_auth_and_private_report(tmp_path,monkeypatch):
    monkeypatch.setenv('PHOTO_SCOUT_ENABLED','1')
    settings=Settings(openai_api_key=None,openai_model='test',database_path=tmp_path/'db',base_url='https://api.test',service_api_key='private')
    client=TestClient(create_app(settings=settings))
    assert client.post('/photo-scout/v1/discover',json={'lat':0,'lon':0}).status_code==401
    assert client.post('/photo-scout/v1/discover',json={'lat':0,'lon':0},headers={'Authorization':'Bearer private'}).status_code==503
    store=PhotoStore(settings.database_path);job,token=store.create(ExploreRequest(lat=0,lon=0),200)
    store.update(job,state='complete',result='{"spots":[]}')
    assert client.get('/photo-scout/v1/report/'+job).status_code==404
    result=client.get('/photo-scout/v1/report/'+job,headers={'X-Report-Token':token})
    assert result.status_code==200;assert result.headers['cache-control']=='private, no-store'
    assert client.get('/photo-scout/openapi.json').status_code==200


def test_payment_amount_and_unpaid_rejected(tmp_path,monkeypatch):
    from fastapi import FastAPI
    import agentic_services.photo_scout.routes as routes
    monkeypatch.setenv('CONTRACTOR_STRIPE_SECRET_KEY','sk_test_fixture')
    settings=Settings(openai_api_key=None,openai_model='test',database_path=tmp_path/'db',base_url='https://api.test')
    vs=VerificationStore(settings.database_path)
    router,retrieve,fulfill=create_photo_router(settings,lambda x:None,vs)
    store=PhotoStore(settings.database_path);job,token=store.create(ExploreRequest(lat=0,lon=0),200)
    store.update(job,session='cs_test_abc')
    session={'id':'cs_test_abc','client_reference_id':job,'metadata':{'serviceId':'photo-scout'},'currency':'usd','amount_total':200,'mode':'payment','payment_status':'paid','livemode':False}
    real=httpx.AsyncClient
    monkeypatch.setattr(routes.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=session))))
    assert asyncio.run(retrieve('cs_test_abc'))['id']=='cs_test_abc'
    for key,value in [('amount_total',1),('payment_status','unpaid'),('livemode',True)]:
        old=session[key];session[key]=value
        with pytest.raises(Exception) as err: asyncio.run(retrieve('cs_test_abc'))
        assert err.value.status_code==402
        session[key]=old


def test_daily_budget_is_persistent(tmp_path,monkeypatch):
    monkeypatch.setenv('PHOTO_SCOUT_DAILY_RUN_LIMIT','1')
    PhotoStore(tmp_path/'db').reserve_run()
    with pytest.raises(Exception) as error: PhotoStore(tmp_path/'db').reserve_run()
    assert error.value.status_code==429


def test_paid_fulfillment_is_reused(tmp_path,monkeypatch):
    import agentic_services.photo_scout.routes as routes
    monkeypatch.setenv('PHOTO_SCOUT_ENABLED','1')
    settings=Settings(openai_api_key='test',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test')
    vs=VerificationStore(settings.database_path)
    router,retrieve,fulfill=create_photo_router(settings,lambda x:None,vs)
    store=PhotoStore(settings.database_path);job,token=store.create(ExploreRequest(lat=0,lon=0),200)
    async def images(*a,**kw): return [{'id':'test'}],{'test':{'status':'ok'}}
    async def pois(*a): return [],{'status':'ok'}
    calls=[]
    async def model(*a): calls.append(1);return {'spots':[],'sources':{},'summary':'No good images'}
    monkeypatch.setattr(routes,'candidates',images);monkeypatch.setattr(routes,'nearby_pois',pois);monkeypatch.setattr(routes,'explore',model)
    session={'client_reference_id':job}
    asyncio.run(fulfill(session));asyncio.run(fulfill(session))
    assert len(calls)==1;assert store.get(job)['state']=='complete'


def test_shared_webhook_queues_photo_delivery_without_success_page(tmp_path,monkeypatch):
    import hashlib,hmac,json,time
    import agentic_services.photo_scout.routes as routes
    monkeypatch.setenv('PHOTO_SCOUT_ENABLED','1')
    monkeypatch.setenv('CONTRACTOR_STRIPE_SECRET_KEY','sk_test_fixture')
    monkeypatch.setenv('HUMAN_STRIPE_WEBHOOK_SECRET','whsec_fixture')
    settings=Settings(openai_api_key='test',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test',service_api_key='private')
    app=create_app(settings=settings);client=TestClient(app)
    store=PhotoStore(settings.database_path);job,token=store.create(ExploreRequest(lat=0,lon=0),200)
    store.update(job,session='cs_test_abc')
    session={'id':'cs_test_abc','client_reference_id':job,'metadata':{'serviceId':'photo-scout'},'currency':'usd','amount_total':200,'mode':'payment','payment_status':'paid','livemode':False}
    real=httpx.AsyncClient
    monkeypatch.setattr(routes.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=session))))
    async def images(*a,**kw): return [{'id':'test'}],{'test':{'status':'ok'}}
    async def pois(*a): return [],{'status':'ok'}
    calls=[]
    async def model(*a): calls.append(1);return {'spots':[],'sources':{},'summary':'No good images'}
    monkeypatch.setattr(routes,'candidates',images);monkeypatch.setattr(routes,'nearby_pois',pois);monkeypatch.setattr(routes,'explore',model)
    event={'type':'checkout.session.completed','data':{'object':session}}
    body=json.dumps(event).encode();stamp=str(int(time.time()))
    sig=hmac.new(b'whsec_fixture',stamp.encode()+b'.'+body,hashlib.sha256).hexdigest()
    assert client.post('/v1/stripe/checkout-webhook',content=body).status_code==400
    for _ in range(2):
        response=client.post('/v1/stripe/checkout-webhook',content=body,headers={'Stripe-Signature':f't={stamp},v1={sig}'})
        assert response.json()=={'status':'queued'}
        asyncio.run(app.state.process_stripe_fulfillment())
    assert calls==[1]
    assert store.get(job)['state']=='complete'


def test_multisource_sample_keeps_both_providers_at_shared_points():
    from agentic_services.photo_scout.sources import diverse_sample
    rows=[{'id':str(i),'provider':'commons','lat':0,'lon':i*.001,'distanceMeters':i} for i in range(15)]
    rows += [{'id':'street','provider':'panoramax','lat':0,'lon':0,'distanceMeters':500}]
    sample=diverse_sample(rows)
    assert len(sample)==12
    assert sample[1]['id']=='street'


def test_panoramax_excludes_unknown_licenses_and_image_hosts():
    from agentic_services.photo_scout.sources import panoramax
    import uuid
    def feature(license,host):
        return {'id':str(uuid.uuid4()),'geometry':{'type':'Point','coordinates':[2.295,48.855]},
            'properties':{'license':license,'datetime':'2025-01-01T00:00:00Z'},
            'providers':[{'name':'City photographer','roles':['producer']}],
            'assets':{'sd':{'href':f'https://{host}/api/image.jpg'}}}
    data={'features':[feature('etalab-2.0','panoramax.ign.fr'),feature('proprietary','panoramax.ign.fr'),feature('CC-BY-SA-4.0','evil.test')]}
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=data))) as c:
            return await panoramax(c,48.855,2.295,700)
    rows=asyncio.run(run());assert len(rows)==1
    assert rows[0]['author']=='City photographer';assert rows[0]['capturedAt']=='2025-01-01T00:00:00Z'


def test_image_redirects_only_follow_verified_panoramax_hosts(monkeypatch):
    import agentic_services.photo_scout.sources as sources
    real=httpx.AsyncClient
    calls=[]
    def handler(r):
        calls.append(str(r.url))
        if r.url.host=='panoramax.ign.fr':
            return httpx.Response(302,headers={'Location':'https://panoramax-storage-public-fast.s3.gra.perf.cloud.ovh.net/sd.jpg'})
        return httpx.Response(200,content=b'\xff\xd8\xfftest')
    monkeypatch.setattr(sources.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler)))
    assert asyncio.run(sources.image_data('https://panoramax.ign.fr/api/pic.jpg')).startswith('data:image/jpeg;')
    assert len(calls)==2
    def hostile(r): return httpx.Response(302,headers={'Location':'http://127.0.0.1/secret'})
    monkeypatch.setattr(sources.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(hostile)))
    with pytest.raises(ValueError): asyncio.run(sources.image_data('https://panoramax.ign.fr/api/pic.jpg'))


def test_google_candidates_keep_angles_without_credentials(monkeypatch):
    import json
    from agentic_services.photo_scout.sources import google_streetview,diverse_sample
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_API_KEY','secret-fixture')
    data={'status':'OK','pano_id':'pano_fixture','location':{'lat':0,'lng':0},'date':'2025-10','copyright':'Google'}
    def handler(request):
        assert 'source' not in request.url.params, 'provider-supported indoor views must not be excluded'
        return httpx.Response(200,json=data)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await google_streetview(c,0,0,1000)
    rows=asyncio.run(run())
    assert len(rows)==8 and {r['viewHeadingDegrees'] for r in rows}==set(range(0,360,45))
    assert all(r['viewFovDegrees']==120 for r in rows)
    assert all(r['imageUrl'].endswith('/0/120') and 'fov=120' in r['sourceUrl'] for r in rows)
    assert {r['viewPitchDegrees'] for r in rows}=={0}
    assert all('pitch=0' in r['sourceUrl'] for r in rows)
    assert 'secret-fixture' not in json.dumps(rows)
    for r in rows:r['distanceMeters']=0
    assert len(diverse_sample(rows))==8
    choices=VisualResult(spots=[VisualChoice(image_id=rows[0]['id'],name='Park',score=80,visible_evidence='Trees',photo_tip='Frame trees',uncertainty='Old image',confidence='medium')],summary='Park')
    result=validate_result(choices,rows,{rows[0]['id']},3)
    assert result[0]['imageUrl'] is None and 'heading=0' in result[0]['sourceUrl']


def test_google_image_budget_and_reference_validation(tmp_path,monkeypatch):
    import agentic_services.photo_scout.sources as sources
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_ENABLED','1')
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_API_KEY','secret-fixture')
    monkeypatch.setenv('WEB_EVIDENCE_DB',str(tmp_path/'db'))
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_DAILY_IMAGE_LIMIT','1')
    requests=[]
    def handler(r):
        requests.append(r)
        assert r.url.host=='maps.googleapis.com'
        assert r.url.params['heading']=='90'
        return httpx.Response(200,content=b'\xff\xd8\xfftest')
    real=httpx.AsyncClient
    monkeypatch.setattr(sources.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler)))
    with pytest.raises(ValueError): asyncio.run(sources.google_image_data('google-streetview://evil.example/path?key=bad'))
    assert not requests
    assert asyncio.run(sources.google_image_data('google-streetview://fixture/90')).startswith('data:image/jpeg;base64,')
    with pytest.raises(ValueError): asyncio.run(sources.google_image_data('google-streetview://fixture/90'))
    assert len(requests)==1


@pytest.mark.parametrize('configured',[None,'0'])
def test_google_image_development_requests_continue_past_existing_count(tmp_path,monkeypatch,configured):
    import sqlite3
    from datetime import datetime,timezone
    import agentic_services.photo_scout.sources as sources
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_ENABLED','1')
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_API_KEY','secret-fixture')
    monkeypatch.setenv('WEB_EVIDENCE_DB',str(tmp_path/'db'))
    if configured is None:monkeypatch.delenv('PHOTO_SCOUT_GOOGLE_DAILY_IMAGE_LIMIT',raising=False)
    else:monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_DAILY_IMAGE_LIMIT',configured)
    day=datetime.now(timezone.utc).date().isoformat()
    with sqlite3.connect(tmp_path/'db') as db:
        db.execute('CREATE TABLE photo_scout_google_budget(day TEXT PRIMARY KEY,requests INTEGER NOT NULL)')
        db.execute('INSERT INTO photo_scout_google_budget VALUES(?,180)',(day,))
    requests=[]
    def handler(request):requests.append(request);return httpx.Response(200,content=b'\xff\xd8\xfffixture')
    real=httpx.AsyncClient
    monkeypatch.setattr(sources.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler)))
    for _ in range(2):assert asyncio.run(sources.google_image_data('google-streetview://fixture/90')).startswith('data:image/jpeg;base64,')
    assert len(requests)==2
    with sqlite3.connect(tmp_path/'db') as db:assert db.execute('SELECT requests FROM photo_scout_google_budget WHERE day=?',(day,)).fetchone()[0]==182


def test_google_sampling_deduplicates_nearby_camera_points(monkeypatch):
    from agentic_services.photo_scout.sources import google_streetview,google_sampling_spacing,diverse_sample
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_API_KEY','secret-fixture')
    positions=iter([(0,0),(0.0001,0),(0.003,0),(0,-0.003),(0,0.0001)])
    def handler(request):
        lat,lon=next(positions,(0,0))
        return httpx.Response(200,json={'status':'OK','pano_id':f'pano_{lat}_{lon}'.replace('.','_'),
            'location':{'lat':lat,'lng':lon}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await google_streetview(c,0,0,1000)
    rows=asyncio.run(run())
    points={(r['lat'],r['lon']) for r in rows}
    assert points=={(0,0),(0.003,0),(0,-0.003)}
    assert len(rows)==24
    assert google_sampling_spacing(100)==80 and google_sampling_spacing(1000)==200 and google_sampling_spacing(5000)==250
    for r in rows:r['distanceMeters']=round(distance((0,0),(r['lat'],r['lon'])))
    sampled=diverse_sample(rows,3)
    assert len({(r['lat'],r['lon']) for r in sampled})==3


def test_google_query_grid_covers_area_and_limits_concurrency(monkeypatch):
    from agentic_services.photo_scout.sources import google_query_points,google_streetview
    points=google_query_points(0,0,1000)
    assert len(points)==50 and points[0]==(0,0)
    assert all(distance((0,0),p)<1010 for p in points)
    assert any(p[0]>0 and p[1]>0 for p in points)
    assert any(p[0]<0 and p[1]<0 for p in points)
    assert len(google_query_points(0,0,100))==5
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_API_KEY','secret-fixture')
    active=0; peak=0; requests=0
    async def handler(request):
        nonlocal active,peak,requests
        active+=1;peak=max(peak,active);requests+=1
        await asyncio.sleep(.001)
        active-=1
        return httpx.Response(200,json={'status':'ZERO_RESULTS'})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await google_streetview(c,0,0,1000)
    assert asyncio.run(run())==[]
    assert requests==50 and peak<=24


def test_free_website_mode_auth_payment_and_budget(tmp_path,monkeypatch):
    import agentic_services.photo_scout.routes as routes
    monkeypatch.setenv('PHOTO_SCOUT_ENABLED','1')
    monkeypatch.setenv('PHOTO_SCOUT_HUMAN_FREE_PREVIEW','0')
    monkeypatch.setenv('PHOTO_SCOUT_PRICE_CENTS','200')
    monkeypatch.setenv('PHOTO_SCOUT_DAILY_RUN_LIMIT','1')
    settings=Settings(openai_api_key='test',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test',service_api_key='private')
    client=TestClient(create_app(settings=settings))
    payload={'lat':0,'lon':0};headers={'Authorization':'Bearer private'}
    assert client.post('/photo-scout/v1/preview',json=payload).status_code==401
    assert client.post('/photo-scout/v1/preview',json=payload,headers=headers).status_code==403
    monkeypatch.setenv('PHOTO_SCOUT_HUMAN_FREE_PREVIEW','1')
    assert client.get('/photo-scout/v1/status').json()['humanPriceUsd']=='0.00'
    assert client.get('/photo-scout/v1/status').json()['humanFreePreview'] is True
    async def images(*a,**kw): return [{'id':'test'}],{'test':{'status':'ok'}}
    async def pois(*a): return [],{'status':'ok'}
    calls=[]
    async def model(*a): calls.append(1);return {'spots':[],'sources':{},'summary':'No good images'}
    monkeypatch.setattr(routes,'candidates',images)
    monkeypatch.setattr(routes,'nearby_pois',pois)
    monkeypatch.setattr(routes,'explore',model)
    # No Stripe client should ever be constructed in free mode.
    monkeypatch.setattr(routes.httpx,'AsyncClient',lambda **kw:pytest.fail('Unexpected payment request'))
    assert client.post('/photo-scout/v1/checkout',json=payload,headers=headers).status_code==409
    result=client.post('/photo-scout/v1/preview',json=payload,headers=headers)
    assert result.status_code==200 and result.json()['summary']=='No good images'
    assert client.post('/photo-scout/v1/preview',json=payload,headers=headers).status_code==429
    assert calls==[1]


def test_osm_poi_categories_and_fallback(monkeypatch):
    import agentic_services.photo_scout.sources as sources
    requests=[]
    def handler(r):
        requests.append(r)
        assert 'nature_reserve' in r.url.params['data'] and 'park' in r.url.params['data']
        assert '[maxsize:16777216]' in r.url.params['data']
        if len(requests)==1: return httpx.Response(503)
        return httpx.Response(200,json={'elements':[{'type':'node','id':1,'lat':0,'lon':0.001,'tags':{'name':'Park','leisure':'park'}}]})
    real=httpx.AsyncClient
    monkeypatch.setattr(sources.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler)))
    rows,status=asyncio.run(sources.nearby_pois(0,0,1000))
    assert rows[0]['category']=='park' and rows[0]['id']=='osm:node:1'
    assert status['status']=='ok' and status['attempts'][0]['httpStatus']==503
    assert len(requests)==2


def test_google_targets_poi_and_points_camera_at_it(monkeypatch):
    from agentic_services.photo_scout.sources import google_streetview
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_API_KEY','secret-fixture')
    targets=[{'id':'osm:node:1','name':'Park','lat':0.001,'lon':0}]
    requests=[]
    def handler(r):
        requests.append(r)
        assert r.url.params['location']=='0.001,0'
        return httpx.Response(200,json={'status':'OK','pano_id':'fixture','location':{'lat':0,'lng':0}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await google_streetview(c,0,0,1000,targets)
    rows=asyncio.run(run())
    assert len(requests)==1 and len(rows)==8
    assert {r['viewHeadingDegrees'] for r in rows}==set(range(0,360,45))
    assert all(r['poi']['id']=='osm:node:1' for r in rows)


def test_poi_first_and_signed_google_image_delivery(tmp_path,monkeypatch):
    import agentic_services.photo_scout.routes as routes
    monkeypatch.setenv('PHOTO_SCOUT_ENABLED','1');monkeypatch.setenv('PHOTO_SCOUT_HUMAN_FREE_PREVIEW','1')
    settings=Settings(openai_api_key='test',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test',service_api_key='private')
    client=TestClient(create_app(settings=settings));calls=[]
    poi={'id':'osm:node:1','lat':0,'lon':0,'name':'Park','category':'park'}
    async def pois(*a):calls.append('pois');return [poi],{'status':'ok','count':1}
    async def images(lat,lon,radius,targets):
        calls.append('images');assert targets==[poi]
        return [{'id':'test'}],{'google-street-view':{'status':'ok'}}
    async def model(*a):
        calls.append('agent');return {'spots':[{'provider':'google-street-view','streetViewReference':'google-streetview://fixture/90','imageUrl':None}], 'sources':{},'summary':'Park'}
    monkeypatch.setattr(routes,'nearby_pois',pois);monkeypatch.setattr(routes,'candidates',images);monkeypatch.setattr(routes,'explore',model)
    result=client.post('/photo-scout/v1/preview',json={'lat':0,'lon':0,'categories':['park']},headers={'Authorization':'Bearer private'})
    assert result.status_code==200 and calls==['pois','images','agent']
    body=result.json();assert body['candidatePoiCount']==1 and body['discoveryMethod']=='fixed-geographic-and-poi'
    image_url=body['spots'][0]['imageUrl'];assert 'private' not in image_url
    image_calls=[]
    async def jpeg(ref):image_calls.append(ref);return 'data:image/jpeg;base64,/9j/dGVzdA=='
    monkeypatch.setattr(routes,'google_image_data',jpeg)
    assert client.get(image_url).status_code==401
    assert client.get(image_url+'&signature=invalid',headers={'Authorization':'Bearer private'}).status_code==403
    response=client.get(image_url,headers={'Authorization':'Bearer private'})
    assert response.status_code==200 and response.headers['cache-control']=='private, no-store'
    assert response.content.startswith(b'\xff\xd8\xff') and image_calls==['google-streetview://fixture/90']


def test_recommendations_deduplicate_same_poi():
    rows=[{'id':str(i),'lat':0,'lon':i*.01,'poi':{'id':'same','name':'Park','lat':0,'lon':0}} for i in range(2)]
    choices=[VisualChoice(image_id=str(i),name='Park',poi_id='same',score=80,visible_evidence='Trees',photo_tip='Frame trees',uncertainty='Unknown',confidence='medium') for i in range(2)]
    assert len(validate_result(VisualResult(spots=choices,summary='Park'),rows,{'0','1'},3))==1


def test_visible_poi_must_be_in_image_candidates():
    park={'id':'park','name':'Verified Park','lat':0,'lon':0}
    rows=[{'id':'image','lat':0,'lon':0,'poi':park,'poiCandidates':[park]}]
    choice=VisualChoice(image_id='image',poi_id='invented',name='Invented',score=90,visible_evidence='Trees',photo_tip='Frame trees',uncertainty='Unknown',confidence='medium')
    assert validate_result(VisualResult(spots=[choice],summary='Park'),rows,{'image'},3)==[]
    choice.poi_id='park'
    out=validate_result(VisualResult(spots=[choice],summary='Park'),rows,{'image'},3)
    assert out[0]['name']=='Verified Park' and out[0]['poi']['id']=='park'




def test_poi_selection_catalog_is_bound_and_restricts_exploration(tmp_path,monkeypatch):
    import agentic_services.photo_scout.routes as routes
    monkeypatch.setenv('PHOTO_SCOUT_ENABLED','1');monkeypatch.setenv('PHOTO_SCOUT_HUMAN_FREE_PREVIEW','1')
    settings=Settings(openai_api_key='test',openai_model='test',base_url='https://api.test',database_path=tmp_path/'db',service_api_key='private')
    client=TestClient(create_app(settings=settings));headers={'Authorization':'Bearer private'};calls=[]
    places=[{'id':str(i),'name':f'Park {i}','lat':0,'lon':i*.001,'category':'park'} for i in range(2)]
    async def pois(*a):calls.append('pois');return places,{'status':'ok','count':2}
    async def images(lat,lon,radius,targets):
        calls.append('images');assert targets==[places[1]]
        return [{'id':'image'}],{'panoramax':{'status':'ok'}}
    async def model(*a):calls.append('agent');return {'spots':[]}
    monkeypatch.setattr(routes,'nearby_pois',pois);monkeypatch.setattr(routes,'candidates',images);monkeypatch.setattr(routes,'explore',model)
    assert client.post('/photo-scout/v1/pois',json={'lat':0,'lon':0,'categories':['park']}).status_code==401
    response=client.post('/photo-scout/v1/pois',json={'lat':0,'lon':0,'categories':['park']},headers=headers)
    assert response.status_code==200 and calls==['pois']
    assert response.headers['cache-control']=='private, no-store'
    token=response.json()['poiCatalogToken']
    payload={'lat':0,'lon':0,'categories':['park'],'selectedPoiIds':['1'],'poiCatalogToken':token}
    for change in ({'selectedPoiIds':[]},{'selectedPoiIds':['1','1']},{'selectedPoiIds':['unknown']},{'lat':1},{'radius':2000},{'poiCatalogToken':token+'x'},{'poiCatalogToken':None}):
        assert client.post('/photo-scout/v1/preview',json={**payload,**change},headers=headers).status_code==422
    assert calls==['pois']
    result=client.post('/photo-scout/v1/preview',json=payload,headers=headers)
    assert result.status_code==200 and result.json()['nearbyPois']==[places[1]]
    assert calls==['pois','images','agent']  # Reuses authenticated catalog, no repeated provider query.
    monkeypatch.setattr(routes.time,'time',lambda: 9999999999)
    assert client.post('/photo-scout/v1/preview',json=payload,headers=headers).status_code==422


def test_category_filter_applied_before_poi_limit(monkeypatch):
    import agentic_services.photo_scout.sources as sources
    requests=[]
    def handler(r):
        query=r.url.params['data'];requests.append(query)
        assert 'park|garden|nature_reserve' in query
        assert 'tourism' not in query and 'historic' not in query
        # Provider results can include unexpected rows; don't let them fill the limit.
        elements=[{'type':'node','id':i,'lat':0,'lon':i*.00001,'tags':{'name':f'Art {i}','tourism':'artwork'}} for i in range(40)]
        elements.append({'type':'node','id':99,'lat':0,'lon':0.001,'tags':{'name':'Garden','leisure':'garden'}})
        return httpx.Response(200,json={'elements':elements})
    real=httpx.AsyncClient
    monkeypatch.setattr(sources.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler)))
    rows,status=asyncio.run(sources.nearby_pois(0,0,1000,['park']))
    assert [row['name'] for row in rows]==['Garden']
    assert rows[0]['categoryGroups']==['park'] and status['count']==1
    assert len(requests)==1


def test_category_api_validation_and_catalog_binding(tmp_path,monkeypatch):
    import agentic_services.photo_scout.routes as routes
    settings=Settings(openai_api_key='test',openai_model='test',base_url='https://api.test',database_path=tmp_path/'db',service_api_key='private')
    client=TestClient(create_app(settings=settings));headers={'Authorization':'Bearer private'};calls=[]
    async def pois(lat,lon,radius,categories):
        calls.append(categories);return [{'id':'park','name':'Park','category':'park'}],{'status':'ok'}
    monkeypatch.setattr(routes,'nearby_pois',pois)
    for categories in ([],['unknown']):
        assert client.post('/photo-scout/v1/pois',headers=headers,json={'lat':0,'lon':0,'categories':categories}).status_code==422
    response=client.post('/photo-scout/v1/pois',headers=headers,json={'lat':0,'lon':0,'categories':['park']})
    assert response.status_code==200 and calls==[['park']]
    payload={'lat':0,'lon':0,'categories':['museum'],'selectedPoiIds':['park'],'poiCatalogToken':response.json()['poiCatalogToken']}
    assert client.post('/photo-scout/v1/candidates',headers=headers,json=payload).status_code==422
    assert calls==[['park']]


def test_photo_mood_maps_to_categories_and_is_bound_to_catalog(tmp_path,monkeypatch):
    import agentic_services.photo_scout.routes as routes
    from agentic_services.photo_scout.styles import PHOTO_STYLES, mapped_categories
    from agentic_services.photo_scout.sources import POI_CATEGORY_FILTERS
    assert all(set(style['categories'])<=set(POI_CATEGORY_FILTERS) for style in PHOTO_STYLES.values())
    assert set(mapped_categories(['nature','waterside']))=={'nature','park','viewpoint'}
    settings=Settings(openai_api_key='test',openai_model='test',base_url='https://api.test',database_path=tmp_path/'db',service_api_key='private')
    monkeypatch.setenv('PHOTO_SCOUT_ENABLED','1');monkeypatch.setenv('PHOTO_SCOUT_HUMAN_FREE_PREVIEW','1')
    client=TestClient(create_app(settings=settings));headers={'Authorization':'Bearer private'};calls=[]
    async def pois(lat,lon,radius,categories):
        calls.append(categories);return [{'id':'park','name':'Park','category':'park','lat':0,'lon':0}],{'status':'ok'}
    async def images(lat,lon,radius,targets):return [{'id':'img','provider':'panoramax','lat':0,'lon':0}],{'panoramax':{'status':'ok'}}
    async def model(settings,payload,rows,statuses):
        assert payload.photoStyles==['waterside'] and payload.categories is None
        return {'spots':[],'summary':'No visible water in the inspected view.'}
    async def geography(*args):return [{'id':'water','name':'Lake','kinds':['waterside'],'geometry':{'type':'LineString','coordinates':[[0,-.001],[0,.001]]}}],[],{'status':'ok'}
    monkeypatch.setattr(routes,'fetch_region',geography)
    monkeypatch.setattr(routes,'nearby_pois',pois);monkeypatch.setattr(routes,'candidates',images);monkeypatch.setattr(routes,'explore',model)
    assert len(client.get('/photo-scout/v1/status').json()['photoStyles'])==8
    for fields in ({'photoStyles':[]},{'photoStyles':['unknown']},{'photoStyles':['nature'],'categories':['park']}):
        assert client.post('/photo-scout/v1/pois',headers=headers,json={'lat':0,'lon':0,**fields}).status_code==422
    response=client.post('/photo-scout/v1/pois',headers=headers,json={'lat':0,'lon':0,'photoStyles':['waterside']})
    assert response.status_code==200 and calls==[['nature','park','viewpoint']]
    payload={'lat':0,'lon':0,'photoStyles':['waterside'],'selectedPoiIds':['park'],'poiCatalogToken':response.json()['poiCatalogToken']}
    # Same source category mapping, different photographic intent: stale selection rejected.
    assert client.post('/photo-scout/v1/preview',headers=headers,json={**payload,'photoStyles':['nature']}).status_code==422
    result=client.post('/photo-scout/v1/preview',headers=headers,json=payload)
    assert result.status_code==200 and result.json()['photoStyles'][0]['label']=='Water & reflections'
    assert calls==[['nature','park','viewpoint']]




def _scoring_client(monkeypatch,parse):
    from types import SimpleNamespace
    import agentic_services.photo_scout.scoring as visual
    class Client:
        def __init__(self,**kw):self.responses=SimpleNamespace(parse=parse)
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
    monkeypatch.setattr(visual,'AsyncOpenAI',Client)


def _scoring_assessment(visual,row,recommend=True):
    return visual.ImageAssessment(image_id=row['id'],poi_id=row['poi']['id'],name=row['poi']['name'],
        score=30+int(row['id'])*5,recommend=recommend,visible_evidence='Visible trees and open water.',
        photo_tip='Frame the water.',uncertainty='Current access unknown.',confidence='medium')


def _scoring_rows():
    return [{'id':str(i),'lat':0,'lon':i*.002,'provider':'test','imageUrl':str(i),
        'poi':{'id':f'poi:{i}','name':f'Place {i}','lat':0,'lon':i*.002}} for i in range(13)]


def test_fixed_pipeline_scores_every_image_and_globally_ranks(tmp_path,monkeypatch):
    monkeypatch.setenv('PHOTO_SCOUT_SCORING_BATCH_SIZE','6')
    import json
    from types import SimpleNamespace
    import agentic_services.photo_scout.scoring as visual
    settings=Settings(openai_api_key='test',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test')
    downloaded=[];seen=[];active=0;peak=0
    async def image(url):downloaded.append(url);return 'data:image/jpeg;base64,/9j/dGVzdA=='
    async def parse(**kw):
        nonlocal active,peak
        assert 'tools' not in kw and kw['store'] is False
        assert 'EVERY supplied image' in kw['instructions']
        assert '8-16 words' in kw['instructions']
        assert 'retain the full' in kw['instructions']
        content=kw['input'][0]['content'];request=json.loads(content[0]['text'])
        assert request['photoStyleBriefs'][0]['label']=='Water & reflections'
        assert request['request']['poiQueries']==['coffee shops']
        assert request['request']['geographicKinds']==['lake']
        assert request['request']['scoringIntent']=='waterside coffee shops with outdoor seating'
        assert 'poiCatalogToken' not in request['request']
        rows=[json.loads(c['text'])['image'] for c in content[1:] if c['type']=='input_text']
        assert 1<=len(rows)<=6 and sum(c['type']=='input_image' for c in content)==len(rows)
        seen.extend(row['id'] for row in rows);active+=1;peak=max(peak,active)
        await asyncio.sleep(0);active-=1
        return SimpleNamespace(output_parsed=visual.VisualBatch(assessments=[_scoring_assessment(visual,r) for r in rows]),usage=SimpleNamespace(input_tokens=10,output_tokens=20))
    _scoring_client(monkeypatch,parse);monkeypatch.setattr(visual,'image_data',image)
    result=asyncio.run(visual.explore(settings,ExploreRequest(lat=0,lon=0,photoStyles=['waterside'],geographicKinds=['lake'],poiQueries=['coffee shops'],scoringIntent='waterside coffee shops with outdoor seating',poiCatalogToken='secret-token'),_scoring_rows(),{}))
    assert set(downloaded)==set(seen)=={str(i) for i in range(13)}
    assert result['inspectedImages']==13 and len(result['imageAssessments'])==13
    assert [spot['image_id'] for spot in result['spots']]==[str(i) for i in range(12,-1,-1)]
    assert result['analysisMethod']=='fixed-batch-scoring' and result['scoring']['batches']==3
    assert result['usage']=={'requests':3,'inputTokens':30,'outputTokens':60} and peak<=8


def test_fixed_pipeline_reports_partial_failures(tmp_path,monkeypatch):
    monkeypatch.setenv('PHOTO_SCOUT_SCORING_BATCH_SIZE','6')
    import json
    from types import SimpleNamespace
    import agentic_services.photo_scout.scoring as visual
    settings=Settings(openai_api_key='test',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test')
    async def image(url):
        if url=='0':raise ValueError('Download failed')
        return 'data:image/jpeg;base64,/9j/dGVzdA=='
    async def parse(**kw):
        rows=[json.loads(c['text'])['image'] for c in kw['input'][0]['content'][1:] if c['type']=='input_text']
        assessments=[_scoring_assessment(visual,r,False) for r in rows]
        if rows[0]['id']=='1':assessments=assessments[:-1]  # Incomplete batch must not pass as fully scored.
        return SimpleNamespace(output_parsed=visual.VisualBatch(assessments=assessments),usage=SimpleNamespace(input_tokens=10,output_tokens=20))
    _scoring_client(monkeypatch,parse);monkeypatch.setattr(visual,'image_data',image)
    result=asyncio.run(visual.explore(settings,ExploreRequest(lat=0,lon=0),_scoring_rows(),{}))
    assert len(result['spots'])==7 and result['inspectedImages']==7
    assert all(s['recommend'] is False for s in result['spots'])
    assert result['scoring']=={'checkedImages':7,'filteredOutImages':0,'matchedImages':7,'candidateImages':13,'downloadedImages':12,'scoredImages':7,'downloadFailedImages':1,'scoringFailedImages':5,'batches':3,'cachedImages':0,'newlyScoredImages':7}
    assert len(result['imageAssessments'])==7 and 'could not be scored' in result['coverage']


def test_fixed_pipeline_all_failed_is_an_error(tmp_path,monkeypatch):
    import agentic_services.photo_scout.scoring as visual
    settings=Settings(openai_api_key='test',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test')
    async def image(url):raise ValueError('Unavailable')
    async def parse(**kw):raise AssertionError('No model request for failed downloads')
    _scoring_client(monkeypatch,parse);monkeypatch.setattr(visual,'image_data',image)
    with pytest.raises(ValueError,match='No images could be scored'):
        asyncio.run(visual.explore(settings,ExploreRequest(lat=0,lon=0),_scoring_rows(),{}))


def test_background_preview_is_durable_private_and_idempotent(tmp_path,monkeypatch):
    import agentic_services.photo_scout.routes as routes
    monkeypatch.setenv('PHOTO_SCOUT_ENABLED','1')
    monkeypatch.setenv('PHOTO_SCOUT_HUMAN_FREE_PREVIEW','1')
    settings=Settings(openai_api_key='fixture',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test',service_api_key='private')
    calls=[]
    async def nearby(*args): return [],{'status':'ok','provider':'openstreetmap'}
    async def candidates(*args,**kwargs): return [],{}
    async def explore(*args,**kwargs):
        calls.append(1)
        return {'spots':[],'summary':'No suitable images'}
    monkeypatch.setattr(routes,'nearby_pois',nearby)
    monkeypatch.setattr(routes,'candidates',candidates)
    monkeypatch.setattr(routes,'explore',explore)
    app=create_app(settings=settings);client=TestClient(app)
    token='a'*32;headers={'Authorization':'Bearer private','X-Request-Token':token}
    submitted=client.post('/photo-scout/v1/jobs',json={'lat':0,'lon':0},headers=headers)
    assert submitted.status_code==202 and not calls
    job=submitted.json()['jobId']
    assert client.post('/photo-scout/v1/jobs',json={'lat':0,'lon':0},headers=headers).json()['jobId']==job
    assert client.post('/photo-scout/v1/jobs',json={'lat':1,'lon':0},headers=headers).status_code==409
    assert client.get('/photo-scout/v1/report/'+job).status_code==404
    auth={'X-Report-Token':token}
    assert client.get('/photo-scout/v1/report/'+job,headers=auth).json()['state']=='queued'
    # Recreate the app: no browser request or in-memory task from submission survives.
    restarted=create_app(settings=settings)
    assert asyncio.run(restarted.state.process_photo_preview())
    assert calls==[1]
    report=TestClient(restarted).get('/photo-scout/v1/report/'+job,headers=auth)
    assert report.json()['state']=='complete' and report.headers['cache-control']=='private, no-store'
    assert not asyncio.run(restarted.state.process_photo_preview())
    assert TestClient(restarted).get('/photo-scout/v1/report/'+job,headers=auth).json()['result']['spots']==[]
    assert calls==[1]


def test_preview_leases_recover_and_failed_reads_do_not_charge(tmp_path,monkeypatch):
    import time
    monkeypatch.setenv('PHOTO_SCOUT_HUMAN_FREE_PREVIEW','1')
    settings=Settings(openai_api_key='fixture',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test',service_api_key='private')
    store=PhotoStore(settings.database_path)
    job=store.enqueue_preview(ExploreRequest(lat=0,lon=0),'b'*32)
    assert store.claim_preview()['id']==job
    assert PhotoStore(settings.database_path).claim_preview() is None
    store.update(job,lease_until=time.time()-1)
    assert PhotoStore(settings.database_path).claim_preview()['id']==job
    store.update(job,lease_until=time.time()-1)
    assert store.claim_preview() is None
    assert store.get(job)['state']=='failed'
    response=TestClient(create_app(settings=settings)).get('/photo-scout/v1/report/'+job,headers={'X-Report-Token':'b'*32})
    assert response.status_code==200 and response.json()['state']=='failed'
    store.update(job,created=time.time()-30*86400-1)
    assert store.get(job) is None
    store.prune()
    with store.connect() as db: assert db.execute('SELECT count(*) FROM photo_scout_jobs').fetchone()[0]==0


def test_city_scale_radius_and_commons_provider_limit(monkeypatch):
    assert ExploreRequest(lat=0,lon=0,radius=20000).radius==20000
    requested=[]
    async def response(client,url,params):
        requested.append(params['gsradius'])
        assert params['gsradius']<=10000
        return {'query':{'geosearch':[]}}
    import agentic_services.photo_scout.sources as sources
    monkeypatch.setattr(sources,'get_json',response)
    assert asyncio.run(commons(None,0,0,20000))==[]
    assert len(requested)==5 and set(requested)=={10000}


def test_city_pois_sample_bounded_regions_in_one_request(monkeypatch):
    import agentic_services.photo_scout.sources as sources
    real=httpx.AsyncClient
    def handler(r):
        query=r.url.params['data']
        assert query.count('out center 40;')==5 and '(around:20000,' not in query
        return httpx.Response(200,json={'elements':[{'type':'node','id':i,'lat':lat,'lon':lon,'tags':{'name':f'Park {i}','leisure':'park'}} for i,(lat,lon) in enumerate([(0,0),(.116,0),(-.116,0),(0,.116),(0,-.116)])]})
    monkeypatch.setattr(sources.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler)))
    rows,status=asyncio.run(sources.nearby_pois(0,0,20000,['park']))
    assert len(rows)==5 and status['sampledAreas']==5 and status['areaRadiusMeters']==5000
    assert all(row['distanceMeters']<=20000 for row in rows)


def test_multiangle_candidates_preserve_every_panorama_view(monkeypatch):
    import agentic_services.photo_scout.sources as sources
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_ENABLED','1')
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_API_KEY','test')
    monkeypatch.setenv('PHOTO_SCOUT_PANORAMAX_ENABLED','0')
    monkeypatch.delenv('PHOTO_SCOUT_MAPILLARY_TOKEN',raising=False)
    async def empty(*a): return []
    async def google(*a,**kw):
        return [{'id':f'{i}:{j}','provider':'google-street-view','lat':0,'lon':i*.001,
            'imageUrl':f'google-streetview://pano{i}/{j*45}/0','poi':{'id':str(i),'lat':0,'lon':i*.001}}
            for i in range(8) for j in range(8)]
    monkeypatch.setattr(sources,'commons',empty)
    monkeypatch.setattr(sources,'google_streetview',google)
    rows,status=asyncio.run(sources.candidates(0,0,1000,[{'id':'test','lat':0,'lon':0}]))
    assert len(rows)==64 and status['google-street-view']['sampledImages']==64
    assert status['google-street-view']['maxViewsPerLocation']==8


def test_multiangle_scoring_keeps_best_view_even_after_old_24_image_cap(tmp_path,monkeypatch):
    import json
    from types import SimpleNamespace
    import agentic_services.photo_scout.scoring as visual
    settings=Settings(openai_api_key='test',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test')
    rows=[{'id':str(i),'provider':'test','lat':0,'lon':0,'imageUrl':str(i),'viewHeadingDegrees':90,
        'viewPitchDegrees':0,'poi':{'id':'same','name':'Tower','lat':0,'lon':0}} for i in range(30)]
    async def image(url):return 'data:image/jpeg;base64,/9j/dGVzdA=='
    async def parse(**kw):
        batch=[json.loads(c['text'])['image'] for c in kw['input'][0]['content'][1:] if c['type']=='input_text']
        return SimpleNamespace(output_parsed=visual.VisualBatch(assessments=[visual.ImageAssessment(
            image_id=r['id'],poi_id='same',name='Tower',score=50+int(r['id']),recommend=True,
            visible_evidence='Tower',photo_tip='Frame tower',uncertainty='Access unknown',confidence='high') for r in batch]),usage=None)
    _scoring_client(monkeypatch,parse);monkeypatch.setattr(visual,'image_data',image)
    result=asyncio.run(visual.explore(settings,ExploreRequest(lat=0,lon=0),rows,{}))
    assert result['inspectedImages']==30
    assert len(result['spots'])==1 and result['spots'][0]['image_id']=='29'
    assert result['spots'][0]['viewPitchDegrees']==0


def test_google_horizontal_reference_reaches_provider(tmp_path,monkeypatch):
    import agentic_services.photo_scout.sources as sources
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_ENABLED','1');monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_API_KEY','test')
    monkeypatch.setenv('WEB_EVIDENCE_DB',str(tmp_path/'db'));monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_DAILY_IMAGE_LIMIT','0')
    requests=[]
    def handler(r):
        requests.append(r); assert r.url.params['heading']=='90'
        return httpx.Response(200,content=b'\xff\xd8\xfftest')
    real=httpx.AsyncClient
    monkeypatch.setattr(sources.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler)))
    asyncio.run(sources.google_image_data('google-streetview://fixture/90'))
    asyncio.run(sources.google_image_data('google-streetview://fixture/90/80'))
    assert [r.url.params['pitch'] for r in requests]==['0','80']
    with pytest.raises(ValueError):asyncio.run(sources.google_image_data('google-streetview://fixture/90/91'))


def test_score_cache_reuses_successful_views_without_download_or_model(tmp_path,monkeypatch):
    import json
    from types import SimpleNamespace
    import agentic_services.photo_scout.scoring as visual
    settings=Settings(openai_api_key='test',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test')
    downloads=[];calls=[]
    async def image(url):downloads.append(url);return 'data:image/jpeg;base64,/9j/dGVzdA=='
    async def parse(**kw):
        batch=[json.loads(c['text'])['image'] for c in kw['input'][0]['content'][1:] if c['type']=='input_text']
        calls.append([r['id'] for r in batch])
        return SimpleNamespace(output_parsed=visual.VisualBatch(assessments=[_scoring_assessment(visual,r,int(r['id'])!=0) for r in batch]),usage=None)
    _scoring_client(monkeypatch,parse);monkeypatch.setattr(visual,'image_data',image)
    first=asyncio.run(visual.explore(settings,ExploreRequest(lat=0,lon=0),_scoring_rows(),{}))
    second=asyncio.run(visual.explore(settings,ExploreRequest(lat=.001,lon=0,radius=2000,limit=5),_scoring_rows(),{}))
    assert len(downloads)==13 and len(calls)==1
    assert first['scoring']['newlyScoredImages']==13
    assert second['scoring']['cachedImages']==13 and second['scoring']['newlyScoredImages']==0
    assert len(second['poiResults'])==13 and any(p['recommend'] is False for p in second['poiResults'])
    assert second['usage']['requests']==0 and second['scoring']['downloadedImages']==0
    assert all(a['scoreFromCache'] for a in second['imageAssessments'])
    assert second['imageAssessments'][-1]['recommend'] is False
    changed=_scoring_rows();changed[-1]['sourceDate']='new-version'
    third=asyncio.run(visual.explore(settings,ExploreRequest(lat=0,lon=0),changed,{}))
    assert third['scoring']['cachedImages']==12 and third['scoring']['newlyScoredImages']==1
    assert len(downloads)==14 and len(calls)==2


def test_score_cache_context_and_expiry(tmp_path,monkeypatch):
    from agentic_services.photo_scout.score_cache import ScoreCache
    import agentic_services.photo_scout.score_cache as module
    row=_scoring_rows()[0];payload=ExploreRequest(lat=0,lon=0)
    cache=ScoreCache(tmp_path/'db');key=cache.key(row,payload,'model','prompt')
    assert key==cache.key({**row,'distanceMeters':90,'poiDistanceMeters':10},ExploreRequest(lat=1,lon=1,radius=2000,limit=5),'model','prompt')
    for image,request,model,prompt in [({**row,'viewHeadingDegrees':45},payload,'model','prompt'),
        ({**row,'poi':{**row['poi'],'id':'other'}},payload,'model','prompt'),
        (row,ExploreRequest(lat=0,lon=0,photoStyles=['urban']),'model','prompt'),
        (row,ExploreRequest(lat=0,lon=0,preferences='golden hour'),'model','prompt'),
        (row,ExploreRequest(lat=0,lon=0,poiQueries=['coffee shops']),'model','prompt'),
        (row,ExploreRequest(lat=0,lon=0,scoringIntent='outdoor coffee shops'),'model','prompt'),
        (row,payload,'new-model','prompt'),(row,payload,'model','new-prompt')]:
        assert key!=cache.key(image,request,model,prompt)
    cache.put([(key,{'score':70})]);assert cache.get(key)=={'score':70}
    monkeypatch.setattr(module.time,'time',lambda:9999999999)
    assert cache.get(key) is None


def test_history_thumbnail_links_require_auth_and_valid_google_panorama(tmp_path):
    from urllib.parse import urlsplit,parse_qs
    settings=Settings(openai_api_key=None,openai_model='test',database_path=tmp_path/'db',base_url='https://api.test',service_api_key='private')
    client=TestClient(create_app(settings=settings));headers={'Authorization':'Bearer private'}
    url='https://www.google.com/maps/@?api=1&map_action=pano&pano=fixture_pano&heading=225'
    assert client.post('/photo-scout/v1/thumbnails',json={'sourceUrls':[url]}).status_code==401
    response=client.post('/photo-scout/v1/thumbnails',json={'sourceUrls':[url]},headers=headers)
    assert response.status_code==200
    assert response.headers['cache-control']=='private, no-store'
    q=parse_qs(urlsplit(response.json()['imageUrls'][0]).query)
    assert q['reference']==['google-streetview://fixture_pano/225']
    assert len(q['signature'][0])==64
    tilted=client.post('/photo-scout/v1/thumbnails',json={'sourceUrls':[url+'&pitch=-20']},headers=headers)
    assert parse_qs(urlsplit(tilted.json()['imageUrls'][0]).query)['reference']==['google-streetview://fixture_pano/225/-20']
    for bad in [url+'&pitch=91',url+'&pitch=abc',url.replace('www.google.com','evil.test'),url.replace('225','360'),url.replace('https:','http:')]:
        assert client.post('/photo-scout/v1/thumbnails',json={'sourceUrls':[bad]},headers=headers).status_code==422


def test_thumbnail_loads_do_not_consume_source_search_limit(tmp_path,monkeypatch):
    import base64
    import agentic_services.photo_scout.routes as routes
    async def image(reference):return 'data:image/jpeg;base64,'+base64.b64encode(b'\xff\xd8\xfffixture').decode()
    monkeypatch.setattr(routes,'google_image_data',image)
    settings=Settings(openai_api_key=None,openai_model='test',database_path=tmp_path/'db',base_url='https://api.test',service_api_key='private')
    client=TestClient(create_app(settings=settings));headers={'Authorization':'Bearer private'}
    url='https://www.google.com/maps/@?map_action=pano&pano=fixture&heading=315'
    signed=client.post('/photo-scout/v1/thumbnails',json={'sourceUrls':[url]},headers=headers).json()['imageUrls'][0]
    for _ in range(24):assert client.get(signed,headers=headers).status_code==200


def test_low_scoring_unsuitable_pois_still_fill_selected_top_five(tmp_path,monkeypatch):
    import json
    from types import SimpleNamespace
    import agentic_services.photo_scout.scoring as visual
    settings=Settings(openai_api_key='test',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test')
    async def image(url):return 'data:image/jpeg;base64,/9j/dGVzdA=='
    async def parse(**kw):
        batch=[json.loads(c['text'])['image'] for c in kw['input'][0]['content'][1:] if c['type']=='input_text']
        choices=[_scoring_assessment(visual,r,False).model_copy(update={'score':int(r['id'])}) for r in batch]
        return SimpleNamespace(output_parsed=visual.VisualBatch(assessments=choices),usage=None)
    _scoring_client(monkeypatch,parse);monkeypatch.setattr(visual,'image_data',image)
    result=asyncio.run(visual.explore(settings,ExploreRequest(lat=0,lon=0,limit=5),_scoring_rows(),{}))
    assert len(result['poiResults'])==13
    assert result['topLimit']==13 and len(result['spots'])==13
    assert [s['score'] for s in result['spots']]==list(range(12,-1,-1))
    assert all(s['recommend'] is False for s in result['spots'])


def test_agent_discovery_requires_paid_order_and_retains_token_protected_report(tmp_path,monkeypatch):
    import hashlib
    import agentic_services.photo_scout.routes as routes
    monkeypatch.setenv('PHOTO_SCOUT_ENABLED','1');monkeypatch.setenv('PHOTO_SCOUT_PRICE_CENTS','200')
    settings=Settings(openai_api_key='fixture',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test',service_api_key='private')
    app=create_app(settings=settings);client=TestClient(app)
    calls=[]
    async def nearby(*args):return [],{'status':'ok','provider':'openstreetmap'}
    async def candidates(*args,**kwargs):return [],{}
    async def explore(*args):calls.append(1);return {'spots':[],'summary':'Verified fixture'}
    monkeypatch.setattr(routes,'nearby_pois',nearby);monkeypatch.setattr(routes,'candidates',candidates);monkeypatch.setattr(routes,'explore',explore)
    payload={'lat':41.88,'lon':-87.62};order='ord_'+'a'*32;token='private-order-token'
    headers={'Authorization':'Bearer private','X-Agentic-Order-Id':order,'X-Agentic-Order-Amount-Microusd':'2000000','X-Agentic-Order-Token-Hash':hashlib.sha256(token.encode()).hexdigest(),'X-Agentic-Payment-Protocol':'x402-mcp'}
    assert client.post('/photo-scout/v1/discover',json=payload,headers={'Authorization':'Bearer private'}).status_code==403
    assert client.post('/photo-scout/v1/discover',json=payload,headers={**headers,'X-Agentic-Order-Amount-Microusd':'1'}).status_code==403
    result=client.post('/photo-scout/v1/discover',json=payload,headers=headers);assert result.status_code==200
    assert result.json()['commerce']['orderId']==order and result.headers['X-Agentic-Receipt-Id']
    assert client.post('/photo-scout/v1/discover',json=payload,headers=headers).status_code==200 and len(calls)==1
    path='/photo-scout/v1/report/ps_'+order
    assert client.get(path).status_code==404
    assert client.get(path,headers={'X-Report-Token':token}).json()['result']['summary']=='Verified fixture'
    from agentic_services.storage import VerificationStore
    store=VerificationStore(settings.database_path)
    assert store.get_order_with_token(order,token)['receipt']['signature']
    assert store.get_order_with_token(order,'stranger') is None

def test_cafe_filter_is_applied_at_overpass_source(monkeypatch):
    import agentic_services.photo_scout.sources as sources
    real=httpx.AsyncClient
    def handler(request):
        query=request.url.params['data']
        assert '["amenity"~"^(cafe)$"]' in query
        assert 'historic' not in query and 'viewpoint' not in query
        return httpx.Response(200,json={'elements':[
            {'type':'node','id':1,'lat':0,'lon':0.001,'tags':{'name':'Cafe','amenity':'cafe'}},
            {'type':'node','id':2,'lat':0,'lon':0.002,'tags':{'name':'Fort','historic':'fort'}}]})
    monkeypatch.setattr(sources.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler)))
    rows,status=asyncio.run(sources.nearby_pois(0,0,1000,['cafe']))
    assert [p['name'] for p in rows]==['Cafe']


def test_relevance_filter_and_scoring_share_one_call_and_cache_rejections(tmp_path,monkeypatch):
    import json
    from types import SimpleNamespace
    import agentic_services.photo_scout.scoring as visual
    settings=Settings(openai_api_key='test',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test')
    rows=_scoring_rows()[:3]
    for row,provider in zip(rows,['wikimedia-commons','panoramax','google-street-view']):row['provider']=provider
    calls=[]
    async def image(url):return 'data:image/jpeg;base64,/9j/dGVzdA=='
    async def parse(**kw):
        calls.append(kw)
        batch=[json.loads(c['text'])['image'] for c in kw['input'][0]['content'][1:] if c['type']=='input_text']
        choices=[visual.ImageAssessment(image_id=r['id'],poi_id=r['poi']['id'],name=r['poi']['name'],
            score=None if r['id']!='2' else 8,recommend=False,matches_request=r['id']=='2',
            match_reason='A landscape, not a cafe.' if r['id']!='2' else 'The requested cafe is visible.',
            visible_evidence='Visible scene.',photo_tip='',uncertainty='',confidence='high') for r in batch]
        return SimpleNamespace(output_parsed=visual.VisualBatch(assessments=choices),usage=None)
    _scoring_client(monkeypatch,parse);monkeypatch.setattr(visual,'image_data',image)
    payload=ExploreRequest(lat=0,lon=0,poiQueries=['cafes'],scoringIntent='cafes')
    result=asyncio.run(visual.explore(settings,payload,rows,{}))
    assert len(calls)==1 and result['inspectedImages']==3
    assert result['scoring']['filteredOutImages']==2 and result['scoring']['scoredImages']==1
    assert len(result['poiResults'])==1 and result['poiResults'][0]['score']==8
    rejected=[a for a in result['imageAssessments'] if not a['matches_request']]
    assert len(rejected)==2 and all(a['score'] is None and not a['eligibleForRecommendation'] and a['exclusionReason'] for a in rejected)
    second=asyncio.run(visual.explore(settings,payload,rows,{}))
    assert len(calls)==1 and second['scoring']['cachedImages']==3
    assert second['scoring']['filteredOutImages']==2
    all_rejected=asyncio.run(visual.explore(settings,payload,rows[:2],{}))
    assert all_rejected['spots']==[] and all_rejected['poiResults']==[]
    assert all_rejected['scoring']['scoredImages']==0 and all_rejected['inspectedImages']==2


def test_area_exploration_keeps_images_far_from_pois_and_without_any_poi(monkeypatch):
    import agentic_services.photo_scout.sources as sources
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_ENABLED','0');monkeypatch.setenv('PHOTO_SCOUT_PANORAMAX_ENABLED','1')
    monkeypatch.delenv('PHOTO_SCOUT_MAPILLARY_TOKEN',raising=False)
    async def commons(*args):return [{'id':'commons','provider':'wikimedia-commons','lat':0,'lon':.006,'imageUrl':'commons'}]
    async def panoramax(*args):return [{'id':'pano','provider':'panoramax','lat':0,'lon':.005,'imageUrl':'pano'}]
    monkeypatch.setattr(sources,'commons',commons);monkeypatch.setattr(sources,'panoramax',panoramax)
    poi={'id':'park','lat':0,'lon':0}
    targeted,_=asyncio.run(sources.candidates(0,0,1000,[poi]));assert targeted==[]
    broad,_=asyncio.run(sources.candidates(0,0,1000,[poi],visual_exploration=True))
    assert {r['id'] for r in broad}=={'commons','pano'}
    assert all(r['allowUnlistedPlace'] and not r.get('poi') for r in broad)
    empty_catalog,_=asyncio.run(sources.candidates(0,0,1000,[],visual_exploration=True))
    assert len(empty_catalog)==2


def test_visual_exploration_retains_best_angle_for_unnamed_location(tmp_path,monkeypatch):
    import json
    from types import SimpleNamespace
    import agentic_services.photo_scout.scoring as visual
    settings=Settings(openai_api_key='test',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test')
    rows=[{'id':str(i),'provider':'google-street-view','lat':0,'lon':.001,
        'allowUnlistedPlace':True,'imageUrl':f'google-streetview://unnamed/{i*45}',
        'viewHeadingDegrees':i*45,'poi':{'id':'nearby-cafe','name':'Cafe','lat':0,'lon':0}} for i in range(8)]
    async def image(url):return 'data:image/jpeg;base64,/9j/dGVzdA=='
    async def parse(**kw):
        batch=[json.loads(c['text'])['image'] for c in kw['input'][0]['content'][1:] if c['type']=='input_text']
        request=json.loads(kw['input'][0]['content'][0]['text'])['request']
        return SimpleNamespace(output_parsed=visual.VisualBatch(assessments=[visual.ImageAssessment(
            image_id=r['id'],poi_id=None,name='Tree-lined corner',score=int(r['id']),recommend=True,
            matches_request=not request['poiQueries'],match_reason='No cafe visible' if request['poiQueries'] else 'Scenic street scene',visible_evidence='Trees frame the street.',photo_tip='Frame the trees.',uncertainty='Access unknown',confidence='medium') for r in batch]),usage=None)
    _scoring_client(monkeypatch,parse);monkeypatch.setattr(visual,'image_data',image)
    result=asyncio.run(visual.explore(settings,ExploreRequest(lat=0,lon=0),rows,{}))
    assert len(result['poiResults'])==1
    spot=result['poiResults'][0]
    assert spot['viewHeadingDegrees']==315 and spot['score']==7
    assert spot['poi']=={'id':'google:unnamed','lat':0,'lon':.001,'name':'Tree-lined corner','category':'photo-location'}
    assert spot['namedPoi'] is False and len(result['imageAssessments'])==8
    strict=[{**r,'allowUnlistedPlace':False} for r in rows]
    assert asyncio.run(visual.explore(settings,ExploreRequest(lat=0,lon=0,poiQueries=['cafes']),strict,{}))['poiResults']==[]

@pytest.mark.parametrize('queries,kinds', [([],['lake']),(['coffee shops'],[]),(['architecture'],[])])
def test_all_searches_keep_matching_unnamed_views_and_deduplicate_panorama(tmp_path,monkeypatch,queries,kinds):
    import json
    from types import SimpleNamespace
    import agentic_services.photo_scout.scoring as visual
    settings=Settings(openai_api_key='test',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test')
    poi={'id':'park','name':'Named Park','lat':0,'lon':0}
    rows=[{'id':str(i),'provider':'google-street-view','lat':0,'lon':i*.001,
           'imageUrl':f'google-streetview://{pano}/{heading}/0/120','poi':poi}
          for i,(pano,heading) in enumerate([('shoreA',0),('shoreA',45),('shoreB',90)])]
    async def image(url):return 'data:image/jpeg;base64,/9j/dGVzdA=='
    async def parse(**kw):
        batch=[json.loads(c['text'])['image'] for c in kw['input'][0]['content'][1:] if c['type']=='input_text']
        assert all(r.get('allowUnlistedPlace') is True for r in batch)
        return SimpleNamespace(output_parsed=visual.VisualBatch(assessments=[visual.ImageAssessment(
            image_id=r['id'],poi_id='park' if r['id']=='0' else None,
            name='Named Park' if r['id']=='0' else 'Lake shore',score={'0':70,'1':80,'2':60}[r['id']],recommend=True,
            matches_request=True,match_reason='Visible lake',visible_evidence='Lake and shore are visible.',
            photo_tip='Frame the lake.',uncertainty='Access unknown',confidence='medium') for r in batch]),usage=None)
    _scoring_client(monkeypatch,parse);monkeypatch.setattr(visual,'image_data',image)
    result=asyncio.run(visual.explore(settings,ExploreRequest(lat=0,lon=0,geographicKinds=kinds,poiQueries=queries),rows,{}))
    assert [p['image_id'] for p in result['spots']]==['1','2']
    assert all(p['namedPoi'] is False for p in result['spots'])


def test_visual_exploration_survives_place_provider_failure(tmp_path,monkeypatch):
    from fastapi import HTTPException
    import agentic_services.photo_scout.routes as routes
    monkeypatch.setenv('PHOTO_SCOUT_ENABLED','1');monkeypatch.setenv('PHOTO_SCOUT_HUMAN_FREE_PREVIEW','1')
    settings=Settings(openai_api_key='test',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test',service_api_key='private')
    async def pois(*args):raise HTTPException(503,'Unavailable')
    calls=[]
    async def images(lat,lon,radius,targets,**kwargs):
        calls.append(kwargs);assert targets==[]
        return [],{'panoramax':{'status':'ok'}}
    async def model(*args):return {'spots':[],'poiResults':[],'summary':'No images','sources':args[-1]}
    monkeypatch.setattr(routes,'nearby_pois',pois);monkeypatch.setattr(routes,'candidates',images);monkeypatch.setattr(routes,'explore',model)
    client=TestClient(create_app(settings=settings));headers={'Authorization':'Bearer private'}
    broad=client.post('/photo-scout/v1/preview',json={'lat':0,'lon':0,'photoStyles':['nature']},headers=headers)
    assert broad.status_code==200 and broad.json()['discoveryMethod']=='fixed-geographic-and-poi'
    assert calls==[{'visual_exploration':True}]
    strict=client.post('/photo-scout/v1/preview',json={'lat':0,'lon':0,'poiQueries':['cafes']},headers=headers)
    assert strict.status_code==503


def test_google_visual_sampling_covers_area_and_poi_anchors_with_eight_headings(monkeypatch):
    from agentic_services.photo_scout.sources import google_streetview
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_API_KEY','fixture')
    requests=[]
    def handler(request):
        location=request.url.params['location'];requests.append(location)
        lat,lon=map(float,location.split(','))
        return httpx.Response(200,json={'status':'OK','pano_id':'pano'+str(len(requests)),
            'location':{'lat':lat,'lng':lon},'copyright':'Google'})
    targets=[{'id':str(i),'lat':0,'lon':.0001*i} for i in range(5)]
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await google_streetview(client,0,0,1000,targets,area_sampling=True)
    rows=asyncio.run(run())
    assert len(requests)==50 and len(rows)<=400
    assert any(abs(float(p.split(',')[0]))>.003 for p in requests)
    assert {r['poi']['id'] for r in rows if r.get('poi')}=={str(i) for i in range(5)}
    panoramas={r['imageUrl'].split('/')[2] for r in rows}
    assert all({r['viewHeadingDegrees'] for r in rows if r['imageUrl'].split('/')[2]==p}==set(range(0,360,45)) for p in panoramas)


@pytest.mark.parametrize("count",[224,424])
def test_larger_parallel_batches_score_all_views_without_dropping_last_batch(tmp_path,monkeypatch,count):
    import json
    from types import SimpleNamespace
    import agentic_services.photo_scout.scoring as visual
    for key in ('PHOTO_SCOUT_SCORING_BATCH_SIZE','PHOTO_SCOUT_SCORING_CONCURRENCY','PHOTO_SCOUT_IMAGE_DOWNLOAD_CONCURRENCY'):
        monkeypatch.delenv(key,raising=False)
    settings=Settings(openai_api_key='test',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test')
    rows=[{'id':str(i),'lat':0,'lon':i*.0002,'provider':'test','imageUrl':str(i),
           'poi':{'id':f'poi:{i}','name':f'Place {i}','lat':0,'lon':i*.0002}} for i in range(count)]
    downloads=0;peak_downloads=0;started=0;peak_models=0;models=0;gate=asyncio.Event();sizes=[]
    async def image(url):
        nonlocal downloads,peak_downloads
        downloads+=1;peak_downloads=max(peak_downloads,downloads)
        await asyncio.sleep(.001);downloads-=1
        return 'data:image/jpeg;base64,/9j/dGVzdA=='
    async def parse(**kwargs):
        nonlocal started,peak_models,models
        batch=[json.loads(c['text'])['image'] for c in kwargs['input'][0]['content'][1:] if c['type']=='input_text']
        assert kwargs['max_output_tokens']>=len(batch)*900
        sizes.append(len(batch));started+=1;models+=1;peak_models=max(peak_models,models)
        if started==min(12,(count+23)//24):gate.set()
        await asyncio.wait_for(gate.wait(),2)
        models-=1
        return SimpleNamespace(output_parsed=visual.VisualBatch(assessments=[visual.ImageAssessment(
            image_id=r['id'],poi_id=r['poi']['id'],name=r['poi']['name'],score=100 if r['id']==str(count-1) else 30,recommend=True,
            visible_evidence='Visible sculpture',photo_tip='Frame sculpture',uncertainty='Access unknown',confidence='medium') for r in batch]),usage=None)
    _scoring_client(monkeypatch,parse);monkeypatch.setattr(visual,'image_data',image)
    result=asyncio.run(visual.explore(settings,ExploreRequest(lat=0,lon=0),rows,{}))
    assert sum(sizes)==count and max(sizes)==24
    assert len(sizes)==(count+23)//24
    assert 16<peak_downloads<=32 and 8<peak_models<=12
    assert result['inspectedImages']==count and result['scoring']['scoringFailedImages']==0
    assert len(result['spots'])==count and result['spots'][0]['image_id']==str(count-1)


def test_address_point_queries_only_nearest_google_panorama(monkeypatch):
    from agentic_services.photo_scout import sources
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_ENABLED','1')
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_API_KEY','secret-fixture')
    calls=[]
    async def metadata(client,url,params):
        calls.append(params)
        assert params['location']=='0,0' and params['radius']==50
        return {'status':'OK','pano_id':'fixture','location':{'lat':0,'lng':0}}
    async def unexpected(*args):raise AssertionError('No regional image sources for a pure address')
    monkeypatch.setattr(sources,'get_json',metadata)
    monkeypatch.setattr(sources,'commons',unexpected)
    monkeypatch.setattr(sources,'panoramax',unexpected)
    rows,statuses=asyncio.run(sources.candidates(0,0,5000,[],visual_exploration=True,point_only=True))
    assert len(calls)==1 and len(rows)==8
    assert set(statuses)=={'google-street-view'}
    assert statuses['google-street-view']['queriedLocations']==1
    assert statuses['google-street-view']['samplingMode']=='point'


@pytest.mark.parametrize('missing',['ZERO_RESULTS','error','outside'])
def test_address_without_street_view_falls_back_to_blank_regional_search(monkeypatch,missing):
    from agentic_services.photo_scout import sources
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_ENABLED','1')
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_API_KEY','secret-fixture')
    calls=[];other=[]
    async def google(client,lat,lon,radius,targets=None,area_sampling=False):
        calls.append((lat,lon,radius,targets,area_sampling))
        if not area_sampling:
            if missing=='error':raise ValueError('Provider unavailable')
            if missing=='ZERO_RESULTS':return []
            return [{'id':'outside','provider':'google-street-view','lat':1,'lon':1}]
        return [{'id':'area','provider':'google-street-view','lat':lat,'lon':lon,
                 'imageUrl':'google-streetview://regional/0/0/120'}]
    async def regional(client,lat,lon,radius):
        other.append((lat,lon,radius));return []
    monkeypatch.setattr(sources,'google_streetview',google)
    monkeypatch.setattr(sources,'commons',regional)
    monkeypatch.setattr(sources,'panoramax',regional)
    rows,statuses=asyncio.run(sources.candidates(0,0,5000,[],visual_exploration=True,point_only=True))
    assert calls[0][:3]==(0,0,50) and not calls[0][4]
    assert calls[1]==(0,0,5000,[],True)
    assert other and all(x==(0,0,5000) for x in other)
    assert [r['id'] for r in rows]==['area']
    assert rows[0]['allowUnlistedPlace']
    assert statuses['google-street-view']['addressFallback']
    assert statuses['google-street-view']['pointLookupRadiusMeters']==50
