import asyncio
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from agentic_services.photo_scout import intent
from agentic_services.photo_scout.planner import PlannerIntent, PlannerProgram
from agentic_services.photo_scout.score_cache import ScoreCache


def output():
    return dict(locationQuery=None,useMapCenter=True,radiusMeters=5000,photoStyles=[],
        sourceCoverage=[{'subject':'motels','usefulTools':['search_places'],'stepIds':['p'],'reason':'Named businesses; map tagging adds little to this category search.'}],
        requirements=[],scoringIntent='Motels with red roofs',preferences='Red roofs',explanation='Nearby motels',
        searchProgram={'steps':[{'id':'p','tool':'search_places','queries':['motels']},
            {'id':'i','tool':'collect_images','inputs':['p']},
            {'id':'s','tool':'score_images','inputs':['i']},
            {'id':'r','tool':'rank_results','inputs':['s']}],'output':'r'})


def test_tool_contract_excludes_irrelevant_arguments_and_requires_delivery():
    data=output();data['searchProgram']['steps'][0]['combination']='any'
    with pytest.raises(ValidationError):PlannerIntent.model_validate(data)
    data=output();data['searchProgram']['steps'][0]['inputs']=['other']
    with pytest.raises(ValidationError):PlannerIntent.model_validate(data)
    with pytest.raises(ValidationError):PlannerProgram(steps=[{'id':'a','tool':'area_imagery'}],output='a')


def test_condition_routes_are_real_steps_and_compilation_preserves_strength():
    data=output();data['requirements']=[{'expression':'red roofs','strength':'preferred','route':'visual','stepIds':['p']}]
    plan=PlannerIntent.model_validate(data)
    assert plan.searchProgram.compile().steps[0].queries==['motels']
    for change in [{'stepIds':['unknown']},{'route':'geography'},{'route':'places','stepIds':[]}]:
        broken=output();broken['requirements']=[dict(data['requirements'][0],**change)]
        with pytest.raises(ValidationError):PlannerIntent.model_validate(broken)


def test_invalid_plan_repaired_once_with_original_request_and_diagnostics(monkeypatch):
    calls=[]
    class Client:
        def __init__(self,**kw):self.responses=self
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def parse(self,**kw):
            calls.append(kw)
            if len(calls)==1:
                broken=output();broken['searchProgram']['steps'][1]['inputs']=['future']
                PlannerIntent.model_validate(broken)
            return SimpleNamespace(output_parsed=PlannerIntent.model_validate(output()))
    monkeypatch.setattr(intent,'AsyncOpenAI',Client)
    settings=SimpleNamespace(openai_api_key='fixture',openai_model='scorer',photo_scout_intent_model='planner',photo_scout_intent_reasoning='low')
    result=asyncio.run(intent.parse_intent(settings,intent.IntentRequest(query='motels with red roof',lat=0,lon=0)))
    assert len(calls)==2 and 'validationErrors' in calls[1]['input']
    assert calls[0]['model']=='planner' and calls[0]['reasoning']=={'effort':'low'}
    assert result.searchProgram.complete and result.clarification is None


def test_provider_errors_not_retried(monkeypatch):
    calls=[]
    class Client:
        def __init__(self,**kw):self.responses=self
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def parse(self,**kw):calls.append(kw);raise RuntimeError('provider unavailable')
    monkeypatch.setattr(intent,'AsyncOpenAI',Client)
    with pytest.raises(RuntimeError):asyncio.run(intent.parse_intent(SimpleNamespace(openai_api_key='fixture',openai_model='test'),intent.IntentRequest(lat=0,lon=0)))
    assert len(calls)==1


def test_second_invalid_plan_stops(monkeypatch):
    calls=[]
    class Client:
        def __init__(self,**kw):self.responses=self
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def parse(self,**kw):
            calls.append(kw);broken=output();broken['searchProgram']['output']='unknown';PlannerIntent.model_validate(broken)
    monkeypatch.setattr(intent,'AsyncOpenAI',Client)
    with pytest.raises(ValidationError):asyncio.run(intent.parse_intent(SimpleNamespace(openai_api_key='fixture',openai_model='test'),intent.IntentRequest(lat=0,lon=0)))
    assert len(calls)==2


