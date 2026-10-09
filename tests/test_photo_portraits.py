import asyncio,base64,io,sqlite3
import pytest
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
    assert portraits.background_reference('google-street-view','https://www.google.com/maps/@?map_action=pano&pano=abc&heading=90&pitch=-20')=='google-streetview://abc/90/-20'


@pytest.mark.parametrize('image_model',['gpt-image-2.5-sunburst','gpt-image-1.5'])
def test_private_job_edits_both_images_and_removes_upload(tmp_path,monkeypatch,image_model):
    monkeypatch.setenv('PHOTO_SCOUT_IMAGE_MODEL',image_model)
    calls=[];raw=photo()
    class Client:
        images=None
        def __init__(self,**kwargs):self.images=self;self.responses=self
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def parse(self,**kwargs):return SimpleNamespace(output_parsed=portraits.SubjectCheck(human_count=1))
        async def edit(self,**kwargs):
            calls.append(kwargs);return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(raw).decode())])
    async def background(ref):return 'data:image/png;base64,'+base64.b64encode(raw).decode()
    monkeypatch.setattr(portraits,'AsyncOpenAI',Client);monkeypatch.setattr(portraits,'image_data',background)
    settings=Settings(openai_api_key='fixture',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test',service_api_key='private')
    app=create_app(settings=settings);client=TestClient(app,base_url='https://api.test');auth={'Authorization':'Bearer private'}
    body={'portrait':'data:image/png;base64,'+base64.b64encode(raw).decode(),'background':'https://www.google.com/maps/@?map_action=pano&pano=abc&heading=90&pitch=-20','provider':'google-street-view','place':'Test park'}
    assert client.post('/photo-scout/v1/portraits',json=body).status_code==401
    assert client.post('/photo-scout/v1/portraits',json=body|{'style':'unsupported'},headers=auth).status_code==422
    for field in ['posture','weather','expression']:
        assert client.post('/photo-scout/v1/portraits',json=body|{field:'unsupported'},headers=auth).status_code==422
    body.update(posture='walking',weather='golden_hour',expression='big_smile')
    result=client.post('/photo-scout/v1/portraits',json=body,headers=auth);assert result.status_code==202
    job=result.json();path='/photo-scout/v1/portraits/'+job['id'];owned=auth|{'X-Report-Token':job['token']}
    assert TestClient(app,base_url='https://api.test').get(path,headers=auth).status_code==404
    assert client.get(path,headers=owned).json()['state']=='queued'
    assert asyncio.run(app.state.process_photo_portrait())
    assert len(calls[0]['image'])==2;assert calls[0]['model']==image_model;assert calls[0]['n']==1
    if image_model=='gpt-image-1.5':
        assert calls[0]['input_fidelity']=='high';assert calls[0]['quality']=='high'
    else:
        assert 'input_fidelity' not in calls[0];assert calls[0]['quality']=='max'
    assert 'keep the original clothing' in calls[0]['prompt']
    assert 'mid-step walking' in calls[0]['prompt']
    assert 'golden-hour light' in calls[0]['prompt']
    assert 'cheerful broad smile' in calls[0]['prompt']
    assert 'relight the entire scene and subjects together' in calls[0]['prompt']
    completed=client.get(path,headers=owned).json();assert completed['state']=='complete'
    assert completed['context']['viewHeadingDegrees']==90 and completed['context']['viewPitchDegrees']==-20
    assert completed['context']['generation']=={'style':'natural','posture':'walking','weather':'golden_hour','expression':'big_smile','directions':''}
    history=client.get('/photo-scout/v1/tasks',headers=auth).json()
    assert history['items'][0]['context']['generation']==completed['context']['generation']
    image=client.get(path+'/image',headers=owned);assert image.content==raw;assert image.headers['cache-control']=='private, no-store'
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute('SELECT photo,payload FROM photo_portraits').fetchone()==(None,None)
        db.execute('UPDATE photo_guests SET expires=0')
    assert client.get(path,headers=owned).status_code==404


def test_phone_heic_and_48mp_jpeg_are_resized_without_metadata():
    # Phone-format content may be labelled JPEG by a browser/exporter.
    for kind,size in [('HEIF',(48,64)),('JPEG',(8064,6048))]:
        image=Image.new('RGB',size,'blue');exif=Image.Exif();exif[274]=6;exif[270]='private camera metadata'
        buffer=io.BytesIO();image.save(buffer,format=kind,exif=exif)
        cleaned=portraits.clean_photo('data:image/jpeg;base64,'+base64.b64encode(buffer.getvalue()).decode())
        with Image.open(io.BytesIO(cleaned)) as result:
            assert max(result.size)<=2048
            assert result.height>=result.width
            assert not result.getexif()


def test_oversized_upload_has_specific_error():
    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        portraits.clean_photo('data:image/jpeg;base64,'+base64.b64encode(b'x'*20_000_001).decode())
    assert error.value.status_code==413
    assert '20 MB' in error.value.detail


@pytest.mark.parametrize('count,kind',[(0,'human_count'),(3,'human_count'),(None,'human_count'),(2,'cartoon_count'),(1,'animal_count')])
def test_subject_check_blocks_empty_or_failed_checks_and_allows_people_cartoons_animals(tmp_path,monkeypatch,count,kind):
    calls=[];raw=photo()
    class Client:
        def __init__(self,**kwargs):self.responses=self;self.images=self
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def parse(self,**kwargs):
            calls.append(('check',kwargs))
            return SimpleNamespace(output_parsed=portraits.SubjectCheck(**{kind:count}) if count is not None else None)
        async def edit(self,**kwargs):
            calls.append(('edit',kwargs));return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(raw).decode())])
    async def background(ref):
        calls.append(('background',ref));return 'data:image/png;base64,'+base64.b64encode(raw).decode()
    monkeypatch.setattr(portraits,'AsyncOpenAI',Client);monkeypatch.setattr(portraits,'image_data',background)
    settings=Settings(openai_api_key='fixture',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test',service_api_key='private')
    app=create_app(settings=settings);client=TestClient(app);auth={'Authorization':'Bearer private'}
    body={'portrait':'data:image/png;base64,'+base64.b64encode(raw).decode(),'background':'https://www.google.com/maps/@?map_action=pano&pano=abc&heading=90','provider':'google-street-view','place':'Park'}
    if count:body['style']='cinematic'
    job=client.post('/photo-scout/v1/portraits',json=body,headers=auth).json()
    assert asyncio.run(app.state.process_photo_portrait())
    state=client.get('/photo-scout/v1/portraits/'+job['id'],headers=auth|{'X-Report-Token':job['token']}).json()
    assert calls[0][0]=='check';assert calls[0][1]['store'] is False
    if count:
        assert state['state']=='complete';assert [c[0] for c in calls]==['check','background','edit']
        assert 'cartoon' in calls[0][1]['instructions'] and 'animals' in calls[0][1]['instructions']
        assert 'EVERY visible foreground subject' in calls[-1][1]['prompt']
        assert 'do not turn them into real humans or animals' in calls[-1][1]['prompt']
        assert 'do not humanize them' in calls[-1][1]['prompt']
        assert 'thoughtful natural expression' in calls[-1][1]['prompt']
        assert 'Keep the original background, camera viewpoint' in calls[-1][1]['prompt']
    else:
        assert state['state']=='failed';assert len(calls)==1
        assert ('person, cartoon character or animal' if count==0 else 'Could not check') in state['error']
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute('SELECT photo,payload FROM photo_portraits').fetchone()==(None,None)
