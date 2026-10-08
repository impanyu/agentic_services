import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from agentic_services.config import Settings
from agentic_services.main import create_app
from agentic_services.photo_scout.agent import VisualChoice,VisualResult,validate_result
from agentic_services.photo_scout.routes import ExploreRequest,PhotoStore,create_photo_router
from agentic_services.photo_scout.sources import commons,distance,image_host
from agentic_services.storage import VerificationStore


def test_coordinates_and_image_hosts():
    for changes in ({'lat':float('nan')},{'lon':181},{'radius':5001},{'lat':86}):
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
    async def images(*a): return [{'id':'test'}],{'test':{'status':'ok'}}
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
    async def images(*a): return [{'id':'test'}],{'test':{'status':'ok'}}
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
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=data))) as c:
            return await google_streetview(c,0,0,1000)
    rows=asyncio.run(run())
    assert len(rows)==2 and {r['viewHeadingDegrees'] for r in rows}=={0,180}
    assert all(r['viewFovDegrees']==120 for r in rows)
    assert 'secret-fixture' not in json.dumps(rows)
    for r in rows:r['distanceMeters']=0
    assert len(diverse_sample(rows))==2
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
    assert len(rows)==6
    assert google_sampling_spacing(100)==80 and google_sampling_spacing(1000)==200 and google_sampling_spacing(5000)==250
    for r in rows:r['distanceMeters']=round(distance((0,0),(r['lat'],r['lon'])))
    sampled=diverse_sample(rows,3)
    assert len({(r['lat'],r['lon']) for r in sampled})==3


def test_google_query_grid_covers_area_and_limits_concurrency(monkeypatch):
    from agentic_services.photo_scout.sources import google_query_points,google_streetview
    points=google_query_points(0,0,1000)
    assert len(points)==25 and points[0]==(0,0)
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
    assert requests==25 and peak<=5


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
    async def images(*a): return [{'id':'test'}],{'test':{'status':'ok'}}
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
    assert len(requests)==1 and len(rows)==2
    assert {r['viewHeadingDegrees'] for r in rows}=={0,180}
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
    result=client.post('/photo-scout/v1/preview',json={'lat':0,'lon':0},headers={'Authorization':'Bearer private'})
    assert result.status_code==200 and calls==['pois','images','agent']
    body=result.json();assert body['candidatePoiCount']==1 and body['discoveryMethod']=='poi-first'
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
    rows=[{'id':str(i),'lat':0,'lon':i*.01,'poi':{'id':'same'}} for i in range(2)]
    choices=[VisualChoice(image_id=str(i),name='Park',score=80,visible_evidence='Trees',photo_tip='Frame trees',uncertainty='Unknown',confidence='medium') for i in range(2)]
    assert len(validate_result(VisualResult(spots=choices,summary='Park'),rows,{'0','1'},3))==1