def test_requirement_strength_changes_cache_identity():
    from agentic_services.photo_scout.routes import ExploreRequest
    from agentic_services.photo_scout.planner import Requirement
    r=Requirement(expression='red roofs',strength='required',route='visual',stepIds=[])
    a=ExploreRequest(lat=0,lon=0,requirements=[r])
    b=a.model_copy(update={'requirements':[r.model_copy(update={'strength':'preferred'})]})
    assert ScoreCache.key({'id':'image'},a,'model','prompt')!=ScoreCache.key({'id':'image'},b,'model','prompt')


def test_branch_requirements_stay_with_their_eligible_images():
    from pathlib import Path
    from test_photo_program import mixed, providers
    from agentic_services.photo_scout.search import SearchParameters, search_locations
    from agentic_services.photo_scout.planner import Requirement
    requirements=[Requirement(expression='Vintage',strength='preferred',route='visual',stepIds=['cafes']),
        Requirement(expression='Quiet',strength='preferred',route='visual',stepIds=['restaurants']),
        Requirement(expression='No crowds',strength='forbidden',route='visual',stepIds=[])]
    p=SearchParameters(lat=0,lon=0,radius=3000,searchProgram=mixed(),requirements=requirements)
    result=asyncio.run(search_locations(p,database_path=Path('/unused'),providers=providers()))
    rows=[{'id':'c','lat':0,'lon':-.005,'poi':{'id':'cafe-lake'}},
          {'id':'r','lat':0,'lon':.015,'poi':{'id':'restaurant-forest'}}]
    filtered=result.program_execution.filter_images(rows)
    assert filtered[0]['eligibleSearchPaths'][0]['requirementIndexes']==[0,2]
    assert filtered[1]['eligibleSearchPaths'][0]['requirementIndexes']==[1,2]


def test_optional_geometry_provider_failure_keeps_unfiltered_named_candidates():
    from pathlib import Path
    from test_photo_program import providers
    from agentic_services.photo_scout.search import SearchProviders, SearchParameters, search_locations
    from agentic_services.photo_scout.planner import Requirement
    ps=providers()
    async def unavailable(*args):return [],[],{'status':'unavailable'}
    program={'steps':[{'id':'p','tool':'search_places','queries':['parks'],'discoveryHints':True},
        {'id':'g','tool':'search_geography','geographicKinds':['forest']},
        {'id':'s','tool':'sample_geography','inputs':['g']},
        {'id':'u','tool':'union','inputs':['p','s']}],'output':'u'}
    p=SearchParameters(lat=0,lon=0,radius=3000,searchProgram=program,
        requirements=[Requirement(expression='Optional forest viewpoints',strength='preferred',route='geography',stepIds=['g','s'])])
    result=asyncio.run(search_locations(p,database_path=Path('/unused'),providers=SearchProviders(ps.places,None,unavailable)))
    assert result.places and result.status['executionTrace'][1]['source']['status']=='unavailable'


def test_source_coverage_requires_all_declared_sources_and_real_references():
    data=output();data['sourceCoverage'][0]['usefulTools'].append('search_features')
    with pytest.raises(ValidationError,match='omits a declared useful'):PlannerIntent.model_validate(data)
    data=output();data['sourceCoverage']=[]
    with pytest.raises(ValidationError,match='sourceCoverage rationale'):PlannerIntent.model_validate(data)
    data=output();data['sourceCoverage'][0]['stepIds'].append('invented')
    with pytest.raises(ValidationError,match='unknown step'):PlannerIntent.model_validate(data)


def test_dual_source_artwork_union_keeps_both_routes():
    data=output();data['searchProgram']['steps'][:1]=[
        {'id':'p','tool':'search_places','queries':['sculptures','雕塑']},
        {'id':'f','tool':'search_features','osmFeatures':[{'label':'sculptures','kind':'tagged','filters':[{'key':'tourism','value':'artwork','required':True},{'key':'artwork_type','value':'sculpture','required':True}]}]},
        {'id':'fp','tool':'feature_points','inputs':['f']},
        {'id':'u','tool':'union','inputs':['p','fp']}]
    data['searchProgram']['steps'][4]['inputs']=['u']
    data['sourceCoverage']=[{'subject':'sculptures','usefulTools':['search_places','search_features'],'stepIds':['p','f'],'reason':'Named artworks and unlisted mapped sculptures complement each other.'}]
    result=PlannerIntent.model_validate(data)
    assert result.searchProgram.compile().steps[3].inputs==['p','fp']


