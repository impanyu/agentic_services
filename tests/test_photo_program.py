import asyncio
from pathlib import Path
import pytest
from pydantic import ValidationError
from agentic_services.photo_scout.program import SearchProgram,Places,Area,execute_program
from agentic_services.photo_scout.search import SearchParameters,SearchProviders,search_locations,SearchUnavailable
from test_photo_search import lake,forest


def scenic():
    return {'steps':[
        {'id':'lake','tool':'search_geography','geographicKinds':['lake']},
        {'id':'parks','tool':'search_places','queries':['lakeside parks'],'discoveryHints':True},
        {'id':'shore_parks','tool':'filter_geography','inputs':['parks','lake']},
        {'id':'shore_points','tool':'sample_geography','inputs':['lake']},
        {'id':'result','tool':'union','inputs':['shore_parks','shore_points'],'weights':[1,4]},
    ],'output':'result'}


def mixed():
    return {'steps':[
        {'id':'lake','tool':'search_geography','geographicKinds':['lake']},
        {'id':'forest','tool':'search_geography','geographicKinds':['forest']},
        {'id':'cafes','tool':'search_places','queries':['coffee shops'],'visualIntent':'Vintage cafes'},
        {'id':'restaurants','tool':'search_places','queries':['restaurants'],'visualIntent':'Quiet restaurants'},
        {'id':'a','tool':'filter_geography','inputs':['cafes','lake']},
        {'id':'b','tool':'filter_geography','inputs':['restaurants','forest']},
        {'id':'result','tool':'union','inputs':['a','b']},
    ],'output':'result'}


def providers(unavailable=False):
    async def places(lat,lon,radius,queries,**kwargs):
        assert kwargs['limit']==60
        name='cafe' if queries==['coffee shops'] else 'restaurant' if queries==['restaurants'] else 'park'
        return [{'id':name+'-lake','name':name,'lat':0,'lon':-.005},
                {'id':name+'-forest','name':name,'lat':0,'lon':.015}],{'status':'unavailable' if unavailable else 'ok'}
    async def geography(lat,lon,radius,kinds,path):
        paths=[{'id':'trail','name':'Trail','geometry':{'type':'LineString','coordinates':[[-.005,-.003],[-.005,.003]]}}]
        return [lake()] if kinds==['lake'] else [forest()],paths,{'status':'ok'}
    return SearchProviders(places,None,geography)


def run(program,ps=None):
    return asyncio.run(search_locations(SearchParameters(lat=0,lon=0,radius=3000,searchProgram=program),
        database_path=Path('/unused'),providers=ps or providers()))


def test_scenery_plan_is_filtered_named_places_or_sampled_shore():
    result=run(scenic())
    assert result.plan.mergeStrategy=='tool-program'
    assert any(p['id']=='park-lake' for p in result.places)
    assert all(p['id']!='park-forest' for p in result.places)
    assert any(p['id'].startswith('geo:') for p in result.places)
    assert len(result.places)<=50
    assert [s['id'] for s in result.status['executionTrace']]==['lake','parks','shore_parks','shore_points','result']


def test_program_preserves_correlated_targets_and_camera_constraints():
    result=run(mixed())
    assert [p['id'] for p in result.places]==['cafe-lake','restaurant-forest']
    rows=[{'id':'cafe-good','lat':0,'lon':-.005,'poi':{'id':'cafe-lake'}},
          {'id':'cafe-wrong','lat':0,'lon':.015,'poi':{'id':'cafe-lake'}},
          {'id':'restaurant-good','lat':0,'lon':.015,'poi':{'id':'restaurant-forest'}},
          {'id':'restaurant-wrong','lat':0,'lon':-.005,'poi':{'id':'restaurant-forest'}}]
    filtered=result.program_execution.filter_images(rows)
    assert [r['id'] for r in filtered]==['cafe-good','restaurant-good']
    assert filtered[0]['eligibleSearchPaths'][0]['targetQueries']==[['coffee shops']]
    assert filtered[0]['eligibleSearchPaths'][0]['visualIntents']==['Vintage cafes']


