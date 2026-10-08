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
