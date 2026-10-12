import asyncio
import base64
import io
import sqlite3
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from agentic_services.photo_scout import image_generation as images, costs


def png(size=(1024,1024), format='PNG'):
    output=io.BytesIO();Image.new('RGB',size,'green').save(output,format=format)
    return output.getvalue()


def response(parts=None, reason='STOP'):
    return {'candidates':[{'finishReason':reason,'content':{'parts':parts if parts is not None else [
        {'inlineData':{'mimeType':'image/png','data':base64.b64encode(png()).decode()}}]}}],
        'usageMetadata':{'promptTokenCount':4383,'thoughtsTokenCount':916,
                        'candidatesTokensDetails':[{'modality':'IMAGE','tokenCount':1120}]}}


@pytest.mark.parametrize('api_key',[True,False])
def test_google_transport_preserves_two_references_and_has_no_retry(monkeypatch,api_key):
    if api_key: monkeypatch.setenv('GEMINI_API_KEY','private-gemini-key')
    else: monkeypatch.delenv('GEMINI_API_KEY',raising=False)
    monkeypatch.setenv('PHOTO_SCOUT_VERTEX_PROJECT','test-project')
    real=httpx.AsyncClient;calls=[]
    subject,background=png(),png(format='JPEG')
    def handler(request):
        import json
        calls.append(request)
        if request.url.host=='metadata.google.internal':
            assert request.headers['Metadata-Flavor']=='Google'
            return httpx.Response(200,json={'access_token':'private-token'})
        body=json.loads(request.content)
        assert [base64.b64decode(p['inlineData']['data']) for p in body['contents'][0]['parts'][1:]]==[subject,background]
        assert body['generationConfig']['imageConfig']=={'aspectRatio':'1:1','imageSize':'1K'}
        assert 'tools' not in body
        if api_key: assert request.headers['x-goog-api-key']=='private-gemini-key'
        else: assert request.headers['Authorization']=='Bearer private-token'
        return httpx.Response(200,json=response())
    monkeypatch.setattr(images.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler)))
    result=asyncio.run(images.google_response(images.DEFAULT_MODEL,subject,background,'image/jpeg','same production prompt'))
    assert images.google_image(result)==png()
    assert len(calls)==(1 if api_key else 2)


def test_provider_failure_is_sanitized_and_not_retried(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY','private-key')
    real=httpx.AsyncClient;calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(429,json={'error':{'message':'private echoed photo and prompt'}})
    monkeypatch.setattr(images.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler)))
    with pytest.raises(images.ImageProviderError) as error:
        asyncio.run(images.google_response(images.DEFAULT_MODEL,png(),png(),'image/png','private prompt'))
    assert error.value.status_code==429 and 'private' not in str(error.value)
    assert len(calls)==1


@pytest.mark.parametrize('body',[response([]),response(reason='SAFETY'),response([
    {'thought':True,'inlineData':{'mimeType':'image/png','data':base64.b64encode(png()).decode()}}]),
    response([{'inlineData':{'mimeType':'image/png','data':'invalid'}}]),
    response([{'inlineData':{'mimeType':'image/png','data':base64.b64encode(png((20,20))).decode()}}])])
def test_nonfinal_blocked_or_invalid_image_is_not_delivered(body):
    with pytest.raises(images.ImageProviderError): images.google_image(SimpleNamespace(value=body))


def test_google_cost_counts_thinking_and_preserves_unknown_usage(tmp_path):
    @costs.tracked_task(tmp_path/'db','selfie')
    async def run():
        async def generate(): return SimpleNamespace(usage=response()['usageMetadata'],value=response([]))
        value=await costs.observe('image-generation',images.DEFAULT_MODEL,generate())
        with pytest.raises(images.ImageProviderError): images.google_image(value)
        assert costs.summary()['estimatedKnownUsd']==.047045
        costs.record('image-generation',images.DEFAULT_MODEL,{})
        assert costs.summary()['unknownCostEvents']==1
    asyncio.run(run())


def test_default_google_portrait_is_delivered_with_accounting(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from agentic_services.config import Settings
    from agentic_services.main import create_app
    from agentic_services.photo_scout import portraits
    monkeypatch.delenv('PHOTO_SCOUT_IMAGE_MODEL',raising=False)
    raw=png()
    class Client:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
    async def subjects(*args):return 1
    async def background(*args):return raw,'https://upload.wikimedia.org/test.png',{'scene':{}}
    async def generate(model,subject,scene,mime,prompt):
        assert model==images.DEFAULT_MODEL and subject==scene==raw
        assert 'Test park' in prompt
        return SimpleNamespace(value=response(),usage=response()['usageMetadata'])
    monkeypatch.setattr(portraits,'AsyncOpenAI',Client)
    monkeypatch.setattr(portraits,'check_subjects',subjects)
    monkeypatch.setattr(portraits,'prepare_background',background)
    monkeypatch.setattr(portraits,'google_response',generate)
    settings=Settings(openai_api_key='fixture',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test',service_api_key='private')
    app=create_app(settings=settings);client=TestClient(app,base_url='https://api.test');auth={'Authorization':'Bearer private'}
    body={'portrait':'data:image/png;base64,'+base64.b64encode(raw).decode(),
          'background':'https://upload.wikimedia.org/test.png','provider':'wikimedia-commons','place':'Test park'}
    job=client.post('/photo-scout/v1/portraits',json=body,headers=auth).json()
    assert asyncio.run(app.state.process_photo_portrait())
    path='/photo-scout/v1/portraits/'+job['id'];auth['X-Report-Token']=job['token']
    value=client.get(path,headers=auth).json()
    assert value['state']=='complete'
    assert value['context']['generationConfig']==images.configuration(images.DEFAULT_MODEL)
    assert value['context']['costAccounting']['estimatedKnownUsd']==.047045
    assert client.get(path+'/image',headers=auth).content==raw
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute('SELECT photo,payload FROM photo_portraits').fetchone()==(None,None)
