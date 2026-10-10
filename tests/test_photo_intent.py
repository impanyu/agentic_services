import asyncio
import httpx
import pytest
from fastapi.testclient import TestClient
from agentic_services.photo_scout import intent
from agentic_services.photo_scout.routes import PhotoStore
from agentic_services.config import Settings
from agentic_services.main import create_app

def plan(**changes):
    return intent.PhotoIntent(**({'locationQuery':None,'useMapCenter':True,'photoStyles':['waterside'],'radiusMeters':1000,'limit':3,'preferences':'Quiet waterside','explanation':'Find nearby waterside places','clarification':None}|changes))

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

def test_best_effort_unresolved_place_uses_map_without_followup(monkeypatch):
    async def parsed(settings,payload):return plan(locationQuery='unknown',clarification='obsolete follow-up')
    async def empty(query):return []
    monkeypatch.setattr(intent,'parse_intent',parsed);monkeypatch.setattr(intent,'geocode',empty)
    payload=intent.IntentRequest(query='any sentence',lat=41,lon=-87)
    with pytest.raises(ValueError,match='Could not resolve'):asyncio.run(intent.resolve_intent(None,payload))


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

def test_text_parameters_override_conflicting_ui_defaults(monkeypatch):
    async def parsed(settings,payload):
        assert payload.radius==500 and payload.photoStyles==['nature']
        return plan(locationQuery='Chicago',useMapCenter=False,radiusMeters=20000,limit=5,photoStyles=['urban'],preferences='Architectural river views')
    async def located(query):return [{'lat':41.88,'lon':-87.63,'label':'Chicago','source':'photon/openstreetmap'}]
    monkeypatch.setattr(intent,'parse_intent',parsed);monkeypatch.setattr(intent,'geocode',located)
    payload=intent.IntentRequest(query='Urban photos in Chicago within 20 km, top 5',lat=40,lon=-96,radius=500,limit=3,photoStyles=['nature'])
    result=asyncio.run(intent.resolve_intent(None,payload))
    assert result['radiusMeters']==20000 and 'limit' not in result and result['photoStyles']==['urban']
    assert result['locations'][0]['lat']==41.88 and result['preferences']=='Architectural river views'


def test_city_radius_validation():
    for model,field in [(intent.IntentRequest,'radius'),(intent.PhotoIntent,'radiusMeters')]:
        payload={'query':'here','lat':0,'lon':0} if model is intent.IntentRequest else plan().model_dump()
        assert getattr(model(**(payload|{field:20000})),field)==20000
        with pytest.raises(ValueError):model(**(payload|{field:20001}))


def test_city_geocoder_prefers_named_city_point_to_boundary_centroid(monkeypatch):
    real=httpx.AsyncClient
    features=[{'geometry':{'coordinates':[-87.57,41.72]},'properties':{'name':'Chicago','osm_key':'boundary'}},{'geometry':{'coordinates':[-87.62,41.87]},'properties':{'name':'Chicago','osm_key':'place','osm_value':'city'}}]
    monkeypatch.setattr(intent.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'features':features}))))
    assert asyncio.run(intent.geocode('Chicago, Illinois, USA'))[0]['lat']==41.87

@pytest.mark.parametrize('query',['caffe','café','coffee shop','咖啡店'])
def test_short_cafe_query_filters_category_without_geocoding(monkeypatch,query):
    async def parsed(settings,payload):return plan(poiQueries=['coffee shops'],preferences='Cafe photography')
    async def geocode(query):raise AssertionError('A category is not an address')
    monkeypatch.setattr(intent,'parse_intent',parsed);monkeypatch.setattr(intent,'geocode',geocode)
    result=asyncio.run(intent.resolve_intent(None,intent.IntentRequest(query=query,lat=37.84,lon=-122.51)))
    assert result['poiQueries']==['coffee shops']
    assert result['locations'][0]['lat']==37.84
    assert 'Cafe' in result['preferences']


def test_legacy_count_does_not_reach_interpretation_model(monkeypatch):
    import json
    from types import SimpleNamespace
    seen=[]
    class Client:
        def __init__(self,**kwargs):self.responses=self
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def parse(self,**kwargs):
            seen.append(kwargs)
            assert 'limit' not in json.loads(kwargs['input'])
            assert 'limit' not in kwargs['text_format'].model_fields
            return SimpleNamespace(output_parsed=plan())
    monkeypatch.setattr(intent,'AsyncOpenAI',Client)
    payload=intent.IntentRequest(query='architecture',lat=40,lon=-96,radius=20000,limit=3)
    result=asyncio.run(intent.resolve_intent(SimpleNamespace(openai_api_key='fixture',openai_model='test'),payload))
    assert seen and 'limit' not in result and result['radiusMeters']==1000


def test_logic_is_preserved_from_parser_to_shared_search_plan(monkeypatch):
    async def parsed(settings,payload):
        return plan(photoStyles=[],poiQueries=['coffee shops'],geographicKinds=['lake','sea'],
                    geographicCombination='any',featureCombination='any',
                    scoringIntent='Coffee shops beside either a lake or the sea, without crowds')
    monkeypatch.setattr(intent,'parse_intent',parsed)
    result=asyncio.run(intent.resolve_intent(None,intent.IntentRequest(query='cafes by lake or sea',lat=0,lon=0)))
    assert result['searchParameters']['geographicCombination']=='any'
    assert result['searchPlan']['geographicCombination']=='any'
    assert result['searchPlan']['mergeStrategy']=='spatial-intersection'
    assert 'without crowds' in result['scoringIntent']


@pytest.mark.parametrize('query',['','   '])
def test_ui_only_requests_are_planned_without_relocating_or_changing_controls(monkeypatch,query):
    from agentic_services.photo_scout.program import SearchProgram
    calls=[]
    async def parsed(settings,payload):
        calls.append(payload.model_dump())
        return plan(locationQuery='Chicago',useMapCenter=False,radiusMeters=100,
            photoStyles=['urban'],searchProgram=SearchProgram(steps=[{'id':'area','tool':'area_imagery'}],output='area').with_delivery())
    async def geocode(*args):pytest.fail('UI-only search must retain the selected map location')
    monkeypatch.setattr(intent,'parse_intent',parsed);monkeypatch.setattr(intent,'geocode',geocode)
    result=asyncio.run(intent.resolve_intent(None,intent.IntentRequest(query=query,lat=37.8,lon=-122.4,radius=5000,photoStyles=['nature'])))
    assert len(calls)==1 and calls[0]['query']==query
    assert result['locations'][0]['lat']==37.8 and result['locations'][0]['lon']==-122.4
    assert result['radiusMeters']==5000 and result['photoStyles']==['nature']
    assert result['searchParameters']['searchProgram']['steps'][-1]['tool']=='rank_results'
