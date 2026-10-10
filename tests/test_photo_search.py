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

@pytest.mark.parametrize('combination,expected',[('all',[]),('any',['shore'])])
def test_cafes_near_lake_or_sea_preserve_subject_and_spatial_logic(combination,expected):
    async def places(*args,**kwargs):
        return [{'id':'shore','lat':0,'lon':-.005},{'id':'inland','lat':0,'lon':-.02}],{'status':'ok'}
    async def geography(*args):return [lake()],[],{'status':'ok'}
    result=asyncio.run(search_locations(SearchParameters(lat=0,lon=0,radius=3000,
        poiQueries=['coffee shops'],geographicKinds=['lake','sea'],geographicCombination=combination),
        database_path=Path('/unused'),providers=SearchProviders(places,None,geography)))
    assert [p['id'] for p in result.places]==expected
    assert result.plan.mergeStrategy=='spatial-intersection'
    assert result.plan.geographicCombination==combination
    assert result.status['searchCounts']['generatedGeographicPlaces']==0


def test_alternative_geographic_scenery_samples_available_shore_even_if_other_kind_absent():
    from agentic_services.photo_scout.geography import geographic_places,filter_places
    paths=[{'id':'trail','name':'Trail','geometry':{'type':'LineString','coordinates':[[-.005,-.003],[-.005,.003]]}}]
    assert not geographic_places(0,0,2000,[lake()],paths,['lake','sea'])
    rows=geographic_places(0,0,2000,[lake()],paths,['lake','sea'],combination='any')
    assert len(rows)>1
    assert filter_places(rows,[lake()],['lake','sea'],0,0,combination='any')==rows
    assert not filter_places(rows,[lake()],['lake','sea'],0,0,combination='all')


def test_alternative_mapped_features_and_score_cache_do_not_collapse_to_and(tmp_path):
    from agentic_services.photo_scout.osm_features import OSMFeatureQuery,matches_features
    from agentic_services.photo_scout.score_cache import ScoreCache
    from agentic_services.photo_scout.routes import ExploreRequest
    qs=[OSMFeatureQuery(label=x,filters=[{'key':'amenity','value':x}]) for x in ['bench','fountain']]
    place={'id':'bench','lat':0,'lon':0}
    assert matches_features(place,[[place],[]],qs,combination='any')
    assert not matches_features(place,[[place],[]],qs,combination='all')
    assert not matches_features(place,[[place]],qs,combination='any')
    a=ExploreRequest(lat=0,lon=0,osmFeatures=qs,featureCombination='all')
    b=a.model_copy(update={'featureCombination':'any'})
    assert ScoreCache.key(place,a,'model','prompt')!=ScoreCache.key(place,b,'model','prompt')


def forest():
    return {'id':'forest','name':'Forest','sourceUrl':'https://www.openstreetmap.org/way/2',
        'kinds':['forest'],'geometry':{'type':'Polygon','coordinates':[[[.01,-.004],[.02,-.004],[.02,.004],[.01,.004],[.01,-.004]]]}}


def mixed_branches():
    return [{'poiQueries':['coffee shops'],'geographicKinds':['lake'],'visualIntent':'Vintage lakeside cafes'},
            {'poiQueries':['restaurants'],'geographicKinds':['forest'],'visualIntent':'Quiet woodland restaurants'}]


