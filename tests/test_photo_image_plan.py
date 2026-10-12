import asyncio
import json
from types import SimpleNamespace

import pytest
from agentic_services.photo_scout import image_plan as plans, costs


@pytest.fixture(autouse=True)
def automatic(monkeypatch):
    monkeypatch.setenv('PHOTO_SCOUT_IMAGE_MODEL','auto')
    monkeypatch.delenv('GEMINI_API_KEY',raising=False)
    monkeypatch.delenv('PHOTO_SCOUT_VERTEX_IMAGE_ENABLED',raising=False)


def settings(tmp_path):
    return SimpleNamespace(openai_model='gpt-6.1-sol',database_path=tmp_path/'db')


def test_single_available_model_keeps_high_without_planner_call(tmp_path):
    result=asyncio.run(plans.plan_image(None,settings(tmp_path),{'pose':'standing'}))
    assert result['selectedModel']==plans.OPENAI_IMAGE
    assert result['steps'][-1]['quality']=='high'
    assert result['selectionSource']=='availability'
    assert result['allowRuntimeModelSwitch'] is False


@pytest.mark.parametrize('selected',[plans.OPENAI_IMAGE,plans.GOOGLE_IMAGE])
def test_planner_selects_once_from_both_options_with_cost_accounting(tmp_path,monkeypatch,selected):
    monkeypatch.setenv('GEMINI_API_KEY','fixture')
    calls=[];config=settings(tmp_path)
    async def parse(**kwargs):
        calls.append(kwargs)
        value=json.loads(kwargs['input'])
        assert {m['model'] for m in value['availableModels']}=={plans.OPENAI_IMAGE,plans.GOOGLE_IMAGE}
        assert 'fixture' not in kwargs['input']
        assert value['task']['composition']=='full_body'
        return SimpleNamespace(output_parsed=plans.ImageDecision(model=selected,reason='Fits this composition.'),usage={'input_tokens':100,'output_tokens':20})
    client=SimpleNamespace(responses=SimpleNamespace(parse=parse))
    @costs.tracked_task(config.database_path,'selfie')
    async def run():
        result=await plans.plan_image(client,config,{'composition':'full_body','reference':'private source'})
        assert costs.summary()['stages'][0]['stage']=='image-planning'
        return result
    result=asyncio.run(run())
    assert result['selectedModel']==selected and result['selectionSource']=='planner'
    assert result['steps'][-1]['model']==selected
    assert len(calls)==1 and result['allowRuntimeModelSwitch'] is False


@pytest.mark.parametrize('failure',['timeout','invalid'])
def test_planning_failure_defaults_before_execution(tmp_path,monkeypatch,failure):
    monkeypatch.setenv('GEMINI_API_KEY','fixture')
    async def parse(**kwargs):
        if failure=='timeout':raise TimeoutError()
        return SimpleNamespace(output_parsed=None)
    result=asyncio.run(plans.plan_image(SimpleNamespace(responses=SimpleNamespace(parse=parse)),settings(tmp_path),{}))
    assert result['selectedModel']==plans.OPENAI_IMAGE and result['selectionSource']=='planning-default'


def test_operator_override_and_unconfigured_google(tmp_path,monkeypatch):
    monkeypatch.setenv('PHOTO_SCOUT_IMAGE_MODEL',plans.OPENAI_IMAGE)
    monkeypatch.setenv('GEMINI_API_KEY','fixture')
    assert asyncio.run(plans.plan_image(None,settings(tmp_path),{}))['selectionSource']=='operator'
    monkeypatch.setenv('PHOTO_SCOUT_IMAGE_MODEL',plans.GOOGLE_IMAGE)
    monkeypatch.delenv('GEMINI_API_KEY')
    with pytest.raises(ValueError):asyncio.run(plans.plan_image(None,settings(tmp_path),{}))
    monkeypatch.setenv('PHOTO_SCOUT_VERTEX_PROJECT','test-project')
    assert len(plans.available_models())==1
    monkeypatch.setenv('PHOTO_SCOUT_VERTEX_IMAGE_ENABLED','1')
    assert len(plans.available_models())==2
