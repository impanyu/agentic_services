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
        return ModelResponse(output=[ResponseFunctionToolCall(type='function_call',id='fc_1',call_id='call_1',name='submit_candidates',arguments='{"explanation":"No coverage"}')],usage=Usage(),response_id='r1')
    async def stream_response(self,*args,**kwargs):
        raise NotImplementedError
        yield


def test_submit_stops_sdk_without_another_model_turn(tmp_path):
    d=state(tmp_path);model=SubmitModel()
    agent=Agent(name='test',model=model,tools=d.tools(),tool_use_behavior={'stop_at_tool_names':['submit_candidates']})
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
    parts,roles=geometry_parts({'type':'relation','members':[{'role':'outer','geometry':[{'lon':1,'lat':2},{'lon':2,'lat':2},{},{'lon':5,'lat':6}]}]})
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