def test_same_source_call_is_shared_and_independent_tools_are_parallel():
    seen=[];gate=asyncio.Event()
    async def places(lat,lon,radius,queries,**kwargs):
        seen.append('places')
        if len(seen)==2:gate.set()
        await asyncio.wait_for(gate.wait(),1)
        return [{'id':'one','lat':0,'lon':-.005}],{'status':'ok'}
    async def geography(*args):
        seen.append('geography')
        if len(seen)==2:gate.set()
        await asyncio.wait_for(gate.wait(),1)
        return [lake()],[],{'status':'ok'}
    plan={'steps':[{'id':'p','tool':'search_places','queries':['cafes']},
        {'id':'p2','tool':'search_places','queries':['cafes']},
        {'id':'lake','tool':'search_geography','geographicKinds':['lake']},
        {'id':'both','tool':'intersection','inputs':['p','p2']},
        {'id':'result','tool':'filter_geography','inputs':['both','lake']}],'output':'result'}
    result=run(plan,SearchProviders(places,None,geography))
    assert len(seen)==2 and len(result.places)==1
    assert result.program_execution.output.paths['one'][0]['targets']==['p','p2']


@pytest.mark.parametrize('bad',[
 {'steps':[{'id':'a','tool':'exec','queries':['cafes']}],'output':'a'},
 {'steps':[{'id':'a','tool':'union','inputs':['a','b']}],'output':'a'},
 {'steps':[{'id':'a','tool':'search_geography','geographicKinds':['lake']}],'output':'a'},
 {'steps':[{'id':'a','tool':'search_places','queries':['cafes']},{'id':'a','tool':'area_imagery'}],'output':'a'},
 {'steps':[{'id':'a','tool':'search_places','queries':['cafes']},{'id':'unused','tool':'search_places','queries':['motels']}],'output':'a'},
 {'steps':[{'id':'a','tool':'search_places','queries':['cafes']},{'id':'b','tool':'sample_geography','inputs':['a']}],'output':'b'},
 {'steps':[{'id':'a','tool':'search_places','queries':['cafes'],'url':'https://secret.test'}],'output':'a'},
])
def test_invalid_programs_fail_before_provider_calls(bad):
    with pytest.raises(ValidationError):SearchProgram.model_validate(bad)


def test_spatial_alternatives_and_exclusion_are_executed():
    plan={'steps':[{'id':'p','tool':'search_places','queries':['cafes']},
        {'id':'geo','tool':'search_geography','geographicKinds':['lake','sea']},
        {'id':'result','tool':'filter_geography','inputs':['p','geo'],'combination':'any'}],'output':'result'}
    async def geography(*args):return [lake()],[],{'status':'ok'}
    ps=providers();ps.geography=geography
    result=run(plan,ps);assert [p['id'] for p in result.places]==['park-lake']
    plan['steps'][-1]['exclude']=True
    result=run(plan,ps);assert [p['id'] for p in result.places]==['park-forest']
    images=[{'lat':0,'lon':-.005,'poi':{'id':'park-forest'}},{'lat':0,'lon':.015,'poi':{'id':'park-forest'}}]
    assert len(result.program_execution.filter_images(images))==1


def test_optional_scenic_hints_can_fail_without_losing_shore_samples():
    assert run(scenic(),providers(unavailable=True)).places
    with pytest.raises(SearchUnavailable):run(mixed(),providers(unavailable=True))


def test_area_plan_uses_area_imagery_and_no_sources():
    result=run({'steps':[{'id':'result','tool':'area_imagery'}],'output':'result'})
    assert result.plan.mergeStrategy=='area-imagery'
    assert len(result.imagery_targets)==50
    assert isinstance(result.program_execution.output,Area)


def test_program_cannot_silently_ignore_other_target_parameters():
    with pytest.raises(ValidationError):SearchParameters(lat=0,lon=0,searchProgram=scenic(),poiQueries=['cafes'])


def test_visual_intent_on_one_union_does_not_mutate_another_path():
    plan={'steps':[{'id':'p','tool':'search_places','queries':['cafes']},
        {'id':'a','tool':'union','inputs':['p','p'],'visualIntent':'Vintage'},
        {'id':'b','tool':'union','inputs':['p','p'],'visualIntent':'Modern'},
        {'id':'result','tool':'union','inputs':['a','b']}],'output':'result'}
    result=run(plan)
    paths=result.program_execution.output.paths['park-lake']
    assert {tuple(p['visualIntents']) for p in paths}=={('Vintage',),('Modern',)}


def test_feature_tools_are_composable_and_attributes_are_retained():
    query={'label':'Fountains','filters':[{'key':'amenity','value':'fountain'}],'proximityMeters':100}
    plan={'steps':[{'id':'features','tool':'search_features','osmFeatures':[query]},
        {'id':'points','tool':'feature_points','inputs':['features']},
        {'id':'cafes','tool':'search_places','queries':['coffee shops']},
        {'id':'nearby','tool':'filter_features','inputs':['cafes','features']},
        {'id':'result','tool':'union','inputs':['points','nearby']}],'output':'result'}
    async def features(*args):return [[{'id':'fountain','name':'Fountain','lat':0,'lon':-.005}]],{'status':'ok'}
    ps=providers();ps.osm_features=features
    result=run(plan,ps)
    assert {p['id'] for p in result.places}=={'fountain','cafe-lake'}
    assert result.program_execution.filter_images([{'id':'f','lat':0,'lon':-.005}])
    assert not result.program_execution.filter_images([{'id':'far','lat':0,'lon':.015}])


