import asyncio
import json
from types import SimpleNamespace
import pytest
from agents import Runner, Agent, RunConfig
from agents.tool_context import ToolContext
from agents.models.interface import Model, ModelResponse
from agents.usage import Usage
from openai.types.responses import ResponseFunctionToolCall
from agentic_services.photo_scout.discovery_agent import Discovery
from agentic_services.photo_scout.routes import ExploreRequest
from agentic_services.photo_scout import discovery_agent, portraits


def state(tmp_path):
    return Discovery(SimpleNamespace(database_path=tmp_path/'db'),ExploreRequest(lat=40,lon=-96,radius=500),'job')


def test_bounds_and_inspection_required(tmp_path):
    d=state(tmp_path)
    with pytest.raises(ValueError):d.point(41,-96)
    with pytest.raises(ValueError):d.point(float('nan'),-96)
    tool=next(t for t in d.tools() if t.name=='manage_candidate')
    result=asyncio.run(tool.on_invoke_tool(ToolContext(None,tool_name=tool.name,tool_call_id="call",tool_arguments="{}"),json.dumps({'view_id':'invented','action':'add','reason':'nice'})))
    assert not d.selected and 'Inspect actual image' in str(result)


def test_freeze_checkpoint_and_recover(tmp_path):
    d=state(tmp_path);d.views['real']={'id':'real','lat':40,'lon':-96};d.inspected.add('real');d.selected['real']='Water visible'
    d.submit('Ready')
    restored=state(tmp_path)
    assert restored.submitted and restored.selected=={'real':'Water visible'}
    with pytest.raises(ValueError):restored.tick('search_places')


class SubmitModel(Model):
    def __init__(self):self.calls=0
    async def get_response(self,*args,**kwargs):
        self.calls+=1
        name='submit_candidates'
        arguments='{"explanation":"Agent decided no useful coverage"}'
        return ModelResponse(output=[ResponseFunctionToolCall(type='function_call',id=f'fc_{self.calls}',call_id=f'call_{self.calls}',name=name,arguments=arguments)],usage=Usage(),response_id=f'r{self.calls}')
    async def stream_response(self,*args,**kwargs):
        raise NotImplementedError
        yield


def test_submit_stops_sdk_without_another_model_turn(tmp_path):
    d=state(tmp_path);model=SubmitModel()
    agent=Agent(name='test',model=model,tools=d.tools(),tool_use_behavior=d.finish_tools)
    asyncio.run(Runner.run(agent,'Submit',run_config=RunConfig(tracing_disabled=True)))
    assert d.submitted and model.calls==1


def test_inspect_keeps_actual_location_heading_and_fov(tmp_path,monkeypatch):
    d=state(tmp_path)
    d.views['original']={'id':'original','provider':'google-street-view','lat':40,'lon':-96,'imageUrl':'google-streetview://pano/0','title':'View'}
    refs=[]
    async def load(ref):refs.append(ref);return 'data:image/jpeg;base64,/9j/'
    monkeypatch.setattr(discovery_agent.sources,'image_data',load)
    tool=next(t for t in d.tools() if t.name=='inspect_view')
    output=asyncio.run(tool.on_invoke_tool(ToolContext(None,tool_name=tool.name,tool_call_id="call",tool_arguments="{}"),json.dumps({'view_id':'original','heading':137,'fov':60})))
    assert len(output)==2 and refs==['google-streetview://pano/137/0/60']
    row=d.views['google:pano:137:f60']
    assert row['lat']==40 and row['viewHeadingDegrees']==137 and row['viewFovDegrees']==60
    assert row['id'] in d.inspected
    assert portraits.background_reference('google-street-view',row['sourceUrl'])==refs[0]


def test_map_contains_geo_and_stable_coordinate_markers(tmp_path):
    d=state(tmp_path);d.features=[{'kind':'water','geometryParts':[[[-96,40],[-95.999,40.001]]]}]
    d.pois['p']={'id':'p','lat':40,'lon':-96}
    result=d.render_map(40,-96,500)
    metadata=json.loads(result[0].text)
    assert metadata['markers'][0]['id']=='p'
    assert result[1].image_url.startswith('data:image/png;base64,')


def test_budget_still_allows_submission(tmp_path):
    d=state(tmp_path);d.calls=d.max_calls
    with pytest.raises(ValueError):d.tick('find_streetview')
    d.tick('submit_candidates');assert d.submit('Budget reached')['submitted']


