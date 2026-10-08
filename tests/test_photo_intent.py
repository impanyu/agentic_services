import asyncio
import httpx
import pytest
from fastapi.testclient import TestClient
from agentic_services.photo_scout import intent
from agentic_services.photo_scout.routes import PhotoStore
from agentic_services.config import Settings
from agentic_services.main import create_app

def plan(**changes):
    return intent.PhotoIntent(**({'locationQuery':None,'useMapCenter':True,'photoStyles':['waterside'],'radiusMeters':1000,'preferences':'Quiet waterside','explanation':'Find nearby waterside places','clarification':None}|changes))

def test_resolve_map_context_and_explicit_place(monkeypatch):
    async def parsed(settings,payload):return plan(locationQuery='Eiffel Tower, Paris',useMapCenter=False)
    seen=[]
    async def geocode(query):seen.append(query);return [{'lat':48.8584,'lon':2.2945,'label':'Eiffel Tower','source':'photon/openstreetmap'}]
    monkeypatch.setattr(intent,'parse_intent',parsed);monkeypatch.setattr(intent,'geocode',geocode)
    payload=intent.IntentRequest(query='Photos near Eiffel Tower in Paris',lat=41,lon=-87)
    result=asyncio.run(intent.resolve_intent(None,payload));assert result['locations'][0]['lat']==48.8584;assert not result['visuallyAnalyzed'];assert seen==['Eiffel Tower, Paris']
    async def map_plan(settings,payload):return plan()
    monkeypatch.setattr(intent,'parse_intent',map_plan)
    result=asyncio.run(intent.resolve_intent(None,payload));assert result['locations'][0]['lat']==41;assert len(seen)==1

def test_clarification_and_unknown_place_never_guess_coordinates(monkeypatch):
    async def parsed(settings,payload):return plan(clarification='Which place?')
    async def geocode(query):raise AssertionError('Should not geocode unclear requests')
    monkeypatch.setattr(intent,'parse_intent',parsed);monkeypatch.setattr(intent,'geocode',geocode)
    payload=intent.IntentRequest(query='unclear',lat=0,lon=0)
    assert asyncio.run(intent.resolve_intent(None,payload))['locations']==[]
    async def explicit(settings,payload):return plan(locationQuery='unknown')
    async def empty(query):return []
    monkeypatch.setattr(intent,'parse_intent',explicit);monkeypatch.setattr(intent,'geocode',empty)
    result=asyncio.run(intent.resolve_intent(None,payload));assert result['clarification'];assert result['locations']==[]

def test_geocoder_validates_and_deduplicates_coordinates(monkeypatch):
    real=httpx.AsyncClient
    features=[{'geometry':{'coordinates':xy},'properties':{'name':'Park','city':'Paris'}} for xy in [[2,48],[2,48],[190,48],[2,90],['2',48]]]
    def response(request):assert request.url.host=='photon.komoot.io';assert request.url.params['q']=='Park Paris';return httpx.Response(200,json={'features':features})
    monkeypatch.setattr(intent.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(response)))
    assert asyncio.run(intent.geocode('Park Paris'))==[{'lat':48,'lon':2,'label':'Park, Paris','source':'photon/openstreetmap'}]

def test_text_budget_is_persistent(tmp_path,monkeypatch):
    monkeypatch.setenv('PHOTO_SCOUT_DAILY_INTENT_LIMIT','1')
    PhotoStore(tmp_path/'db').reserve_intent()
    with pytest.raises(Exception) as e:PhotoStore(tmp_path/'db').reserve_intent()
    assert e.value.status_code==429

def test_text_resolve_auth_and_no_store(tmp_path,monkeypatch):
    import agentic_services.photo_scout.routes as routes
    monkeypatch.setenv('PHOTO_SCOUT_ENABLED','1');monkeypatch.setenv('PHOTO_SCOUT_HUMAN_FREE_PREVIEW','1')
    settings=Settings(openai_api_key='fixture',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test',service_api_key='private')
    async def resolved(settings,payload):return {'locations':[{'lat':payload.lat,'lon':payload.lon}],'visuallyAnalyzed':False}
    monkeypatch.setattr(routes,'resolve_intent',resolved)
    client=TestClient(create_app(settings=settings));payload={'query':'here','lat':41,'lon':-87}
    assert client.post('/photo-scout/v1/resolve',json=payload).status_code==401
    response=client.post('/photo-scout/v1/resolve',json=payload,headers={'Authorization':'Bearer private'})
    assert response.status_code==200;assert response.headers['cache-control']=='private, no-store'
    assert client.post('/photo-scout/v1/resolve',json=payload|{'lat':91},headers={'Authorization':'Bearer private'}).status_code==422
