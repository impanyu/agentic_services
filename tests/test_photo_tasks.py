import asyncio,base64,hashlib,io,json,sqlite3,time
from types import SimpleNamespace
from fastapi.testclient import TestClient
from PIL import Image
from agentic_services.config import Settings
from agentic_services.main import create_app
from agentic_services.photo_scout.tasks import TaskStore,GUEST_COOKIE,prune_records
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
        async def parse(self,**kwargs):return SimpleNamespace(output_parsed=portraits.SubjectCheck(human_count=2))
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
        db.execute('UPDATE photo_guests SET expires=0')
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


def test_two_search_workers_score_independent_jobs_concurrently(tmp_path,monkeypatch):
    settings,app,client=setup(tmp_path,monkeypatch)
    async def nearby(*args):return [],{'status':'ok','provider':'openstreetmap'}
    async def candidates(*args,**kwargs):return [],{}
    monkeypatch.setattr(routes,'nearby_pois',nearby);monkeypatch.setattr(routes,'candidates',candidates)
    jobs=[client.post('/photo-scout/v1/jobs',headers={'X-Request-Token':str(i)*32},json={'lat':40+i,'lon':-96}).json()['jobId'] for i in range(2)]
    async def exercise():
        both=asyncio.Event();release=asyncio.Event();entered=[]
        async def explore(settings,payload,*args):
            entered.append(payload.lat)
            if len(entered)==2:both.set()
            await release.wait()
            return {'spots':[],'summary':str(payload.lat)}
        monkeypatch.setattr(routes,'explore',explore)
        workers=[asyncio.create_task(app.state.process_photo_preview()) for _ in range(2)]
        try:
            await asyncio.wait_for(both.wait(),2)
            assert sorted(entered)==[40,41]
        finally:release.set()
        assert await asyncio.gather(*workers)==[True,True]
    asyncio.run(exercise())
    results=[client.get('/photo-scout/v1/report/'+job).json() for job in jobs]
    assert [r['state'] for r in results]==['complete','complete']
    assert [r['result']['summary'] for r in results]==['40.0','41.0']


def test_two_portrait_workers_generate_concurrently_without_mixing_context(tmp_path,monkeypatch):
    settings,app,client=setup(tmp_path,monkeypatch)
    buffer=io.BytesIO();Image.new('RGB',(16,16),'green').save(buffer,format='PNG');encoded=base64.b64encode(buffer.getvalue()).decode()
    async def background(*args):return 'data:image/png;base64,'+encoded
    monkeypatch.setattr(portraits,'image_data',background)
    jobs=[]
    for i in range(2):
        r=client.post('/photo-scout/v1/portraits',json={'portrait':'data:image/png;base64,'+encoded,'provider':'google-street-view','background':'https://www.google.com/maps/@?map_action=pano&pano=abc&heading=90','place':'Park '+str(i),'lat':40+i,'lon':-96})
        assert r.status_code==202;jobs.append(r.json()['id'])
    async def exercise():
        both=asyncio.Event();release=asyncio.Event();entered=[]
        class Client:
            def __init__(self,**kwargs):self.images=self;self.responses=self
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            async def parse(self,**kwargs):return SimpleNamespace(output_parsed=portraits.SubjectCheck(human_count=2))
            async def edit(self,**kwargs):
                entered.append(kwargs)
                if len(entered)==2:both.set()
                await release.wait()
                return SimpleNamespace(data=[SimpleNamespace(b64_json=encoded)])
        monkeypatch.setattr(portraits,'AsyncOpenAI',Client)
        workers=[asyncio.create_task(app.state.process_photo_portrait()) for _ in range(2)]
        try:await asyncio.wait_for(both.wait(),2)
        finally:release.set()
        assert await asyncio.gather(*workers)==[True,True]
    asyncio.run(exercise())
    results=[client.get('/photo-scout/v1/portraits/'+job).json() for job in jobs]
    assert all(r['state']=='complete' for r in results)
    assert [r['context']['poi']['lat'] for r in results]==[40,41]


