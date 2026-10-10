import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentic_services.photo_scout.search import (
    SearchParameters, SearchProviders, SearchUnavailable, compile_search, search_locations,
)


def lake():
    return {'id':'lake','name':'Lake','sourceUrl':'https://www.openstreetmap.org/way/1',
            'kinds':['lake'],'geometry':{'type':'Polygon','coordinates':[[[-.004,-.004],[.004,-.004],[.004,.004],[-.004,.004],[-.004,-.004]]]}}


@pytest.mark.parametrize('queries,kinds,strategy', [
    (['vegan bakeries'],[],'places-only'),
    (['coffee shops'],['lake'],'spatial-intersection'),
    ([],['lake'],'spatial-union'),
    ([],[],'area-imagery'),
])
def test_plan_routes_conditions_without_turning_style_into_a_business(queries,kinds,strategy):
    p=SearchParameters(lat=0,lon=0,poiQueries=queries,geographicKinds=kinds,
                       photoStyles=['vintage'],scoringIntent='Quiet vintage outdoor seating')
    plan=compile_search(p)
    assert plan.mergeStrategy==strategy and plan.candidateLimit==50
    if queries:assert plan.placesQueries==queries
    assert plan.parameters.scoringIntent=='Quiet vintage outdoor seating'
    assert 'vintage' not in plan.placesQueries
    if kinds:assert plan.geographicProximityMeters=={'lake':150}


def test_unknown_parameters_fail_instead_of_silently_dropping_conditions():
    with pytest.raises(ValidationError):SearchParameters(lat=0,lon=0,unsupportedCondition='lake')


def test_intersection_queries_in_parallel_and_filters_before_candidate_cap():
    arrivals=set();gate=asyncio.Event()
    async def arrived(name):
        arrivals.add(name)
        if len(arrivals)==2:gate.set()
        await asyncio.wait_for(gate.wait(),1)
    async def places(lat,lon,radius,queries,*,limit):
        await arrived('places');assert queries==['coffee shops'] and limit==60
        return [{'id':str(i),'name':'Cafe','lat':0,'lon':-.02} for i in range(30)]+[
            {'id':'shore-cafe','name':'Shore Cafe','lat':0,'lon':-.005}],{'status':'ok'}
    async def geometry(*args):
        await arrived('geometry');return [lake()],[],{'status':'ok'}
    async def unused(*args):raise AssertionError('Unexpected OSM POI call')
    result=asyncio.run(search_locations(SearchParameters(lat=0,lon=0,radius=3000,
        poiQueries=['coffee shops'],geographicKinds=['lake']),database_path=Path('/unused'),
        providers=SearchProviders(places,unused,geometry)))
    assert [p['id'] for p in result.places]==['shore-cafe']
    assert result.status['searchCounts']=={'rawNamedPlaces':31,'spatiallyMatchedNamedPlaces':1,
        'generatedGeographicPlaces':0,'returnedCandidates':1}


@pytest.mark.parametrize('named_ok',[True,False])
def test_geographic_union_can_keep_unnamed_road_points_without_named_source(named_ok):
    async def places(*args,**kwargs):
        return ([{'id':'park','name':'Shore Park','lat':0,'lon':-.005}] if named_ok else []),{'status':'ok' if named_ok else 'unavailable'}
    async def geometry(*args):
        paths=[{'id':'trail','name':'Trail','geometry':{'type':'LineString','coordinates':[[-.005,-.003],[-.005,.003]]}}]
        return [lake()],paths,{'status':'ok'}
    result=asyncio.run(search_locations(SearchParameters(lat=0,lon=0,radius=2000,geographicKinds=['lake']),
        database_path=Path('/unused'),providers=SearchProviders(places,None,geometry)))
    assert len(result.places)>1 and len(result.places)<=50
    assert any(p['id'].startswith('geo:') for p in result.places)
    assert result.status['namedSourceStatus']==('ok' if named_ok else 'unavailable')
    if named_ok:assert result.places[0]['id']=='park'


def test_address_only_area_search_does_not_invent_a_places_query():
    async def unused(*args,**kwargs):raise AssertionError('Address-only search called a POI provider')
    result=asyncio.run(search_locations(SearchParameters(lat=41.88,lon=-87.63,radius=1000),
        database_path=Path('/unused'),providers=SearchProviders(unused,unused,unused)))
    assert result.plan.mergeStrategy=='area-imagery' and result.plan.placesQueries==[]
    assert result.status['role']=='area-imagery'


def test_spatial_provider_failure_never_returns_unfiltered_places():
    async def places(*args,**kwargs):return [{'id':'cafe','lat':0,'lon':0}],{'status':'ok'}
    async def geometry(*args):return [],[],{'status':'unavailable'}
    with pytest.raises(SearchUnavailable,match='Geographic search'):
        asyncio.run(search_locations(SearchParameters(lat=0,lon=0,poiQueries=['cafes'],geographicKinds=['lake']),
            database_path=Path('/unused'),providers=SearchProviders(places,None,geometry)))


