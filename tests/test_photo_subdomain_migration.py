import json
import sqlite3
import time
from fastapi.testclient import TestClient
from starlette.requests import Request
from agentic_services.config import Settings
from agentic_services.main import create_app
from agentic_services.photo_scout.accounts import COOKIE
from agentic_services.photo_scout.tasks import TaskStore,GUEST_COOKIE,digest

ORIGIN='https://photo-scout.aisoup.net'
PREFIX='/photo-scout/v1/session-migration/'

def setup(tmp_path):
    settings=Settings(openai_api_key='fixture',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test',service_api_key='internal')
    app=create_app(settings=settings)
    def client(host):return TestClient(app,base_url=host,headers={'Authorization':'Bearer internal','Origin':ORIGIN})
    return settings,client('https://api.aisoup.net'),client(ORIGIN)

def transfer(legacy,new):
    started=new.post(PREFIX+'start');assert started.status_code==200
    state=started.json()['state']
    exported=legacy.post(PREFIX+'export',json={'state':state});assert exported.status_code==200
    return {'state':state,'ticket':exported.json()['ticket']}

def identity(settings,client):
    cookie='; '.join(k+'='+v for k,v in client.cookies.items())
    return TaskStore(settings.database_path).identity(Request({'type':'http','headers':[(b'cookie',cookie.encode())]}))

def seed_session(settings,client,user,token):
    with sqlite3.connect(settings.database_path) as db:
        db.execute('INSERT INTO photo_sessions VALUES(?,?,?,?,?)',(digest(token),user,json.dumps({'id':user,'name':user}),user+'-csrf',time.time()+3600))
    client.cookies.set(COOKIE,token)

def test_guest_transfer_keeps_owner_and_issues_no_legacy_cookie_value(tmp_path):
    settings,legacy,new=setup(tmp_path)
    legacy.get('/photo-scout/v1/tasks');guest=identity(settings,legacy)[0]
    with TaskStore(settings.database_path).db() as db:db.execute('INSERT INTO photo_task_owners VALUES(?,?,?,?,?,?)',('search','old-search',guest,None,time.time(),'{}'))
    payload=transfer(legacy,new);assert legacy.cookies.get(GUEST_COOKIE) not in json.dumps(payload)
    response=new.post(PREFIX+'import',json=payload);assert response.status_code==200
    assert response.json()['migratedGuest'] is True
    assert identity(settings,new)==(guest,None)
    assert identity(settings,legacy)==(guest,None)
    assert new.cookies.get(GUEST_COOKIE)!=legacy.cookies.get(GUEST_COOKIE)
    assert new.post(PREFIX+'import',json=payload).status_code in (400,403)
    with sqlite3.connect(settings.database_path) as db:assert db.execute('SELECT guest FROM photo_task_owners WHERE job=?',('old-search',)).fetchone()[0]==guest

def test_existing_new_guest_records_merge_without_losing_either_history(tmp_path):
    settings,legacy,new=setup(tmp_path)
    legacy.get('/photo-scout/v1/tasks');new.get('/photo-scout/v1/tasks')
    old_guest=identity(settings,legacy)[0];new_guest=identity(settings,new)[0]
    with TaskStore(settings.database_path).db() as db:
        for job,guest in [('legacy',old_guest),('new',new_guest)]:db.execute('INSERT INTO photo_task_owners VALUES(?,?,?,?,?,?)',('search',job,guest,None,time.time(),'{}'))
        db.execute('INSERT INTO photo_hidden_pois VALUES(?,?,?)',('guest:'+new_guest,'new','poi'))
    assert new.post(PREFIX+'import',json=transfer(legacy,new)).status_code==200
    assert identity(settings,new)[0]==old_guest
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute('SELECT DISTINCT guest FROM photo_task_owners').fetchall()==[(old_guest,)]
        assert db.execute('SELECT owner FROM photo_hidden_pois').fetchone()[0]=='guest:'+old_guest

def test_account_transfer_preserves_user_and_permanent_records(tmp_path):
    settings,legacy,new=setup(tmp_path);seed_session(settings,legacy,'alice','legacy-session')
    with TaskStore(settings.database_path).db() as db:db.execute('INSERT INTO photo_task_owners VALUES(?,?,?,?,?,?)',('search','permanent',None,'alice',time.time()-365*86400,'{}'))
    response=new.post(PREFIX+'import',json=transfer(legacy,new));assert response.json()['migratedUser']
    assert new.get('/photo-scout/v1/auth/me').json()['user']['id']=='alice'
    assert new.cookies.get(COOKIE)!='legacy-session'
    with sqlite3.connect(settings.database_path) as db:assert db.execute('SELECT user_id FROM photo_task_owners WHERE job=?',('permanent',)).fetchone()[0]=='alice'

def test_legacy_login_after_anonymous_transfer_is_imported_without_new_guest_aliases(tmp_path):
    settings,legacy,new=setup(tmp_path);legacy.get('/photo-scout/v1/tasks')
    assert new.post(PREFIX+'import',json=transfer(legacy,new)).status_code==200
    guest=identity(settings,new)[0]
    with TaskStore(settings.database_path).db() as db:
        before=db.execute('SELECT COUNT(*) FROM photo_guest_aliases').fetchone()[0]
        db.execute('INSERT INTO photo_scout_jobs(id,token_hash,payload,created,price,state,result,kind) VALUES(?,?,?,?,?,?,?,?)',('guest-history','hash','{"query":"archive"}',time.time(),0,'complete','{"spots":[]}','preview'))
        db.execute('INSERT INTO photo_task_owners VALUES(?,?,?,?,?,?)',('search','guest-history',guest,None,time.time(),'{}'))
    seed_session(settings,legacy,'alice','later-legacy-login')
    assert new.post(PREFIX+'import',json=transfer(legacy,new)).json()['migratedUser'] is True
    assert new.get('/photo-scout/v1/auth/me').json()['user']['id']=='alice'
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute('SELECT COUNT(*) FROM photo_guest_aliases').fetchone()[0]==before
        assert db.execute('SELECT user_id FROM photo_task_owners WHERE job=?',('guest-history',)).fetchone()[0]=='alice'

def test_current_signed_in_user_is_never_replaced(tmp_path):
    settings,legacy,new=setup(tmp_path);seed_session(settings,legacy,'alice','old');seed_session(settings,new,'bob','current')
    response=new.post(PREFIX+'import',json=transfer(legacy,new));assert response.status_code==200
    assert response.json()=={'migratedGuest':False,'migratedUser':False}
    assert new.get('/photo-scout/v1/auth/me').json()['user']['id']=='bob'

def test_signout_revokes_only_the_related_alias_sessions_and_keeps_account_history(tmp_path):
    settings,legacy,new=setup(tmp_path);seed_session(settings,legacy,'alice','legacy-session')
    other=TestClient(legacy.app,base_url='https://other.test',headers={'Authorization':'Bearer internal'})
    seed_session(settings,other,'alice','unrelated-device')
    with sqlite3.connect(settings.database_path) as db:
        db.execute('INSERT INTO photo_account_history VALUES(?,?,?,?)',('alice','kept',100,'{"id":"kept","result":{"spots":[]}}'))
    assert new.post(PREFIX+'import',json=transfer(legacy,new)).status_code==200
    csrf=new.get('/photo-scout/v1/auth/me').json()['csrfToken']
    assert new.post('/photo-scout/v1/auth/logout',headers={'X-CSRF-Token':csrf}).status_code==200
    assert new.get('/photo-scout/v1/auth/me').json()['user'] is None
    assert legacy.get('/photo-scout/v1/auth/me').json()['user'] is None
    assert other.get('/photo-scout/v1/auth/me').json()['user']['id']=='alice'
    with sqlite3.connect(settings.database_path) as db:assert db.execute('SELECT id FROM photo_account_history WHERE user_id=?',('alice',)).fetchone()[0]=='kept'

def test_origin_browser_nonce_ticket_expiry_and_export_replay_are_checked(tmp_path):
    settings,legacy,new=setup(tmp_path)
    assert new.post(PREFIX+'start',headers={'Origin':'https://evil.test'}).status_code==403
    payload=transfer(legacy,new)
    assert legacy.post(PREFIX+'export',json={'state':payload['state']}).status_code==400
    assert legacy.post(PREFIX+'import',json=payload).status_code==403
    assert new.post(PREFIX+'import',json={**payload,'ticket':'wrong'}).status_code==400
    with sqlite3.connect(settings.database_path) as db:db.execute('UPDATE photo_session_transfers SET expires=0')
    assert new.post(PREFIX+'import',json=payload).status_code==400

def test_page_leave_records_owned_stage_without_changing_search_state(tmp_path):
    settings,legacy,stranger=setup(tmp_path);legacy.get('/photo-scout/v1/tasks')
    guest=identity(settings,legacy)[0]
    with TaskStore(settings.database_path).db() as db:
        db.execute('INSERT INTO photo_task_owners VALUES(?,?,?,?,?,?)',('search','owned',guest,None,time.time(),'{"stage":"planning"}'))
        db.execute('INSERT INTO photo_task_owners VALUES(?,?,?,?,?,?)',('search','other','other-guest',None,time.time(),'{}'))
    body=json.dumps({'event':'page_left','clientMillis':123,'jobs':['owned','other']})
    assert legacy.post('/photo-scout/v1/client-events',content=body,headers={'Content-Type':'text/plain'}).status_code==204
    assert stranger.post('/photo-scout/v1/client-events',content=body,headers={'Content-Type':'text/plain'}).status_code==204
    assert legacy.post('/photo-scout/v1/client-events',content=body,headers={'Origin':'https://evil.test'}).status_code==403
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute('SELECT job,event,client_millis,stage FROM photo_client_events').fetchall()==[('owned','page_left',123,'planning')]
        assert db.execute('SELECT context FROM photo_task_owners WHERE job=?',('owned',)).fetchone()[0]=='{"stage":"planning"}'