def test_five_active_tasks_combines_search_and_portrait_and_releases_finished_slots(tmp_path,monkeypatch):
    settings,app,client=setup(tmp_path,monkeypatch)
    jobs=[client.post('/photo-scout/v1/jobs',headers={'X-Request-Token':str(i)*32},json={'lat':40,'lon':-96}).json()['jobId'] for i in range(4)]
    buffer=io.BytesIO();Image.new('RGB',(16,16),'green').save(buffer,format='PNG')
    payload={'portrait':'data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode(),'provider':'google-street-view','background':'https://www.google.com/maps/@?map_action=pano&pano=abc&heading=90','place':'Park','lat':40,'lon':-96}
    assert client.post('/photo-scout/v1/portraits',json=payload).status_code==202
    rejected=client.post('/photo-scout/v1/jobs',headers={'X-Request-Token':'z'*32},json={'lat':40,'lon':-96})
    assert rejected.status_code==429 and '5 active tasks' in rejected.json()['detail']
    assert client.post('/photo-scout/v1/portraits',json=payload).status_code==429
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute('SELECT count(*) FROM photo_scout_jobs').fetchone()[0]==4
        assert db.execute('SELECT count(*) FROM photo_portraits').fetchone()[0]==1
        assert db.execute('SELECT runs FROM photo_portrait_budget').fetchone()[0]==1
        db.execute("UPDATE photo_scout_jobs SET state='failed' WHERE id=?",(jobs[0],))
    assert client.post('/photo-scout/v1/jobs',headers={'X-Request-Token':'z'*32},json={'lat':40,'lon':-96}).status_code==202
    # Retrying an admitted search does not consume another slot.
    assert client.post('/photo-scout/v1/jobs',headers={'X-Request-Token':'1'*32},json={'lat':40,'lon':-96}).status_code==202
    other=TestClient(app,base_url='https://api.test',headers={'Authorization':'Bearer private'})
    assert other.post('/photo-scout/v1/jobs',headers={'X-Request-Token':'o'*32},json={'lat':40,'lon':-96}).status_code==202


def test_account_limit_spans_sessions_and_concurrent_admission_is_atomic(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    settings,app,client=setup(tmp_path,monkeypatch)
    with sqlite3.connect(settings.database_path) as db:
        for token in ['alice-one','alice-two']:
            db.execute('INSERT INTO photo_sessions VALUES(?,?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),'alice','{}','csrf',time.time()+3600))
    client.cookies.set(COOKIE,'alice-one')
    for i in range(4):assert client.post('/photo-scout/v1/jobs',headers={'X-Request-Token':str(i)*32},json={'lat':40,'lon':-96}).status_code==202
    def submit(i):
        browser=TestClient(app,base_url='https://api.test',headers={'Authorization':'Bearer private'})
        browser.cookies.set(COOKIE,'alice-two')
        return browser.post('/photo-scout/v1/jobs',headers={'X-Request-Token':str(i)*32},json={'lat':40,'lon':-96}).status_code
    with ThreadPoolExecutor(max_workers=2) as executor:assert sorted(executor.map(submit,[5,6]))==[202,429]
    with sqlite3.connect(settings.database_path) as db:assert db.execute('SELECT count(*) FROM photo_task_owners WHERE user_id=?',('alice',)).fetchone()[0]==5


def test_retention_keeps_all_account_records_but_deletes_seven_day_guest_data(tmp_path,monkeypatch):
    settings,app,guest=setup(tmp_path,monkeypatch)
    guest.get('/photo-scout/v1/tasks')
    guest_hash=hashlib.sha256(guest.cookies.get(GUEST_COOKIE).encode()).hexdigest()
    now=time.time();old=now-365*86400
    with sqlite3.connect(settings.database_path) as db:
        db.execute('INSERT INTO photo_sessions VALUES(?,?,?,?,?)',(hashlib.sha256(b'alice').hexdigest(),'alice','{}','csrf',now+3600))
        for i in range(70):
            job='account-'+str(i)
            db.execute("INSERT INTO photo_scout_jobs(id,token_hash,payload,created,price,state,result) VALUES(?,?,?, ?,0,'complete',?)",(job,'hash','{}',old,json.dumps({'spots':[],'summary':'Saved'})))
            db.execute('INSERT INTO photo_task_owners VALUES(?,?,?,?,?,?)',('search',job,None,'alice',old,'{}'))
        for kind,job,user,created in [('search','guest-old',None,now-8*86400),('portrait','guest-photo',None,now-8*86400),('portrait','account-photo','alice',old)]:
            if kind=='search':db.execute("INSERT INTO photo_scout_jobs(id,token_hash,payload,created,price,state) VALUES(?,?,?, ?,0,'complete')",(job,'hash','{}',created))
            else:db.execute('INSERT INTO photo_portraits VALUES(?,?,?,?,?,?,?,?,?)',(job,'hash',created,created+7*86400,'complete',None,None,b'saved image',None))
            db.execute('INSERT INTO photo_task_owners VALUES(?,?,?,?,?,?)',(kind,job,guest_hash,user,created,'{}'))
            db.execute('UPDATE photo_guests SET expires=? WHERE hash=?',(now-1,guest_hash))
    # Startup migrates existing signed-in photos before any worker pruning.
    restarted=create_app(settings=settings)
    account=TestClient(restarted,base_url='https://api.test',headers={'Authorization':'Bearer private'});account.cookies.set(COOKIE,'alice')
    data=account.get('/photo-scout/v1/tasks').json()
    assert data['retention']=='permanent' and data['searchRetentionDays'] is None and data['photoRetentionDays'] is None
    assert len(data['items'])==71 and all(t['expiresAt'] is None for t in data['items'])
    assert account.get('/photo-scout/v1/portraits/account-photo/image').content==b'saved image'
    assert account.get('/photo-scout/v1/report/account-0').status_code==200
    assert guest.get('/photo-scout/v1/tasks').json()['items']==[]
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute("SELECT count(*) FROM photo_task_owners WHERE user_id='alice'").fetchone()[0]==71
        assert not db.execute("SELECT 1 FROM photo_scout_jobs WHERE id='guest-old'").fetchone()
        assert not db.execute("SELECT 1 FROM photo_portraits WHERE id='guest-photo'").fetchone()


def test_signin_promotes_guest_photo_and_search_to_permanent_storage(tmp_path,monkeypatch):
    from starlette.requests import Request
    settings,app,client=setup(tmp_path,monkeypatch);client.get('/photo-scout/v1/tasks')
    job=client.post('/photo-scout/v1/jobs',headers={'X-Request-Token':'p'*32},json={'lat':40,'lon':-96}).json()['jobId']
    token=client.cookies.get(GUEST_COOKIE);now=time.time()
    with sqlite3.connect(settings.database_path) as db:
        db.execute('INSERT INTO photo_portraits VALUES(?,?,?,?,?,?,?,?,?)',('photo','hash',now,now+7*86400,'complete',None,None,b'kept',None))
        db.execute('INSERT INTO photo_task_owners VALUES(?,?,?,?,?,?)',('portrait','photo',hashlib.sha256(token.encode()).hexdigest(),None,now,'{}'))
    request=Request({'type':'http','headers':[(b'cookie',(GUEST_COOKIE+'='+token).encode())]})
    TaskStore(settings.database_path).attach_user(request,'alice')
    with sqlite3.connect(settings.database_path) as db:
        db.execute('INSERT INTO photo_sessions VALUES(?,?,?,?,?)',(hashlib.sha256(b'alice').hexdigest(),'alice','{}','csrf',now+3600))
        db.execute('UPDATE photo_scout_jobs SET created=? WHERE id=?',(now-365*86400,job))
        db.execute('UPDATE photo_portraits SET created=? WHERE id=?',(now-365*86400,'photo'))
        assert db.execute("SELECT expires FROM photo_portraits WHERE id='photo'").fetchone()[0]>now+365*86400
    client.cookies.set(COOKIE,'alice')
    assert len(client.get('/photo-scout/v1/tasks').json()['items'])==2
    assert client.get('/photo-scout/v1/portraits/photo/image').content==b'kept'


def test_guest_retention_uses_last_visit_not_record_age_and_polling_does_not_renew(tmp_path,monkeypatch):
    settings,app,client=setup(tmp_path,monkeypatch)
    client.get('/photo-scout/v1/tasks')
    guest_hash=hashlib.sha256(client.cookies.get(GUEST_COOKIE).encode()).hexdigest()
    now=time.time();old=now-365*86400;expiry=now+86400
    with sqlite3.connect(settings.database_path) as db:
        db.execute('UPDATE photo_guests SET expires=? WHERE hash=?',(expiry,guest_hash))
        db.execute("INSERT INTO photo_scout_jobs(id,token_hash,payload,created,price,state,result) VALUES(?,?,?, ?,0,'complete',?)",('old-search','hash','{}',old,json.dumps({'spots':[]})))
        db.execute('INSERT INTO photo_portraits VALUES(?,?,?,?,?,?,?,?,?)',('old-photo','hash',old,old+7*86400,'complete',None,None,b'kept',None))
        for kind,job in [('search','old-search'),('portrait','old-photo')]:
            db.execute('INSERT INTO photo_task_owners VALUES(?,?,?,?,?,?)',(kind,job,guest_hash,None,old,'{}'))
    # Worker pruning and background polling preserve old records but do not touch the clock.
    with sqlite3.connect(settings.database_path) as db:prune_records(db)
    data=client.get('/photo-scout/v1/tasks').json()
    assert data['retention']=='seven_days_inactive'
    assert len(data['items'])==2 and all(t['expiresAt']==expiry for t in data['items'])
    assert client.get('/photo-scout/v1/portraits/old-photo/image').content==b'kept'
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute('SELECT expires FROM photo_guests WHERE hash=?',(guest_hash,)).fetchone()[0]==expiry
    # Visiting renews the cookie and the shared deadline for every existing record.
    visit=client.get('/photo-scout/v1/tasks?visit=true')
    assert 'Max-Age=604800' in visit.headers['set-cookie']
    deadlines={t['expiresAt'] for t in visit.json()['items']}
    assert len(deadlines)==1 and next(iter(deadlines))>=now+7*86400
    assert client.get('/photo-scout/v1/report/old-search').status_code==200
    # Seven days of inactivity deletes the complete guest data set, regardless of creation time.
    with sqlite3.connect(settings.database_path) as db:
        db.execute('UPDATE photo_guests SET expires=? WHERE hash=?',(now-1,guest_hash))
    assert client.get('/photo-scout/v1/tasks?visit=true').json()['items']==[]
    with sqlite3.connect(settings.database_path) as db:
        assert not db.execute('SELECT 1 FROM photo_guests WHERE hash=?',(guest_hash,)).fetchone()
        assert not db.execute('SELECT 1 FROM photo_task_owners WHERE guest=?',(guest_hash,)).fetchone()
        assert not db.execute("SELECT 1 FROM photo_scout_jobs WHERE id='old-search'").fetchone()
        assert not db.execute("SELECT 1 FROM photo_portraits WHERE id='old-photo'").fetchone()


def test_hidden_poi_is_private_to_one_history_keeps_report_and_moves_to_account(tmp_path,monkeypatch):
    from starlette.requests import Request
    settings,app,client=setup(tmp_path,monkeypatch);client.get('/photo-scout/v1/tasks')
    job=client.post('/photo-scout/v1/jobs',headers={'X-Request-Token':'h'*32},json={'lat':40,'lon':-96}).json()['jobId']
    report={'spots':[{'id':'poi-1','score':80}], 'poiResults':[{'id':'poi-1','score':80}]}
    with sqlite3.connect(settings.database_path) as db:db.execute("UPDATE photo_scout_jobs SET state='complete',result=? WHERE id=?",(json.dumps(report),job))
    body={'searchId':job,'poiId':'poi-1'};path='/photo-scout/v1/hidden-pois';headers={'Origin':'https://aisoup.net'}
    assert client.post(path,json=body).status_code==403
    stranger=TestClient(app,base_url='https://api.test',headers={'Authorization':'Bearer private'});stranger.get('/photo-scout/v1/tasks')
    assert stranger.post(path,json=body,headers=headers).status_code==404
    assert client.post(path,json=body,headers=headers).json()['hiddenPois']=={job:['poi-1']}
    assert client.get('/photo-scout/v1/tasks').json()['hiddenPois']=={job:['poi-1']}
    assert client.get('/photo-scout/v1/report/'+job).json()['result']['spots']==report['spots']
    with sqlite3.connect(settings.database_path) as db:assert json.loads(db.execute('SELECT result FROM photo_scout_jobs WHERE id=?',(job,)).fetchone()[0])==report
    token=client.cookies.get(GUEST_COOKIE)
    TaskStore(settings.database_path).attach_user(Request({'type':'http','headers':[(b'cookie',(GUEST_COOKIE+'='+token).encode())]}),'alice')
    with sqlite3.connect(settings.database_path) as db:db.execute('INSERT INTO photo_sessions VALUES(?,?,?,?,?)',(hashlib.sha256(b'alice').hexdigest(),'alice','{}','csrf',time.time()+3600))
    account=TestClient(create_app(settings=settings),base_url='https://api.test',headers={'Authorization':'Bearer private'});account.cookies.set(COOKIE,'alice')
    assert account.get('/photo-scout/v1/tasks').json()['hiddenPois']=={job:['poi-1']}
    assert client.get('/photo-scout/v1/tasks').json()['hiddenPois']=={}
    assert account.post(path,json=body,headers=headers).status_code==403
    assert account.post(path,json=body|{'hidden':False},headers=headers|{'X-CSRF-Token':'csrf'}).json()['hiddenPois']=={}
    assert account.get('/photo-scout/v1/report/'+job).json()['result']['spots']==report['spots']