def test_typo_plan_repair_receives_rejected_coverage_without_weakening_subject(monkeypatch):
    import json
    calls=[]
    good=output()
    good.update(photoStyles=[],requirements=[{'expression':'beautiful woman','strength':'required','route':'visual','stepIds':[]}],scoringIntent='A beautiful woman visible in the image',preferences='Portrait subject',sourceCoverage=[])
    good['searchProgram']['steps'][0]={'id':'p','tool':'area_imagery'}
    class Client:
        def __init__(self,**kw):self.responses=self
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def parse(self,**kw):
            calls.append(kw)
            if len(calls)==1:
                broken=output();broken['sourceCoverage'][0]['usefulTools'].append('search_features')
                PlannerIntent.model_validate(broken)
            return SimpleNamespace(output_parsed=PlannerIntent.model_validate(good))
    monkeypatch.setattr(intent,'AsyncOpenAI',Client)
    r=asyncio.run(intent.parse_intent(SimpleNamespace(openai_api_key='fixture',openai_model='test'),intent.IntentRequest(query='beautidul woman',lat=40,lon=-96,radius=20000,photoStyles=['waterside'])))
    repair=json.loads(calls[1]['input'])
    assert repair['request']['query']=='beautidul woman'
    assert repair['repair']['rejectedPlan']['sourceCoverage'][0]['usefulTools']==['search_places','search_features']
    assert r.requirements[0].expression=='beautiful woman'
    assert r.requirements[0].strength=='required'
    assert r.searchProgram.steps[0].tool=='area_imagery'
    assert not r.photoStyles and not r.sourceCoverage

@pytest.mark.parametrize('action',['help','unsupported','uninterpretable'])
def test_nonsearch_actions_require_feedback_and_no_executable_plan(action):
    data=output();data.update(action=action,feedback='Try a photo search near Chicago.',searchProgram=None,requirements=[],sourceCoverage=[])
    assert PlannerIntent.model_validate(data).searchProgram is None
    with pytest.raises(ValidationError):PlannerIntent.model_validate(data|{'feedback':''})
    with pytest.raises(ValidationError):PlannerIntent.model_validate(data|{'searchProgram':output()['searchProgram']})
    with pytest.raises(ValidationError):PlannerIntent.model_validate(data|{'action':'search'})


def test_evidence_mode_and_subject_role_flow_to_search_and_cache():
    from agentic_services.photo_scout.routes import ExploreRequest
    from agentic_services.photo_scout.search import SearchParameters
    r={'expression':'near a lake','strength':'required','route':'geography','evidence':'spatial','stepIds':['g']}
    a=ExploreRequest(lat=0,lon=0,subjectRole='portrait-background',requirements=[r])
    b=a.model_copy(update={'subjectRole':'existing-subject'})
    assert SearchParameters.model_validate(a.model_dump(include=set(SearchParameters.model_fields))).subjectRole=='portrait-background'
    assert a.requirements[0].evidence=='spatial'
    assert ScoreCache.key({'id':'image'},a,'model','prompt')!=ScoreCache.key({'id':'image'},b,'model','prompt')


def test_empty_input_is_repaired_to_ui_search_not_conversational_feedback(monkeypatch):
    calls=[]
    class Client:
        def __init__(self,**kw):self.responses=self
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def parse(self,**kw):
            calls.append(kw)
            if len(calls)==1:
                data=output();data.update(action='uninterpretable',searchProgram=None,sourceCoverage=[],feedback='Enter a query')
                return SimpleNamespace(output_parsed=PlannerIntent.model_validate(data))
            return SimpleNamespace(output_parsed=PlannerIntent.model_validate(output()))
    monkeypatch.setattr(intent,'AsyncOpenAI',Client)
    r=asyncio.run(intent.parse_intent(SimpleNamespace(openai_api_key='fixture',openai_model='test'),intent.IntentRequest(query='  ',lat=0,lon=0)))
    assert r.action=='search' and r.searchProgram.complete and len(calls)==2
    assert 'UI-driven search' in calls[1]['input']