def test_program_is_in_cache_identity():
    from agentic_services.photo_scout.routes import ExploreRequest
    from agentic_services.photo_scout.score_cache import ScoreCache
    a=ExploreRequest(lat=0,lon=0,searchProgram=mixed())
    changed=mixed();changed['steps'][2]['visualIntent']='Modern cafes'
    b=ExploreRequest(lat=0,lon=0,searchProgram=changed)
    assert ScoreCache.key({'id':'image'},a,'model','prompt')!=ScoreCache.key({'id':'image'},b,'model','prompt')


def test_source_query_budget_is_checked_before_execution():
    steps=[{'id':f'p{i}','tool':'search_places','queries':[f'query {i}']} for i in range(9)]
    steps+=[{'id':'a','tool':'union','inputs':[f'p{i}' for i in range(6)]},
            {'id':'b','tool':'union','inputs':[f'p{i}' for i in range(6,9)]},
            {'id':'out','tool':'union','inputs':['a','b']}]
    with pytest.raises(ValidationError,match='eight source'):SearchProgram(steps=steps,output='out')


def test_resolve_and_human_workflow_execute_generated_program(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from agentic_services.config import Settings
    from agentic_services.main import create_app
    from agentic_services.photo_scout import intent,routes
    monkeypatch.setenv('PHOTO_SCOUT_ENABLED','1');monkeypatch.setenv('PHOTO_SCOUT_HUMAN_FREE_PREVIEW','1')
    monkeypatch.setenv('PHOTO_SCOUT_POI_PROVIDER','google-places')
    settings=Settings(openai_api_key='fixture',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test',service_api_key='private')
    async def parse(*args):return intent.PhotoIntent(locationQuery=None,useMapCenter=True,
        searchProgram=mixed(),photoStyles=[],radiusMeters=3000,preferences='Vintage cafes OR quiet restaurants',
        scoringIntent='(Vintage lake cafes) OR (quiet woodland restaurants)',explanation='Composed search tools',clarification=None)
    ps=providers()
    async def images(lat,lon,radius,pois,**kwargs):return [
        {'id':'good','lat':0,'lon':-.005,'provider':'google-street-view','poi':{'id':'cafe-lake'}},
        {'id':'wrong','lat':0,'lon':-.005,'provider':'google-street-view','poi':{'id':'restaurant-forest'}}],{'google-street-view':{'status':'ok'}}
    async def score(settings,payload,rows,statuses):
        assert payload.searchProgram is not None
        assert [r['id'] for r in rows]==['good']
        assert rows[0]['eligibleSearchPaths'][0]['targetQueries']==[['coffee shops']]
        return {'spots':[],'poiResults':[],'summary':'Verified','sources':statuses}
    monkeypatch.setattr(intent,'parse_intent',parse);monkeypatch.setattr(routes,'nearby_places',ps.places)
    monkeypatch.setattr(routes,'fetch_region',ps.geography);monkeypatch.setattr(routes,'candidates',images);monkeypatch.setattr(routes,'explore',score)
    with TestClient(create_app(settings=settings)) as client:
        h={'Authorization':'Bearer private'}
        resolved=client.post('/photo-scout/v1/resolve',headers=h,json={'lat':0,'lon':0,'query':'lake cafes or forest restaurants'}).json()
        assert resolved['searchPlan']['mergeStrategy']=='tool-program'
        parameters=resolved['searchParameters']
        for values in [parameters,{'lat':0,'lon':0,'query':'lake cafes or forest restaurants'}]:
            response=client.post('/photo-scout/v1/preview',headers=h,json=values)
            assert response.status_code==200,response.text
            assert len(response.json()['sources']['google-places']['executionTrace'])==7
        tool=client.get('/photo-scout/v1/search-tools',headers=h)
        assert tool.status_code==200 and len(tool.json()['tools'])==10
        catalog=client.post('/photo-scout/v1/pois',headers=h,json=parameters).json()
        selected={**parameters,'selectedPoiIds':['cafe-lake'],'poiCatalogToken':catalog['poiCatalogToken']}
        assert client.post('/photo-scout/v1/preview',headers=h,json=selected).status_code==200
        selected['searchProgram']['steps'][4]['exclude']=True
        assert client.post('/photo-scout/v1/preview',headers=h,json=selected).status_code==422
