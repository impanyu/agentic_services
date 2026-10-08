import asyncio,base64,hashlib,io,json,sqlite3,time
from types import SimpleNamespace
from fastapi.testclient import TestClient
from PIL import Image
from agentic_services.config import Settings
from agentic_services.main import create_app
from agentic_services.photo_scout.tasks import TaskStore,GUEST_COOKIE
from agentic_services.photo_scout.accounts import COOKIE
import agentic_services.photo_scout.routes as routes
import agentic_services.photo_scout.portraits as portraits


def setup(tmp_path,monkeypatch):
    monkeypatch.setenv('PHOTO_SCOUT_ENABLED','1');monkeypatch.setenv('PHOTO_SCOUT_HUMAN_FREE_PREVIEW','1')
    settings=Settings(openai_api_key='fixture',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test',service_api_key='private')
    app=create_app(settings=settings)
    client=TestClient(app,base_url='https://api.test',headers={'Authorization':'Bearer private'})
    return settings,app,client


def test_guest_search_admitted_before_resolution_recovers_after_restart_and_keeps_result(tmp_path,monkeypatch):
    settings,app,client=setup(tmp_path,monkeypatch);calls=[]
    response=client.get('/photo-scout/v1/tasks');assert response.json()['items']==[]
    assert 'HttpOnly' in response.headers['set-cookie'] and 'Secure' in response.headers['set-cookie']
    async def resolve(settings,payload):
        calls.append('resolve');assert payload.radius==1000
        return {'locations':[{'lat':48.8,'lon':2.3,'label':'Paris'}],'radiusMeters':20000,'limit':5,'photoStyles':['urban'],'preferences':'architecture','explanation':'Paris, 20 km'}
    async def nearby(*args):return [],{'status':'ok','provider':'openstreetmap'}
    async def candidates(*args,**kwargs):return [],{}
    async def explore(settings,payload,*args):
        calls.append('score');assert payload.lat==48.8 and payload.radius==20000 and payload.photoStyles==['urban']
        return {'spots':[],'summary':'Saved result'}
    monkeypatch.setattr(routes,'resolve_intent',resolve);monkeypatch.setattr(routes,'nearby_pois',nearby);monkeypatch.setattr(routes,'candidates',candidates);monkeypatch.setattr(routes,'explore',explore)
    response=client.post('/photo-scout/v1/jobs',headers={'X-Request-Token':'s'*32},json={'lat':0,'lon':0,'query':'Urban Paris, 20 km, top 5'})
    assert response.status_code==202 and calls==[];job=response.json()['jobId']
    assert client.get('/photo-scout/v1/tasks').json()['items'][0]['state']=='queued'
    restarted=create_app(settings=settings);assert asyncio.run(restarted.state.process_photo_preview());assert calls==['resolve','score']
    restored=TestClient(restarted,base_url='https://api.test',headers={'Authorization':'Bearer private'});restored.cookies.set(GUEST_COOKIE,client.cookies.get(GUEST_COOKIE))
    tasks=restored.get('/photo-scout/v1/tasks').json()['items'];assert tasks[0]['state']=='complete' and tasks[0]['context']['radius']==20000
    assert 'reportToken' not in json.dumps(tasks)
    result=restored.get('/photo-scout/v1/report/'+job);assert result.status_code==200 and result.json()['result']['summary']=='Saved result'
    stranger=TestClient(restarted,base_url='https://api.test',headers={'Authorization':'Bearer private'})
    assert stranger.get('/photo-scout/v1/tasks').json()['items']==[];assert stranger.get('/photo-scout/v1/report/'+job).status_code==404
    assert not asyncio.run(restarted.state.process_photo_preview()) and calls==['resolve','score']
    with sqlite3.connect(settings.database_path) as db:db.execute('UPDATE photo_scout_jobs SET created=?',(time.time()-2*86400,))
    assert restored.get('/photo-scout/v1/report/'+job).status_code==200


def test_guest_portrait_recovery_owns_image_without_exposing_tokens_or_upload(tmp_path,monkeypatch):
    settings,app,client=setup(tmp_path,monkeypatch);client.get('/photo-scout/v1/tasks')
    buffer=io.BytesIO();Image.new('RGB',(16,16),'green').save(buffer,format='PNG');raw=buffer.getvalue()
    class Client:
        def __init__(self,**kwargs):self.images=self;self.responses=self
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def parse(self,**kwargs):return SimpleNamespace(output_parsed=portraits.PersonCheck(person_count=2))
        async def edit(self,**kwargs):return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(raw).decode())])
    async def background(*args):return 'data:image/png;base64,'+base64.b64encode(raw).decode()
    monkeypatch.setattr(portraits,'AsyncOpenAI',Client);monkeypatch.setattr(portraits,'image_data',background)
    response=client.post('/photo-scout/v1/portraits',json={'portrait':'data:image/png;base64,'+base64.b64encode(raw).decode(),'provider':'google-street-view','background':'https://www.google.com/maps/@?map_action=pano&pano=abc&heading=90','place':'Park','lat':40,'lon':-96})
    assert response.status_code==202 and response.json()['expiresInSeconds']==7*86400;job=response.json()['id']
    restarted=create_app(settings=settings);assert asyncio.run(restarted.state.process_photo_portrait())
    restored=TestClient(restarted,base_url='https://api.test',headers={'Authorization':'Bearer private'});restored.cookies.set(GUEST_COOKIE,client.cookies.get(GUEST_COOKIE))
    task=restored.get('/photo-scout/v1/tasks').json()['items'][0];assert task['state']=='complete' and task['context']['poi']=={'lat':40,'lon':-96}
    path='/photo-scout/v1/portraits/'+job
    assert restored.get(path).json()['state']=='complete';assert restored.get(path+'/image').content==raw
    stranger=TestClient(restarted,base_url='https://api.test',headers={'Authorization':'Bearer private'})
    assert stranger.get(path).status_code==404 and stranger.get(path+'/image').status_code==404
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute('SELECT photo,payload FROM photo_portraits').fetchone()==(None,None)
        db.execute('UPDATE photo_portraits SET expires=0')
    assert restored.get(path+'/image').status_code==404;assert restored.get('/photo-scout/v1/tasks').json()['items']==[]