def test_spatial_context_distinguishes_land_water_and_hole(tmp_path):
    d=state(tmp_path)
    outer=[[-96.001,39.999],[-95.999,39.999],[-95.999,40.001],[-96.001,40.001],[-96.001,39.999]]
    inner=[[-96.0001,39.9999],[-95.9999,39.9999],[-95.9999,40.0001],[-96.0001,40.0001],[-96.0001,39.9999]]
    d.features=[{'id':'lake','kind':'water','tags':{},'geometryParts':[outer],'memberRoles':[]}]
    assert d.spatial_context(40,-96)['nearbyFeatures'][0]['insideMappedWater'] is True
    assert d.spatial_context(40,-95.998)['nearbyFeatures'][0]['insideMappedWater'] is False
    d.features[0].update(geometryParts=[outer,inner],memberRoles=['outer','inner'])
    assert d.spatial_context(40,-96)['nearbyFeatures'][0]['insideMappedWater'] is False


def test_exploration_reason_does_not_invalidate_score_cache():
    from agentic_services.photo_scout.score_cache import ScoreCache
    payload=ExploreRequest(lat=40,lon=-96)
    row={'id':'view','lat':40,'lon':-96,'imageUrl':'google-streetview://pano/90'}
    assert ScoreCache.key({**row,'explorationReason':'Nice view'},payload,'model','rules')==ScoreCache.key({**row,'explorationReason':'Water visible'},payload,'model','rules')


def test_usage_audit_checkpoint_does_not_break_submission(tmp_path):
    d=state(tmp_path);d.submit('Done');d.audit.append({'model':'model','inputTokens':10})
    d.checkpoint('scoring');assert state(tmp_path).submitted


def test_cropped_geometry_does_not_invent_connecting_segments():
    from agentic_services.photo_scout.discovery_agent import geometry_parts
    parts,roles=geometry_parts({'type':'relation','members':[{'role':'outer','geometry':[{'lon':1,'lat':2},{'lon':2,'lat':2},None,{}, {'lon':5,'lat':6}]}]})
    assert parts==[[[1,2],[2,2]],[[5,6]]] and roles==['outer','outer']


def test_compaction_preserves_tool_call_correlations():
    from agentic_services.photo_scout.discovery_agent import compact_model_input
    items=[]
    for i in range(5):
        items.extend([{'type':'function_call','call_id':str(i),'name':'inspect_view'},
                      {'type':'function_call_output','call_id':str(i),'output':[{'type':'input_text','text':'view-id'},{'type':'input_image','image_url':'data:image/png;base64,huge'}]}])
    result=compact_model_input(SimpleNamespace(model_data=SimpleNamespace(input=items,instructions='rules')))
    assert [r.get('call_id') for r in result.input]==[r.get('call_id') for r in items]
    assert len(result.input[1]['output'])==1 and len(result.input[-1]['output'])==2


def invoke(d,name,params):
    tool=next(t for t in d.tools() if t.name==name)
    return asyncio.run(tool.on_invoke_tool(ToolContext(None,tool_name=name,tool_call_id='trace-call',tool_arguments=json.dumps(params)),json.dumps(params)))


def test_submit_without_review_or_coverage_quota_is_logged(tmp_path):
    d=state(tmp_path)
    result=invoke(d,'submit_candidates',{'explanation':'Agent decided further exploration is not useful'})
    assert result['submitted'] and d.submitted
    assert d.finish_tools(None,[]).is_final_output
    event=state(tmp_path).audit[-1]
    assert event['outcome']=='completed' and event['error'] is None
    assert event['parameters']['explanation']=='Agent decided further exploration is not useful'
    assert event['durationSeconds']>=0 and event['callId']=='trace-call'


def test_optional_review_reports_gaps_without_blocking_submit(tmp_path):
    d=state(tmp_path);d.views['one']={'id':'one','lat':40,'lon':-96};d.inspected={'one'}
    p={'comparison':'One view looks promising.', 'unexplored_places':['Other shoreline'], 'coverage_limitations':''}
    review=invoke(d,'review_exploration',p)
    assert review['advisoryOnly'] and review['missingDecisions']==['one']
    assert 'ready' not in review and 'targetPositions' not in review
    invoke(d,'submit_candidates',{'explanation':'The Agent decided to stop'})
    assert d.submitted and state(tmp_path).review['distinctPositions']==1


def test_tool_failure_remains_logged(tmp_path):
    d=state(tmp_path)
    result=invoke(d,'inspect_view',{'view_id':'unknown','heading':0,'fov':80})
    assert 'Unknown view' in result and not d.finish_tools(None,[]).is_final_output
    event=state(tmp_path).audit[-1]
    assert event['outcome']=='failed' and event['errorType']=='ValueError'


def test_batch_views_preserve_ids_and_partial_failure_evidence(tmp_path,monkeypatch):
    d=state(tmp_path)
    d.views['original']={'id':'original','provider':'google-street-view','lat':40,'lon':-96,'imageUrl':'google-streetview://pano/0','title':'View'}
    async def load(ref):
        if '/90/' in ref:raise RuntimeError('credential-bearing URL must not leak')
        return 'data:image/jpeg;base64,pixels'
    monkeypatch.setattr(discovery_agent.sources,'image_data',load)
    output=invoke(d,'inspect_views',{'view_id':'original','headings':[0,90,180],'fov':80})
    assert len(d.inspected)==2 and len(output)==5
    assert d.images==3 and d.calls==1
    event=state(tmp_path).audit[-1]
    assert event['parameters']['headings']==[0,90,180]
    assert 'RuntimeError' in str(event['result']) and 'credential-bearing' not in str(event)
    assert 'base64' not in str(event) and 'pixels' not in str(event)
    assert event['counts']['inspectedViews']==2


