import base64,io,time
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from types import SimpleNamespace
from agentic_services.photo_scout.avatars import create_avatars_router
from agentic_services.photo_scout.tasks import TaskStore,digest,GUEST_COOKIE,ACCOUNT_COOKIE,prune_records

def fixture(tmp_path):
    settings=SimpleNamespace(database_path=tmp_path/'db');app=FastAPI();app.include_router(create_avatars_router(settings,lambda token:None));client=TestClient(app,base_url='https://api.test');buf=io.BytesIO();Image.new('RGB',(2200,1600),'red').save(buf,'PNG');payload={'name':'My character','image':'data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode()};return settings,app,client,payload

def test_private_upload_normalization_and_remove(tmp_path):
    settings,app,client,payload=fixture(tmp_path);assert client.post('/photo-scout/v1/avatars',json=payload).status_code==403
    item=client.post('/photo-scout/v1/avatars',json=payload,headers={'Origin':'https://aisoup.net'}).json();listing=client.get('/photo-scout/v1/avatars').json();assert len(listing['items'])==1
    image=client.get(item['imageUrl']);assert image.status_code==200 and image.headers['Cache-Control']=='private, no-store'
    with Image.open(io.BytesIO(image.content)) as img:assert max(img.size)==1600 and not img.getexif()
    other=TestClient(app,base_url='https://api.test');assert other.get(item['imageUrl']).status_code==404;assert not other.get('/photo-scout/v1/avatars').json()['items']
    other.delete('/photo-scout/v1/avatars/'+item['id'],headers={'Origin':'https://aisoup.net'});assert client.get(item['imageUrl']).status_code==200
    client.delete('/photo-scout/v1/avatars/'+item['id'],headers={'Origin':'https://aisoup.net'});assert not client.get('/photo-scout/v1/avatars').json()['items'];assert client.get(item['imageUrl']).status_code==404

def test_invalid_image_and_account_adoption_csrf_retention(tmp_path):
    settings,app,client,payload=fixture(tmp_path)
    assert client.post('/photo-scout/v1/avatars',json={**payload,'image':'data:image/png;base64,bad'},headers={'Origin':'https://aisoup.net'}).status_code==422
    item=client.post('/photo-scout/v1/avatars',json=payload,headers={'Origin':'https://aisoup.net'}).json();store=TaskStore(settings.database_path)
    from starlette.requests import Request
    with store.db() as db:db.execute('CREATE TABLE photo_portraits(id TEXT,expires REAL)')
    cookie=client.cookies.get(GUEST_COOKIE);request=Request({'type':'http','headers':[(b'cookie',(GUEST_COOKIE+'='+cookie).encode())]});store.attach_user(request,'user-a')
    with store.db() as db:
        db.execute('CREATE TABLE photo_sessions(hash TEXT,user_id TEXT,expires REAL,csrf TEXT)');db.execute('INSERT INTO photo_sessions VALUES(?,?,?,?)',(digest('session-a'),'user-a',time.time()+10000,'csrf-a'));db.execute('UPDATE photo_guests SET expires=0');prune_records(db)
        assert db.execute('SELECT owner FROM photo_avatars').fetchone()['owner']=='user:user-a'
    client.cookies.set(ACCOUNT_COOKIE,'session-a');assert client.get(item['imageUrl']).status_code==200
    assert client.post('/photo-scout/v1/avatars',json=payload,headers={'Origin':'https://aisoup.net'}).status_code==403
    assert client.post('/photo-scout/v1/avatars',json=payload,headers={'Origin':'https://aisoup.net','X-CSRF-Token':'csrf-a'}).status_code==200

def test_inactive_guest_is_pruned(tmp_path):
    settings,app,client,payload=fixture(tmp_path);client.post('/photo-scout/v1/avatars',json=payload,headers={'Origin':'https://aisoup.net'})
    with TaskStore(settings.database_path).db() as db:db.execute('UPDATE photo_guests SET expires=0');prune_records(db);assert db.execute('SELECT count(*) FROM photo_avatars').fetchone()[0]==0

def test_duplicate_upload_reuses_private_item_even_when_library_full(tmp_path):
    settings,app,client,payload=fixture(tmp_path);headers={'Origin':'https://aisoup.net'}
    first=client.post('/photo-scout/v1/avatars',json=payload,headers=headers).json()
    assert client.post('/photo-scout/v1/avatars',json={**payload,'name':'Another name'},headers=headers).json()['id']==first['id']
    with TaskStore(settings.database_path).db() as db:
        owner=db.execute('SELECT owner FROM photo_avatars WHERE id=?',(first['id'],)).fetchone()['owner']
        db.executemany('INSERT INTO photo_avatars(id,owner,name,created,image) VALUES(?,?,?,?,?)',[(str(i),owner,'Other',time.time(),str(i).encode()) for i in range(49)])
    assert client.post('/photo-scout/v1/avatars',json=payload,headers=headers).json()['id']==first['id']
    other=TestClient(app,base_url='https://api.test')
    assert other.post('/photo-scout/v1/avatars',json=payload,headers=headers).json()['id']!=first['id']
    client.delete('/photo-scout/v1/avatars/'+first['id'],headers=headers)
    assert client.post('/photo-scout/v1/avatars',json=payload,headers=headers).json()['id']!=first['id']

def test_heic_studio_upload_is_normalized_for_library(tmp_path):
    settings,app,client,payload=fixture(tmp_path);buf=io.BytesIO()
    Image.new('RGB',(64,48),'blue').save(buf,'HEIF')
    payload['image']='data:image/heic;base64,'+base64.b64encode(buf.getvalue()).decode()
    uploaded=client.post('/photo-scout/v1/avatars',json=payload,headers={'Origin':'https://aisoup.net'})
    assert uploaded.status_code==200
    with Image.open(io.BytesIO(client.get(uploaded.json()['imageUrl']).content)) as image:assert image.format=='JPEG'
