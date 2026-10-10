import hashlib
import json
import sqlite3
import time
import pytest
from fastapi.testclient import TestClient
from agentic_services.config import Settings
from agentic_services.main import create_app
from agentic_services.photo_scout.tasks import GUEST_COOKIE, TaskStore, prune_records

@pytest.fixture
def publication_env(tmp_path,monkeypatch):
    monkeypatch.setenv('PHOTO_SCOUT_ENABLED','1')
    settings=Settings(openai_api_key='fixture',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test',service_api_key='private')
    app=create_app(settings=settings)
    client=TestClient(app,base_url='https://api.test',headers={'Authorization':'Bearer private','Origin':'https://aisoup.net'})
    client.get('/photo-scout/v1/tasks')
    guest=hashlib.sha256(client.cookies.get(GUEST_COOKIE).encode()).hexdigest()
    spots=[{'id':'view1','name':'Lake view','score':75,'provider':'google-street-view','poi':{'id':'poi1','lat':40,'lon':-96},'sourceUrl':'https://www.google.com/maps/@?pano=example','imageUrl':'https://signed-private.example/token','streetViewReference':'private-fetch-reference','viewHeadingDegrees':90,'visible_evidence':'Lake'},
           {'id':'view2','name':'Garden','score':60,'provider':'panoramax','poi':{'id':'poi2','lat':40.1,'lon':-96},'imageUrl':'https://panoramax.ign.fr/image/example'}]
    with sqlite3.connect(settings.database_path) as db:
        db.execute("INSERT INTO photo_scout_jobs(id,token_hash,payload,created,price,state,result,kind) VALUES(?,?,?,?,0,'complete',?,'preview')",('search', 'secret',json.dumps({'query':'Lakeside views'}),time.time(),json.dumps({'spots':spots,'summary':'A shortlist','internalToken':'private'})))
        db.execute('INSERT INTO photo_task_owners VALUES(?,?,?,?,?,?)',('search','search',guest,None,time.time(),json.dumps({'lat':40,'lon':-96,'radius':5000,'query':'Lakeside views','private':'secret'})))
        db.execute("INSERT INTO photo_portraits(id,token_hash,created,expires,state,photo,output) VALUES(?,?,?,?,'complete',?,?)",('photo','secret',time.time(),time.time()+86400,b'uploaded-original',b'generated-image'))
        db.execute('INSERT INTO photo_task_owners VALUES(?,?,?,?,?,?)',('portrait','photo',guest,None,time.time(),json.dumps({'name':'Lake selfie','provider':'google-street-view','poi':{'lat':40,'lon':-96},'generation':{'style':'travel','directions':'private text'}})))
    return settings,app,client

def test_search_publication_snapshot_is_public_and_does_not_leak_private_fields(publication_env):
    settings,app,client=publication_env
    published=client.post('/photo-scout/v1/publications',json={'kind':'search','id':'search'})
    assert published.status_code==200
    ident=published.json()['id'];public=TestClient(app)
    data=public.get('/photo-scout/v1/publications/'+ident).json()
    assert len(data['result']['spots'])==2 and not data['mine']
    encoded=json.dumps(data)
    for secret in ('internalToken','streetViewReference','signed-private','private-fetch-reference','token_hash','uploaded-original'):
        assert secret not in encoded
    assert 'private' not in data['searchContext']
    assert public.get('/photo-scout/v1/publications').json()['items'][0]['id']==ident
    # Private source endpoints still reject this visitor.
    assert public.get('/photo-scout/v1/report/search').status_code in (401,403,404)
    assert client.post('/photo-scout/v1/publications',json={'kind':'search','id':'search'}).json()['id']==ident

def test_only_owner_can_publish_or_withdraw_and_origin_is_required(publication_env):
    settings,app,client=publication_env
    stranger=TestClient(app,base_url='https://api.test',headers={'Authorization':'Bearer private','Origin':'https://aisoup.net'})
    stranger.get('/photo-scout/v1/tasks')
    assert stranger.post('/photo-scout/v1/publications',json={'kind':'search','id':'search'}).status_code==404
    assert client.post('/photo-scout/v1/publications',headers={'Origin':'https://evil.example'},json={'kind':'photo','id':'photo'}).status_code==403
    item=client.post('/photo-scout/v1/publications',json={'kind':'search','id':'search'}).json()
    assert stranger.post('/photo-scout/v1/publications/withdraw',json={'id':item['id']}).status_code==404
    assert client.post('/photo-scout/v1/publications/withdraw',json={'id':item['id']}).status_code==200
    assert TestClient(app).get('/photo-scout/v1/publications/'+item['id']).status_code==404
    assert TestClient(app).get('/photo-scout/v1/publications').json()['items']==[]

def test_place_publication_filters_hidden_places_and_excludes_siblings(publication_env):
    settings,app,client=publication_env
    item=client.post('/photo-scout/v1/publications',json={'kind':'place','id':'search','poiId':'poi1'}).json()
    data=TestClient(app).get('/photo-scout/v1/publications/'+item['id']).json()
    assert len(data['result']['spots'])==1 and data['title']=='Lake view'
    assert 'searchContext' not in data
    assert client.post('/photo-scout/v1/publications',json={'kind':'place','id':'search','poiId':'unknown'}).status_code==404
    client.post('/photo-scout/v1/hidden-pois',json={'searchId':'search','poiId':'poi2'})
    data=client.post('/photo-scout/v1/publications',json={'kind':'search','id':'search'}).json()
    assert len(TestClient(app).get('/photo-scout/v1/publications/'+data['id']).json()['result']['spots'])==1

def test_generated_photo_snapshot_survives_private_expiry_and_revocation_blocks_image(publication_env):
    settings,app,client=publication_env
    item=client.post('/photo-scout/v1/publications',json={'kind':'photo','id':'photo'}).json();ident=item['id']
    public=TestClient(app);path='/photo-scout/v1/publications/'+ident
    assert public.get(path+'/image').content==b'generated-image'
    assert public.get(path).json()['context']['generation']=={'style':'travel'}
    # Remove private source through normal guest inactivity pruning.
    with sqlite3.connect(settings.database_path) as db:
        db.execute('UPDATE photo_guests SET expires=0');prune_records(db)
        assert db.execute('SELECT count(*) FROM photo_portraits').fetchone()[0]==0
    assert public.get(path+'/image').content==b'generated-image'
    with sqlite3.connect(settings.database_path) as db:db.execute('UPDATE photo_publications SET active=0 WHERE id=?',(ident,))
    assert public.get(path+'/image').status_code==404

def test_unfinished_items_cannot_be_published(publication_env):
    settings,app,client=publication_env
    with sqlite3.connect(settings.database_path) as db:db.execute("UPDATE photo_portraits SET state='running'");db.execute("UPDATE photo_scout_jobs SET state='running'")
    for kind,ident in [('search','search'),('photo','photo')]:
        assert client.post('/photo-scout/v1/publications',json={'kind':kind,'id':ident}).status_code==409

def test_google_account_requires_csrf_and_claims_guest_publications(publication_env):
    from starlette.requests import Request
    from agentic_services.photo_scout.tasks import ACCOUNT_COOKIE
    settings,app,client=publication_env
    item=client.post('/photo-scout/v1/publications',json={'kind':'search','id':'search'}).json()
    request=Request({'type':'http','headers':[(b'cookie',(GUEST_COOKIE+'='+client.cookies.get(GUEST_COOKIE)).encode())]})
    TaskStore(settings.database_path).attach_user(request,'alice')
    with sqlite3.connect(settings.database_path) as db:
        db.execute('INSERT INTO photo_sessions VALUES(?,?,?,?,?)',(hashlib.sha256(b'account').hexdigest(),'alice','{}','csrf-value',time.time()+3600))
    client.cookies.set(ACCOUNT_COOKIE,'account')
    assert client.post('/photo-scout/v1/publications',json={'kind':'search','id':'search'}).status_code==403
    assert client.post('/photo-scout/v1/publications',headers={'X-CSRF-Token':'csrf-value'},json={'kind':'search','id':'search'}).json()['id']==item['id']
    assert client.get('/photo-scout/v1/publications').json()['items'][0]['mine']
    assert client.post('/photo-scout/v1/publications/withdraw',headers={'X-CSRF-Token':'csrf-value'},json={'id':item['id']}).status_code==200

def test_public_photo_thumbnail_is_readable_and_withdrawn_with_photo(publication_env):
    import io
    from PIL import Image
    settings,app,client=publication_env
    output=io.BytesIO();Image.new('RGB',(320,240),'purple').save(output,format='PNG')
    with sqlite3.connect(settings.database_path) as db:
        db.execute('UPDATE photo_portraits SET output=? WHERE id=?',(output.getvalue(),'photo'))
    ident=client.post('/photo-scout/v1/publications',json={'kind':'photo','id':'photo'}).json()['id']
    public=TestClient(app);response=public.get('/photo-scout/v1/publications/'+ident+'/thumbnail')
    assert response.status_code==200 and response.headers['content-type']=='image/jpeg'
    image=Image.open(io.BytesIO(response.content));assert image.size==(160,120)
    client.post('/photo-scout/v1/publications/withdraw',json={'id':ident})
    assert public.get('/photo-scout/v1/publications/'+ident+'/thumbnail').status_code==404

def test_removing_search_withdraws_search_and_places_but_not_independent_photo(publication_env):
    settings,app,client=publication_env
    search=client.post('/photo-scout/v1/publications',json={'kind':'search','id':'search'}).json()
    place=client.post('/photo-scout/v1/publications',json={'kind':'place','id':'search','poiId':'poi1'}).json()
    photo=client.post('/photo-scout/v1/publications',json={'kind':'photo','id':'photo'}).json()
    mine=client.get('/photo-scout/v1/publications/mine').json()['items']
    assert {item['sourceId'] for item in mine}=={'search','photo'}
    assert client.post('/photo-scout/v1/removed-items',json={'kind':'search','id':'search','removed':True}).status_code==200
    public=TestClient(app)
    for item in (search,place):assert public.get('/photo-scout/v1/publications/'+item['id']).status_code==404
    assert public.get('/photo-scout/v1/publications/'+photo['id']).status_code==200
    assert client.post('/photo-scout/v1/publications',json={'kind':'search','id':'search'}).status_code==404
    client.post('/photo-scout/v1/removed-items',json={'kind':'search','id':'search','removed':False})
    assert public.get('/photo-scout/v1/publications/'+search['id']).status_code==404
    client.post('/photo-scout/v1/removed-items',json={'kind':'portrait','id':'photo','removed':True})
    assert public.get('/photo-scout/v1/publications/'+photo['id']).status_code==404
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute('SELECT count(*) FROM photo_scout_jobs').fetchone()[0]==1

def test_removing_place_updates_public_search_and_withdraws_place(publication_env):
    settings,app,client=publication_env
    search=client.post('/photo-scout/v1/publications',json={'kind':'search','id':'search'}).json()
    place=client.post('/photo-scout/v1/publications',json={'kind':'place','id':'search','poiId':'poi1'}).json()
    response=client.post('/photo-scout/v1/hidden-pois',json={'searchId':'search','poiId':'poi1','hidden':True})
    assert response.status_code==200
    public=TestClient(app)
    assert public.get('/photo-scout/v1/publications/'+place['id']).status_code==404
    assert [s['name'] for s in public.get('/photo-scout/v1/publications/'+search['id']).json()['result']['spots']]==['Garden']


def test_owner_camera_edit_survives_restart_and_updates_publications(publication_env):
    from urllib.parse import urlsplit,parse_qs
    settings,app,client=publication_env
    publication=client.post('/photo-scout/v1/publications',json={'kind':'search','id':'search'}).json()['id']
    place=client.post('/photo-scout/v1/publications',json={'kind':'place','id':'search','poiId':'poi1'}).json()['id']
    payload={'searchId':'search','poiId':'poi1','heading':227.5,'pitch':12,'fov':60}
    assert client.post('/photo-scout/v1/poi-view',json=payload).status_code==200
    reopened=TestClient(create_app(settings=settings),base_url='https://api.test',headers={'Authorization':'Bearer private'})
    reopened.cookies.set(GUEST_COOKIE,client.cookies.get(GUEST_COOKIE))
    spot=reopened.get('/photo-scout/v1/report/search').json()['result']['spots'][0]
    assert (spot['viewHeadingDegrees'],spot['viewPitchDegrees'],spot['viewFovDegrees'])==(227.5,12,60)
    assert spot['viewAdjusted'] and spot['score']==75 and spot['imageUrl'] is None
    assert parse_qs(urlsplit(spot['sourceUrl']).query)['pano']==['example']
    for ident in (publication,place):
        public=TestClient(app).get('/photo-scout/v1/publications/'+ident).json()['result']['spots'][0]
        assert public['viewHeadingDegrees']==227.5 and public['viewFovDegrees']==60
    later=client.post('/photo-scout/v1/publications',json={'kind':'search','id':'search'}).json()['id']
    assert TestClient(app).get('/photo-scout/v1/publications/'+later).json()['result']['spots'][0]['viewAdjusted']


def test_camera_edit_requires_owner_origin_and_valid_existing_google_place(publication_env):
    settings,app,client=publication_env
    payload={'searchId':'search','poiId':'poi1','heading':45,'pitch':0,'fov':90}
    stranger=TestClient(app,base_url='https://api.test',headers={'Authorization':'Bearer private','Origin':'https://aisoup.net'})
    stranger.get('/photo-scout/v1/tasks')
    assert stranger.post('/photo-scout/v1/poi-view',json=payload).status_code==404
    assert client.post('/photo-scout/v1/poi-view',json=payload,headers={'Origin':'https://evil.example'}).status_code==403
    assert client.post('/photo-scout/v1/poi-view',json={**payload,'poiId':'poi2'}).status_code==404
    for field,value in [('fov',5),('heading',360),('pitch',91)]:
        assert client.post('/photo-scout/v1/poi-view',json={**payload,field:value}).status_code==422
    assert client.get('/photo-scout/v1/report/search').json()['result']['spots'][0]['viewHeadingDegrees']==90


def test_signed_in_camera_edit_requires_csrf_and_preserves_account_history(publication_env):
    from agentic_services.photo_scout.tasks import ACCOUNT_COOKIE,digest
    settings,app,client=publication_env
    with sqlite3.connect(settings.database_path) as db:
        result=json.loads(db.execute("SELECT result FROM photo_scout_jobs WHERE id='search'").fetchone()[0])
        db.execute("UPDATE photo_task_owners SET user_id='owner' WHERE job='search'")
        db.execute('INSERT INTO photo_sessions VALUES(?,?,?,?,?)',(digest('session'),'owner',json.dumps({'id':'owner'}),'csrf',time.time()+86400))
        db.execute('INSERT INTO photo_account_history VALUES(?,?,?,?)',('owner','search',1,json.dumps({'id':'search','result':result})))
    client.cookies.set(ACCOUNT_COOKIE,'session')
    payload={'searchId':'search','poiId':'poi1','heading':180,'pitch':-5,'fov':45}
    assert client.post('/photo-scout/v1/poi-view',json=payload).status_code==403
    assert client.post('/photo-scout/v1/poi-view',json=payload,headers={'X-CSRF-Token':'csrf'}).status_code==200
    with sqlite3.connect(settings.database_path) as db:
        record=json.loads(db.execute("SELECT record FROM photo_account_history WHERE id='search'").fetchone()[0])
        assert record['result']['spots'][0]['viewFovDegrees']==45
        # Pre-task account histories can also save their owned view.
        db.execute("DELETE FROM photo_task_owners WHERE job='search'")
    assert client.post('/photo-scout/v1/poi-view',json={**payload,'fov':60},headers={'X-CSRF-Token':'csrf'}).status_code==200