def test_grouped_union_keeps_correlated_subjects_and_filters_panorama_under_its_own_branch():
    from agentic_services.photo_scout.search import branch_contexts,filter_branch_images
    async def places(lat,lon,radius,queries,**kwargs):
        subject='cafe' if queries==['coffee shops'] else 'restaurant'
        return [{'id':subject+'-lake','lat':0,'lon':-.005},
                {'id':subject+'-forest','lat':0,'lon':.015}],{'status':'ok'}
    async def geometry(lat,lon,radius,kinds,path):return ([lake()] if kinds==['lake'] else [forest()]),[],{'status':'ok'}
    p=SearchParameters(lat=0,lon=0,radius=3000,searchBranches=mixed_branches())
    result=asyncio.run(search_locations(p,database_path=Path('/unused'),providers=SearchProviders(places,None,geometry)))
    assert result.plan.mergeStrategy=='branch-union'
    assert [r['id'] for r in result.places]==['cafe-lake','restaurant-forest']
    assert [r['searchBranchIndexes'] for r in result.places]==[[0],[1]]
    assert len(result.imagery_targets)==2
    rows=[{'id':'lake-cafe-view','lat':0,'lon':-.005,'poi':{'id':'cafe-lake'}},
          {'id':'forest-restaurant-view','lat':0,'lon':.015,'poi':{'id':'restaurant-forest'}},
          {'id':'wrong-restaurant-at-lake','lat':0,'lon':-.005,'poi':{'id':'restaurant-forest'}},
          {'id':'wrong-cafe-in-forest','lat':0,'lon':.015,'poi':{'id':'cafe-lake'}}]
    eligible=filter_branch_images(rows,branch_contexts(result))
    assert [r['id'] for r in eligible]==['lake-cafe-view','forest-restaurant-view']
    assert [r['eligibleSearchBranchIndexes'] for r in eligible]==[[0],[1]]


def test_branch_union_is_fair_capped_and_deduplicates_shared_provider_work():
    calls=[]
    async def places(lat,lon,radius,queries,**kwargs):
        calls.append(tuple(queries))
        return [{'id':queries[0]+str(i),'lat':0,'lon':i/10000} for i in range(50)],{'status':'ok'}
    async def geometry(*args):return [],[],{'status':'not_requested'}
    branches=[{'poiQueries':['cafes']},{'poiQueries':['motels']},{'poiQueries':['cafes']}]
    result=asyncio.run(search_locations(SearchParameters(lat=0,lon=0,searchBranches=branches),
        database_path=Path('/unused'),providers=SearchProviders(places,None,geometry)))
    assert len(calls)==2 and len(result.places)==50
    assert len([p for p in result.places if p['id'].startswith('cafes')])==25
    assert len([p for p in result.places if p['id'].startswith('motels')])==25
    assert result.places[0]['searchBranchIndexes']==[0,2]


def test_branch_failure_is_explicit_instead_of_silently_losing_an_alternative():
    async def places(*args,**kwargs):return [],{'status':'ok'}
    async def geometry(lat,lon,radius,kinds,path):return [],[],{'status':'unavailable' if kinds==['forest'] else 'ok'}
    with pytest.raises(SearchUnavailable):
        asyncio.run(search_locations(SearchParameters(lat=0,lon=0,searchBranches=mixed_branches()),
            database_path=Path('/unused'),providers=SearchProviders(places,None,geometry)))


@pytest.mark.parametrize('extra',[{'poiQueries':['cafes']},{'geographicKinds':['lake']},{'categories':['park']}])
def test_ambiguous_branch_and_top_level_constraints_fail_instead_of_being_ignored(extra):
    with pytest.raises(ValidationError):SearchParameters(lat=0,lon=0,searchBranches=mixed_branches(),**extra)
    with pytest.raises(ValidationError):SearchParameters(lat=0,lon=0,searchBranches=[{}])


