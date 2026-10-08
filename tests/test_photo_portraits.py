import asyncio,base64,io,sqlite3
from types import SimpleNamespace
from PIL import Image
from fastapi.testclient import TestClient
from agentic_services.config import Settings
from agentic_services.main import create_app
import agentic_services.photo_scout.portraits as portraits


def photo():
    out=io.BytesIO();Image.new('RGB',(32,48),'green').save(out,format='PNG');return out.getvalue()


def test_portrait_validation_and_background_allowlist():
    raw=photo();clean=portraits.clean_photo('data:image/png;base64,'+base64.b64encode(raw).decode());assert clean.startswith(b'\x89PNG')
    import pytest
    from fastapi import HTTPException
    for bad in ['data:text/plain;base64,YQ==','data:image/png;base64,YQ==']:
        with pytest.raises(HTTPException):portraits.clean_photo(bad)
    with pytest.raises(HTTPException):portraits.background_reference('other','http://127.0.0.1/private')
    assert portraits.background_reference('google-street-view','https://www.google.com/maps/@?map_action=pano&pano=abc&heading=90')=='google-streetview://abc/90'


def test_private_job_edits_both_images_and_removes_upload(tmp_path,monkeypatch):
    calls=[];raw=photo()
    class Client:
        images=None
        def __init__(self,**kwargs):self.images=self
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def edit(self,**kwargs):
            calls.append(kwargs);return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(raw).decode())])
    async def background(ref):return 'data:image/png;base64,'+base64.b64encode(raw).decode()
    monkeypatch.setattr(portraits,'AsyncOpenAI',Client);monkeypatch.setattr(portraits,'image_data',background)
    settings=Settings(openai_api_key='fixture',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test',service_api_key='private')
    app=create_app(settings=settings);client=TestClient(app);auth={'Authorization':'Bearer private'}
    body={'portrait':'data:image/png;base64,'+base64.b64encode(raw).decode(),'background':'https://www.google.com/maps/@?map_action=pano&pano=abc&heading=90','provider':'google-street-view','place':'Test park'}
    assert client.post('/photo-scout/v1/portraits',json=body).status_code==401
    result=client.post('/photo-scout/v1/portraits',json=body,headers=auth);assert result.status_code==202
    job=result.json();path='/photo-scout/v1/portraits/'+job['id'];owned=auth|{'X-Report-Token':job['token']}
    assert client.get(path,headers=auth).status_code==404
    assert client.get(path,headers=owned).json()['state']=='queued'
    assert asyncio.run(app.state.process_photo_portrait())
    assert len(calls[0]['image'])==2;assert calls[0]['input_fidelity']=='high';assert calls[0]['n']==1
    assert client.get(path,headers=owned).json()['state']=='complete'
    image=client.get(path+'/image',headers=owned);assert image.content==raw;assert image.headers['cache-control']=='private, no-store'
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute('SELECT photo,payload FROM photo_portraits').fetchone()==(None,None)
        db.execute('UPDATE photo_portraits SET expires=0')
    assert client.get(path,headers=owned).status_code==404