def test_further_discovery_invalidates_coverage_review(tmp_path):
    d=state(tmp_path);d.review={'advisoryOnly':True};d.tick('inspect_views')
    assert d.review is None


def test_per_turn_usage_survives_restart(tmp_path):
    d=state(tmp_path);hooks=discovery_agent.ExplorationHooks(d)
    agent=SimpleNamespace(model=SimpleNamespace(model='explorer'))
    async def run():
        await hooks.on_llm_start(None,agent,None,[])
        await hooks.on_llm_end(None,agent,ModelResponse(output=[],usage=Usage(input_tokens=123,output_tokens=8),response_id='response'))
    asyncio.run(run())
    event=state(tmp_path).audit[-1]
    assert event['event']=='model_turn' and event['inputTokens']==123 and event['outputTokens']==8
    assert event['durationSeconds']>=0


def test_explorer_does_not_receive_legacy_result_count(tmp_path,monkeypatch):
    captured=[]
    class Client:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
    async def run(agent,prompt,**kwargs):
        data=json.loads(prompt);captured.append(data)
        assert 'limit' not in data['request']
        tool=next(t for t in agent.tools if t.name=='submit_candidates')
        params={'explanation':'No matching image evidence was found.'}
        await tool.on_invoke_tool(ToolContext(None,tool_name=tool.name,tool_call_id='submit',tool_arguments=json.dumps(params)),json.dumps(params))
        return SimpleNamespace(context_wrapper=SimpleNamespace(usage=SimpleNamespace(input_tokens=0,output_tokens=0,requests=1)))
    monkeypatch.setattr(discovery_agent,'AsyncOpenAI',Client)
    monkeypatch.setattr(discovery_agent.Runner,'run',run)
    settings=SimpleNamespace(database_path=tmp_path/'db',openai_api_key='fixture')
    _,_,_,result=asyncio.run(discovery_agent.discover(settings,ExploreRequest(lat=40,lon=-96,radius=20000,limit=3),'regression',original_query='architecture'))
    assert captured and result['candidateCount']==0


def test_multi_place_batch_downloads_concurrently_and_keeps_partial_results(tmp_path,monkeypatch):
    d=state(tmp_path);active=0;peak=0;loaded=[]
    d.views['pano']={'id':'pano','provider':'google-street-view','lat':40,'lon':-96,'imageUrl':'google-streetview://pano/0','title':'Street'}
    d.views['photo']={'id':'photo','provider':'wikimedia-commons','lat':40,'lon':-96,'imageUrl':'https://upload.wikimedia.org/photo.jpg','title':'Photo'}
    async def load(ref):
        nonlocal active,peak
        loaded.append(ref);active+=1;peak=max(peak,active)
        await asyncio.sleep(.01);active-=1
        if '/90/' in ref:raise RuntimeError('secret must never appear')
        return 'data:image/jpeg;base64,pixels'
    monkeypatch.setattr(discovery_agent.sources,'image_data',load)
    views=[{'view_id':'pano','heading':h,'fov':120} for h in (0,45,90,135,180)]
    views.extend([{'view_id':'photo','heading':0,'fov':120},{'view_id':'pano','heading':0,'fov':120}])
    output=invoke(d,'inspect_batch',{'views':views})
    assert peak==4 and len(loaded)==6 and d.images==6
    assert len(d.inspected)==5 and d.calls==1
    assert sum(isinstance(v,discovery_agent.ToolOutputImage) for v in output)==5
    texts=[json.loads(v.text) for v in output if isinstance(v,discovery_agent.ToolOutputText)]
    assert sum(v.get('status')=='failed' for v in texts)==1
    assert {v['view']['provider'] for v in texts if 'view' in v}=={'google-street-view','wikimedia-commons'}
    assert 'secret must never appear' not in str(d.audit)
    assert len(state(tmp_path).inspected)==5


def test_batch_budget_is_checked_before_any_download(tmp_path,monkeypatch):
    d=state(tmp_path);d.images=d.max_images-1;loaded=[]
    d.views['pano']={'id':'pano','provider':'google-street-view','imageUrl':'google-streetview://pano/0'}
    async def load(ref):loaded.append(ref)
    monkeypatch.setattr(discovery_agent.sources,'image_data',load)
    result=invoke(d,'inspect_views',{'view_id':'pano','headings':[0,45],'fov':120})
    assert 'image budget' in result and not loaded and d.images==d.max_images-1