def test_grouped_conditions_survive_resolve_and_website_image_pipeline(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from agentic_services.config import Settings
    from agentic_services.main import create_app
    from agentic_services.photo_scout import intent,routes
    monkeypatch.setenv('PHOTO_SCOUT_ENABLED','1');monkeypatch.setenv('PHOTO_SCOUT_HUMAN_FREE_PREVIEW','1')
    monkeypatch.setenv('PHOTO_SCOUT_POI_PROVIDER','google-places')
    settings=Settings(openai_api_key='fixture',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test',service_api_key='private')
    async def parse(*args):return intent.PhotoIntent(locationQuery=None,useMapCenter=True,
        searchBranches=mixed_branches(),photoStyles=[],radiusMeters=3000,preferences='Vintage cafe OR quiet restaurant',
        scoringIntent='(Vintage cafes by a lake) OR (quiet restaurants in a forest)',explanation='Two alternative groups',clarification=None)
    async def places(lat,lon,radius,queries,**kwargs):
        p={'id':'cafe','name':'Cafe','lat':0,'lon':-.005} if queries==['coffee shops'] else {'id':'restaurant','name':'Restaurant','lat':0,'lon':.015}
        return [p],{'status':'ok'}
    async def geometry(lat,lon,radius,kinds,path):return ([lake()] if kinds==['lake'] else [forest()]),[],{'status':'ok'}
    async def images(lat,lon,radius,pois,**kwargs):return [
        {'id':'good','lat':0,'lon':-.005,'provider':'google-street-view','poi':{'id':'cafe'}},
        {'id':'wrong','lat':0,'lon':-.005,'provider':'google-street-view','poi':{'id':'restaurant'}}],{'google-street-view':{'status':'ok'}}
    async def score(settings,payload,rows,statuses):
        assert len(payload.searchBranches)==2 and payload.poiQueries==[]
        assert [r['id'] for r in rows]==['good'] and rows[0]['eligibleSearchBranchIndexes']==[0]
        return {'spots':[],'poiResults':[],'summary':'Verified','sources':statuses}
    monkeypatch.setattr(intent,'parse_intent',parse);monkeypatch.setattr(routes,'nearby_places',places)
    monkeypatch.setattr(routes,'fetch_region',geometry);monkeypatch.setattr(routes,'candidates',images);monkeypatch.setattr(routes,'explore',score)
    with TestClient(create_app(settings=settings)) as client:
        headers={'Authorization':'Bearer private'}
        resolved=client.post('/photo-scout/v1/resolve',headers=headers,json={'lat':0,'lon':0,'query':'lake cafes or forest restaurants'}).json()
        assert len(resolved['searchParameters']['searchBranches'])==2
        for parameters in [resolved['searchParameters'],{'lat':0,'lon':0,'query':'lake cafes or forest restaurants'}]:
            r=client.post('/photo-scout/v1/preview',headers=headers,json=parameters)
            assert r.status_code==200,r.text
            assert r.json()['searchPlan']['mergeStrategy']=='branch-union'
        search=client.post('/photo-scout/v1/search',headers=headers,json=resolved['searchParameters'])
        assert search.status_code==200 and search.json()['searchCounts']['returnedCandidates']==2
        # A catalog may not be reused with edited branch semantics.
        catalog=client.post('/photo-scout/v1/pois',headers=headers,json=resolved['searchParameters']).json()
        selected={**resolved['searchParameters'],'selectedPoiIds':['cafe'],'poiCatalogToken':catalog['poiCatalogToken']}
        assert client.post('/photo-scout/v1/preview',headers=headers,json=selected).status_code==200
        changed={**resolved['searchParameters'],'selectedPoiIds':['cafe'],'poiCatalogToken':catalog['poiCatalogToken']}
        changed['searchBranches'][0]['geographicKinds']=['forest']
        assert client.post('/photo-scout/v1/preview',headers=headers,json=changed).status_code==422


def test_branch_visual_requirements_are_part_of_score_cache_identity():
    from agentic_services.photo_scout.score_cache import ScoreCache
    from agentic_services.photo_scout.routes import ExploreRequest
    a=ExploreRequest(lat=0,lon=0,searchBranches=mixed_branches())
    changed=mixed_branches();changed[0]['visualIntent']='Modern lakeside cafes'
    b=ExploreRequest(lat=0,lon=0,searchBranches=changed)
    assert ScoreCache.key({'id':'view'},a,'model','prompt')!=ScoreCache.key({'id':'view'},b,'model','prompt')