def test_account_tasks_follow_user_across_browsers_but_are_hidden_after_logout(tmp_path,monkeypatch):
    settings,app,client=setup(tmp_path,monkeypatch);client.get('/photo-scout/v1/tasks')
    job=client.post('/photo-scout/v1/jobs',headers={'X-Request-Token':'x'*32},json={'lat':0,'lon':0}).json()['jobId']
    from starlette.requests import Request
    request=Request({'type':'http','headers':[(b'cookie',(GUEST_COOKIE+'='+client.cookies.get(GUEST_COOKIE)).encode())]})
    TaskStore(settings.database_path).attach_user(request,'alice')
    with sqlite3.connect(settings.database_path) as db:
        for user in ['alice','bob']:db.execute('INSERT INTO photo_sessions VALUES(?,?,?,?,?)',(hashlib.sha256(user.encode()).hexdigest(),user,json.dumps({'id':user,'name':user}),user+'-csrf',time.time()+3600))
    other=TestClient(app,base_url='https://api.test',headers={'Authorization':'Bearer private'});other.cookies.set(COOKIE,'alice')
    assert other.get('/photo-scout/v1/tasks').json()['items'][0]['id']==job;assert other.get('/photo-scout/v1/report/'+job).status_code==200
    client.cookies.set(COOKIE,'alice');assert client.get('/photo-scout/v1/report/'+job).status_code==200
    assert client.post('/photo-scout/v1/auth/logout',headers={'Origin':'https://aisoup.net','X-CSRF-Token':'alice-csrf'}).status_code==200
    assert client.get('/photo-scout/v1/tasks').json()['items']==[];assert client.get('/photo-scout/v1/report/'+job).status_code==404
    other.cookies.set(COOKIE,'bob');assert other.get('/photo-scout/v1/tasks').json()['items']==[]
