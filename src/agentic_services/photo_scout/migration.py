"""One-time, browser-bound migration from the legacy API to the App subdomain."""
import hmac
import secrets
import time
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from .accounts import COOKIE
from .tasks import TaskStore, GUEST_COOKIE, SEARCH_RETENTION, digest
from .web_origin import CANONICAL_ORIGIN

NONCE_COOKIE='photo_scout_migration'

def merge_guests(db, current, legacy):
    if not current or current == legacy:return
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    db.execute('UPDATE photo_task_owners SET guest=? WHERE guest=? AND user_id IS NULL',(legacy,current))
    for table in ('photo_avatars','photo_publications'):
        if table in tables:db.execute(f'UPDATE OR IGNORE {table} SET owner=? WHERE owner=?',('guest:'+legacy,'guest:'+current))
    for table,columns in [('photo_hidden_pois','search,poi'),('photo_removed_items','kind,job')]:
        db.execute(f'INSERT OR IGNORE INTO {table} SELECT ?,{columns} FROM {table} WHERE owner=?',('guest:'+legacy,'guest:'+current))
        db.execute(f'DELETE FROM {table} WHERE owner=?',('guest:'+current,))
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='photo_avatar_names'").fetchone():
        db.execute('INSERT OR IGNORE INTO photo_avatar_names SELECT ?,avatar,name FROM photo_avatar_names WHERE owner=?',('guest:'+legacy,'guest:'+current))
        db.execute('DELETE FROM photo_avatar_names WHERE owner=?',('guest:'+current,))
    db.execute('UPDATE photo_guest_aliases SET canonical=? WHERE canonical=?',(legacy,current))
    db.execute('INSERT OR REPLACE INTO photo_guest_aliases VALUES(?,?)',(current,legacy))

class TransferRequest(BaseModel):
    state: str = Field(min_length=32,max_length=128)
    ticket: str = Field(default='',max_length=128)


def create_migration_router(settings, require_api):
    router=APIRouter(tags=['Photo Scout']);store=TaskStore(settings.database_path)
    with store.db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS photo_session_transfers (nonce TEXT PRIMARY KEY,ticket TEXT,guest TEXT,session_hash TEXT,expires REAL NOT NULL)')
    def check(request):
        require_api(request.headers.get('authorization'))
        if request.headers.get('origin') != CANONICAL_ORIGIN:
            raise HTTPException(403,'Invalid migration origin')
    def set_cookie(response,name,value,age,path='/photo-scout/'):
        response.set_cookie(name,value,max_age=age,httponly=True,secure=True,samesite='lax',path=path)
    @router.post('/photo-scout/v1/session-migration/start')
    def start(request:Request,response:Response):
        check(request);nonce=secrets.token_urlsafe(32)
        with store.db() as db:
            db.execute('DELETE FROM photo_session_transfers WHERE expires<=?',(time.time(),))
            db.execute('INSERT INTO photo_session_transfers(nonce,expires) VALUES(?,?)',(digest(nonce),time.time()+300))
        set_cookie(response,NONCE_COOKIE,nonce,300,'/photo-scout/v1/session-migration/')
        response.headers['Cache-Control']='private, no-store'
        return {'state':nonce}
    @router.post('/photo-scout/v1/session-migration/export')
    def export(payload:TransferRequest,request:Request,response:Response):
        check(request);guest,_=store.identity(request);session_hash=digest(request.cookies.get(COOKIE,''))
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            pending=db.execute('SELECT nonce FROM photo_session_transfers WHERE nonce=? AND expires>? AND ticket IS NULL',(digest(payload.state),time.time())).fetchone()
            if not pending:raise HTTPException(400,'Migration expired')
            session=db.execute('SELECT hash FROM photo_sessions WHERE hash=? AND expires>?',(session_hash,time.time())).fetchone()
            ticket=secrets.token_urlsafe(32)
            db.execute('UPDATE photo_session_transfers SET ticket=?,guest=?,session_hash=? WHERE nonce=?',(digest(ticket),guest,session_hash if session else None,digest(payload.state)))
        response.headers['Cache-Control']='private, no-store'
        return {'ticket':ticket}
    @router.post('/photo-scout/v1/session-migration/import')
    def finish(payload:TransferRequest,request:Request,response:Response):
        check(request)
        if not hmac.compare_digest(payload.state,request.cookies.get(NONCE_COOKIE,'')):
            raise HTTPException(403,'Migration browser mismatch')
        current_guest,current_user=store.identity(request);migrated_guest=False;migrated_user=False
        guest_token=None;imported_user=None
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            pending=db.execute('SELECT * FROM photo_session_transfers WHERE nonce=? AND ticket=? AND expires>?',(digest(payload.state),digest(payload.ticket),time.time())).fetchone()
            if not pending:raise HTTPException(400,'Migration expired or already used')
            db.execute('DELETE FROM photo_session_transfers WHERE nonce=?',(pending['nonce'],))
            guest=db.execute('SELECT hash FROM photo_guests WHERE hash=? AND expires>?',(pending['guest'],time.time())).fetchone()
            if guest and not current_user:
                merge_guests(db,current_guest,guest['hash'])
                if current_guest != guest['hash']:
                    token=secrets.token_urlsafe(32)
                    guest_token=token
                    db.execute('INSERT INTO photo_guest_aliases VALUES(?,?)',(digest(token),guest['hash']))
                    set_cookie(response,GUEST_COOKIE,token,SEARCH_RETENTION);migrated_guest=True
                db.execute('UPDATE photo_guests SET expires=? WHERE hash=?',(time.time()+SEARCH_RETENTION,guest['hash']))
            if not current_user and pending['session_hash']:
                session=db.execute('SELECT user_id,user_json FROM photo_sessions WHERE hash=? AND expires>?',(pending['session_hash'],time.time())).fetchone()
                if session:
                    token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
                    db.execute('INSERT INTO photo_sessions VALUES(?,?,?,?,?)',(digest(token),session['user_id'],session['user_json'],csrf,time.time()+30*86400))
                    source_family=db.execute('SELECT family FROM photo_session_families WHERE hash=?',(pending['session_hash'],)).fetchone()
                    family=source_family['family'] if source_family else pending['session_hash']
                    db.execute('INSERT OR IGNORE INTO photo_session_families VALUES(?,?)',(pending['session_hash'],family))
                    db.execute('INSERT INTO photo_session_families VALUES(?,?)',(digest(token),family))
                    set_cookie(response,COOKIE,token,30*86400);migrated_user=True
                    imported_user=session['user_id']
        if imported_user:
            scope=dict(request.scope)
            if guest_token:
                cookies={**request.cookies,GUEST_COOKIE:guest_token}
                scope['headers']=[(k,v) for k,v in scope['headers'] if k.lower()!=b'cookie']+[(b'cookie','; '.join(k+'='+v for k,v in cookies.items()).encode())]
            store.attach_user(Request(scope),imported_user)
        set_cookie(response,NONCE_COOKIE,'',0,'/photo-scout/v1/session-migration/')
        response.headers['Cache-Control']='private, no-store'
        return {'migratedGuest':migrated_guest,'migratedUser':migrated_user}
    return router