def test_nearby_distinct_businesses_are_not_erased_by_spatial_dedup():
    async def places(*args,**kwargs):return [
        {'id':'cafeA','name':'Cafe A','lat':0,'lon':0},
        {'id':'cafeB','name':'Cafe B','lat':0,'lon':.0001}],{'status':'ok'}
    async def geometry(*args):return [],[],{'status':'not_requested'}
    result=asyncio.run(search_locations(SearchParameters(lat=0,lon=0,poiQueries=['coffee shops']),
        database_path=Path('/unused'),providers=SearchProviders(places,None,geometry)))
    assert [p['id'] for p in result.places]==['cafeA','cafeB']


@pytest.mark.parametrize('queries,mode', [(['vegan bakeries'],'places-only'),([], 'area-imagery')])
def test_resolved_address_parameters_invoke_shared_tool_and_workflow(tmp_path,monkeypatch,queries,mode):
    from fastapi.testclient import TestClient
    from agentic_services.config import Settings
    from agentic_services.main import create_app
    from agentic_services.photo_scout import intent,routes
    monkeypatch.setenv('PHOTO_SCOUT_ENABLED','1');monkeypatch.setenv('PHOTO_SCOUT_HUMAN_FREE_PREVIEW','1')
    monkeypatch.setenv('PHOTO_SCOUT_POI_PROVIDER','google-places')
    settings=Settings(openai_api_key='fixture',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test',service_api_key='private')
    async def parse(*args):
        return intent.PhotoIntent(locationQuery='123 Main Street, Chicago',useMapCenter=False,
            poiQueries=queries,photoStyles=['vintage'],radiusMeters=2000,preferences='Quiet outdoor seating',
            scoringIntent='Quiet vintage outdoor seating',explanation='Address search',clarification=None)
    async def geocode(query):
        assert query=='123 Main Street, Chicago'
        return [{'lat':40,'lon':-96,'label':'123 Main Street'}]
    calls=[]
    async def places(lat,lon,radius,terms,*,limit):
        calls.append('places');assert (lat,lon,radius,limit)==(40,-96,2000,50) and terms==queries
        return [{'id':'bakery','name':'Bakery','lat':40,'lon':-96}],{'status':'ok'}
    async def geometry(*args):return [],[],{'status':'not_requested'}
    async def images(lat,lon,radius,pois,**kwargs):
        calls.append('images');assert kwargs.get('visual_exploration',False)==(mode=='area-imagery')
        assert len(pois)==(1 if queries else 0)
        return [],{}
    async def score(settings,payload,*args):
        assert payload.scoringIntent=='Quiet vintage outdoor seating'
        return {'spots':[],'poiResults':[],'summary':'No images','sources':{}}
    monkeypatch.setattr(intent,'parse_intent',parse);monkeypatch.setattr(intent,'geocode',geocode)
    monkeypatch.setattr(routes,'nearby_places',places);monkeypatch.setattr(routes,'fetch_region',geometry)
    monkeypatch.setattr(routes,'candidates',images);monkeypatch.setattr(routes,'explore',score)
    client=TestClient(create_app(settings=settings));headers={'Authorization':'Bearer private'}
    resolved=client.post('/photo-scout/v1/resolve',headers=headers,json={'query':'123 Main Street, Chicago','lat':0,'lon':0}).json()
    parameters=resolved['searchParameters']
    assert resolved['searchPlan']['mergeStrategy']==mode and parameters['lat']==40
    assert client.post('/photo-scout/v1/search',json=parameters).status_code==401
    search=client.post('/photo-scout/v1/search',headers=headers,json=parameters)
    assert search.status_code==200 and search.json()['searchPlan']==resolved['searchPlan']
    preview=client.post('/photo-scout/v1/preview',headers=headers,json=parameters)
    assert preview.status_code==200 and preview.json()['searchPlan']==resolved['searchPlan']
    if not queries:assert calls==['images']


def test_fifty_candidates_prioritize_spatial_coverage_for_geography_only():
    from agentic_services.photo_scout.search import merge_candidates
    plan=compile_search(SearchParameters(lat=0,lon=0,radius=20000,geographicKinds=['lake']))
    named=[{'id':f'n{i}','lat':.1,'lon':i*.001} for i in range(50)]
    generated=[{'id':f'g{i}','lat':0,'lon':i*.001} for i in range(50)]
    rows=merge_candidates(named,generated,plan)
    assert len(rows)==50 and sum(p['id'].startswith('g') for p in rows)==40
