import hashlib,json,sqlite3,time
from pathlib import Path
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from agentic_services.config import Settings
from agentic_services.photo_scout.accounts import create_accounts_router,COOKIE,STATE_COOKIE


def fixture(tmp_path,monkeypatch):
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_CLIENT_ID','client')
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_CLIENT_SECRET','secret-fixture')
    settings=Settings(openai_api_key='test',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test')
    app=FastAPI()
    def authorize(v):
        if v!='Bearer internal':raise HTTPException(401)
    app.include_router(create_accounts_router(settings,authorize))
    return TestClient(app,base_url='https://api.test',headers={'Authorization':'Bearer internal'}),settings.database_path


def seed(client,path,owner,token='session'):
    with sqlite3.connect(path) as db:db.execute('INSERT OR REPLACE INTO photo_sessions VALUES(?,?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),owner,json.dumps({'id':owner,'name':owner}),owner+'-csrf',time.time()+3600))
    client.cookies.set(COOKIE,token)
    return {'Origin':'https://aisoup.net','X-CSRF-Token':owner+'-csrf'}


def item():return {'id':'history-1','created':100,'label':'Chicago','radius':1000,'checked':True,'result':{'spots':[{'poi':{'id':'park'},'imageUrl':'private-url','streetViewReference':'private-reference','score':80}],'poiResults':[],'imageAssessments':[{'pixels':'not-retained'}],'reportToken':'private'}}


def test_account_history_owner_isolation_csrf_and_logout(tmp_path,monkeypatch):
    client,path=fixture(tmp_path,monkeypatch)
    assert client.get('/photo-scout/v1/auth/me').json()['user'] is None
    assert client.get('/photo-scout/v1/history').status_code==401
    headers=seed(client,path,'alice')
    assert client.post('/photo-scout/v1/history',json=item()).status_code==403
    assert client.post('/photo-scout/v1/history',headers={**headers,'Origin':'https://evil.test'},json=item()).status_code==403
    assert client.post('/photo-scout/v1/history',headers=headers,json=item()).status_code==200
    result=client.get('/photo-scout/v1/history').json()['items'][0]
    assert result['result']['spots'][0]['score']==80
    assert 'imageUrl' not in result['result']['spots'][0] and 'reportToken' not in result['result'] and 'imageAssessments' not in result['result']
    seed(client,path,'bob','bob-session')
    assert client.get('/photo-scout/v1/history').json()['items']==[]
    client.cookies.set(COOKIE,'session')
    assert client.post('/photo-scout/v1/auth/logout',headers=headers).status_code==200
    assert client.get('/photo-scout/v1/history').status_code==401


def test_oauth_state_pkce_and_replay_rejection(tmp_path,monkeypatch):
    from urllib.parse import parse_qs,urlsplit
    client,path=fixture(tmp_path,monkeypatch)
    response=client.get('/photo-scout/v1/auth/login',follow_redirects=False)
    assert response.status_code==302
    query=parse_qs(urlsplit(response.headers['location']).query)
    assert query['scope']==['openid email profile'] and query['code_challenge_method']==['S256']
    assert query['redirect_uri']==['https://api.aisoup.net/photo-scout/v1/auth/callback']
    assert 'HttpOnly' in response.headers['set-cookie'] and 'Secure' in response.headers['set-cookie']
    state=query['state'][0]
    assert client.get('/photo-scout/v1/auth/callback?state=wrong&error=denied').status_code==400
    assert client.get('/photo-scout/v1/auth/callback',params={'state':state,'error':'denied'},follow_redirects=False).status_code==302
    client.cookies.set(STATE_COOKIE,state)
    assert client.get('/photo-scout/v1/auth/callback',params={'state':state,'error':'denied'},follow_redirects=False).status_code==400


def test_history_retains_all_account_records_and_validates_views(tmp_path,monkeypatch):
    client,path=fixture(tmp_path,monkeypatch);headers=seed(client,path,'alice')
    for i in range(32):
        record={**item(),'id':f'record-{i}','created':i}
        assert client.post('/photo-scout/v1/history',headers=headers,json=record).status_code==200
    records=client.get('/photo-scout/v1/history').json()['items']
    assert len(records)==32 and records[0]['id']=='record-31' and records[-1]['id']=='record-0'
    assert client.post('/photo-scout/v1/history',headers=headers,json={**item(),'result':{'spots':'invalid'}}).status_code==422


def test_google_callback_verifies_signature_audience_nonce_and_creates_session(tmp_path,monkeypatch):
    import base64
    from urllib.parse import parse_qs,urlsplit
    from cryptography.hazmat.primitives.asymmetric import rsa
    import httpx,jwt
    import agentic_services.photo_scout.accounts as accounts
    client,path=fixture(tmp_path,monkeypatch)
    private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    numbers=private.public_key().public_numbers()
    encode=lambda v:base64.urlsafe_b64encode(v.to_bytes((v.bit_length()+7)//8,'big')).decode().rstrip('=')
    jwk={'kty':'RSA','kid':'fixture','use':'sig','alg':'RS256','n':encode(numbers.n),'e':encode(numbers.e)}
    active={'nonce':'','aud':'client'}
    def handler(request):
        if request.url.path.endswith('/token'):
            claims={'iss':'https://accounts.google.com','sub':'verified-user','aud':active['aud'],'iat':int(time.time()),'exp':int(time.time())+600,'nonce':active['nonce'],'email_verified':True,'email':'user@example.test','name':'Test user'}
            return httpx.Response(200,json={'id_token':jwt.encode(claims,private,algorithm='RS256',headers={'kid':'fixture'})})
        return httpx.Response(200,json={'keys':[jwk]})
    real=httpx.AsyncClient
    monkeypatch.setattr(accounts.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler)))
    def start():
        response=client.get('/photo-scout/v1/auth/login',follow_redirects=False)
        state=parse_qs(urlsplit(response.headers['location']).query)['state'][0]
        with sqlite3.connect(path) as db:active['nonce']=db.execute('SELECT nonce FROM photo_oauth_states WHERE hash=?',(hashlib.sha256(state.encode()).hexdigest(),)).fetchone()[0]
        return state
    state=start();active['aud']='wrong-client'
    assert client.get('/photo-scout/v1/auth/callback',params={'state':state,'code':'fixture'},follow_redirects=False).status_code==400
    state=start();active['aud']='client';active['nonce']='wrong-nonce'
    assert client.get('/photo-scout/v1/auth/callback',params={'state':state,'code':'fixture'},follow_redirects=False).status_code==400
    state=start()
    assert client.get('/photo-scout/v1/auth/callback',params={'state':state,'code':'fixture'},follow_redirects=False).status_code==302
    me=client.get('/photo-scout/v1/auth/me').json()
    assert me['user']['id']=='google:verified-user' and me['csrfToken']

def test_removed_history_does_not_return_after_stale_browser_sync(tmp_path,monkeypatch):
    client,path=fixture(tmp_path,monkeypatch);headers=seed(client,path,'alice')
    client.post('/photo-scout/v1/history',headers=headers,json=item())
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO photo_removed_items VALUES('user:alice','search','history-1')")
    client.post('/photo-scout/v1/history',headers=headers,json=item())
    assert client.get('/photo-scout/v1/history').json()['items']==[]
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM photo_account_history').fetchone()[0]==1
        db.execute("DELETE FROM photo_removed_items WHERE owner='user:alice'")
    assert len(client.get('/photo-scout/v1/history').json()['items'])==1


def test_history_bootstrap_includes_owned_completed_tasks_without_browser_sync(tmp_path,monkeypatch):
    from agentic_services.photo_scout.tasks import TaskStore
    client,path=fixture(tmp_path,monkeypatch);headers=seed(client,path,'alice');TaskStore(path)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE photo_scout_jobs(id TEXT PRIMARY KEY,state TEXT,payload TEXT,result TEXT)')
        report={'spots':[{'poi':{'id':'park','lat':1,'lon':2},'score':80,'imageUrl':'temporary','streetViewReference':'private-reference'}],'poiResults':[],'imageAssessments':[{'large':'audit'}]}
        for job,user in [('owned','alice'),('other','bob'),('removed','alice')]:
            db.execute('INSERT INTO photo_scout_jobs VALUES(?,?,?,?)',(job,'complete',json.dumps({'query':'Lake views'}),json.dumps(report)))
            db.execute('INSERT INTO photo_task_owners VALUES(?,?,?,?,?,?)',('search',job,None,user,123,json.dumps({'lat':1,'lon':2,'radius':5000})))
        db.execute('INSERT INTO photo_removed_items VALUES(?,?,?)',('user:alice','search','removed'))
        db.execute('INSERT INTO photo_hidden_pois VALUES(?,?,?)',('user:alice','owned','park'))
    record={**item(),'id':'owned','checked':False}
    assert client.post('/photo-scout/v1/history',headers=headers,json=record).status_code==200
    response=client.get('/photo-scout/v1/history').json();assert len(response['items'])==1
    record=response['items'][0];assert record['id']=='owned' and record['checked'] is False
    assert response['hiddenPois']=={'owned':['park']}
    view=record['result']['spots'][0];assert view['verifiedImageAvailable'] is True
    assert 'imageUrl' not in view and 'streetViewReference' not in view
    assert 'imageAssessments' not in record['result']
    # No legacy history row is required to display task-owned map places.
    with sqlite3.connect(path) as db:db.execute('DELETE FROM photo_account_history')
    assert client.get('/photo-scout/v1/history').json()['items'][0]['id']=='owned'


def test_history_accepts_current_fifty_place_searches(tmp_path,monkeypatch):
    client,path=fixture(tmp_path,monkeypatch);headers=seed(client,path,'alice')
    result={'spots':[],'poiResults':[{'score':80,'imageUrl':'temporary','poi':{'id':str(i)}} for i in range(50)]}
    assert client.post('/photo-scout/v1/history',headers=headers,json={**item(),'result':result}).status_code==200
    saved=client.get('/photo-scout/v1/history').json()['items'][0]['result']['poiResults']
    assert len(saved)==50 and all(view['verifiedImageAvailable'] for view in saved)
    result['poiResults'].append({'score':80})
    assert client.post('/photo-scout/v1/history',headers=headers,json={**item(),'result':result}).status_code==422
