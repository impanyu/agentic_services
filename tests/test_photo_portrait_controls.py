"""Verify UI choices survive job storage and reach generation for each control."""
import asyncio, base64, io, json
from types import SimpleNamespace
from pathlib import Path
from typing import get_args
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from agentic_services.config import Settings
from agentic_services.main import create_app
from agentic_services.photo_scout import portraits

CONTROLS={'style':list(portraits.PORTRAIT_STYLES),'composition':list(portraits.COMPOSITIONS),'posture':list(portraits.POSTURES),'weather':list(portraits.WEATHERS),'expression':list(portraits.EXPRESSIONS),'framing':['auto','current','90','60','45']}

def test_ui_and_api_expose_the_same_control_values():
    source=Path('photo-scout-site/app.js').read_text()
    for key,values in CONTROLS.items():
        assert set(values)==set(get_args(portraits.PortraitRequest.model_fields[key].annotation))
        line=next(line for line in source.splitlines() if ("const portraitStyles=" if key=='style' else "'"+key+"',[") in line)
        for value in values:assert "['"+value+"'," in line

@pytest.mark.parametrize('index',range(8))
def test_controls_persist_and_reach_the_generator(tmp_path,monkeypatch,index):
    selected={key:values[index%len(values)] for key,values in CONTROLS.items()}
    monkeypatch.setenv('PHOTO_SCOUT_IMAGE_MODEL','gpt-image-2.5-sunburst')
    output=io.BytesIO();Image.new('RGB',(32,32),'blue').save(output,'PNG');raw=output.getvalue();calls=[];backgrounds=[]
    class Client:
        def __init__(self,**kwargs):self.images=self
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def edit(self,**kwargs):calls.append(kwargs);return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(raw).decode())])
    async def check(*args):return 1
    async def background(client,reference,model,framing):backgrounds.append({'framing':framing});return raw,'source',{'scene':{}}
    monkeypatch.setattr(portraits,'AsyncOpenAI',Client);monkeypatch.setattr(portraits,'check_subjects',check);monkeypatch.setattr(portraits,'prepare_background',background)
    settings=Settings(openai_api_key='fixture',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test',service_api_key='private')
    app=create_app(settings=settings);client=TestClient(app,base_url='https://api.test',headers={'Authorization':'Bearer private'})
    payload={**selected,'portrait':'data:image/png;base64,'+base64.b64encode(raw).decode(),'background':'https://www.google.com/maps/@?map_action=pano&pano=abc&heading=45&pitch=0&fov=60','provider':'google-street-view','place':'Test park','pose':'Keep the building visible'}
    response=client.post('/photo-scout/v1/portraits',json=payload);assert response.status_code==202
    job=response.json();assert asyncio.run(app.state.process_photo_portrait())
    result=client.get('/photo-scout/v1/portraits/'+job['id'],headers={'X-Report-Token':job['token']}).json();assert result['state']=='complete'
    for key,value in selected.items():assert result['context']['generation'][key]==value
    assert backgrounds[0]['framing']==selected['framing']
    prompt=calls[0]['prompt']
    for key,mapping in [('style',portraits.PORTRAIT_STYLES),('composition',portraits.COMPOSITIONS),('posture',portraits.POSTURES),('weather',portraits.WEATHERS),('expression',portraits.EXPRESSIONS)]:assert mapping[selected[key]] in prompt
    assert 'Keep the building visible' in prompt
    assert ('FULL-BODY ACCEPTANCE REQUIREMENT' in prompt)==(selected['composition']=='full_body')
