"""Server-owned task recovery for anonymous browsers and Google accounts."""
import hmac
import os
import hashlib
import json
import secrets
import sqlite3
import time
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from typing import Literal

GUEST_COOKIE='photo_scout_guest'
ACCOUNT_COOKIE='photo_scout_session'
SEARCH_RETENTION=7*86400
ACCOUNT_EXPIRY=253402300799.0
PAID_SEARCH_RETENTION=30*86400
ACTIVE_TASK_LIMIT=5

def digest(token):return hashlib.sha256(token.encode()).hexdigest()

def prune_records(db):
    """Account data is permanent; guest data expires as one unit after inactivity."""
    now=time.time()
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    owners='photo_task_owners' in tables
    for table,kind in [('photo_scout_jobs','search'),('photo_portraits','portrait')]:
        if table not in tables:continue
        legacy='created<?' if kind=='search' else 'expires<=?'
        cutoff=now-PAID_SEARCH_RETENTION if kind=='search' else now
        if owners:
            db.execute(f"""DELETE FROM {table} WHERE
                (EXISTS (SELECT 1 FROM photo_task_owners o WHERE o.kind=? AND o.job={table}.id)
                 AND NOT EXISTS (SELECT 1 FROM photo_task_owners o LEFT JOIN photo_guests g ON g.hash=o.guest
                     WHERE o.kind=? AND o.job={table}.id AND (o.user_id IS NOT NULL OR g.expires>?)))
                OR (NOT EXISTS (SELECT 1 FROM photo_task_owners o WHERE o.kind=? AND o.job={table}.id) AND {legacy})""",
                (kind,kind,now,kind,cutoff))
        else:db.execute(f'DELETE FROM {table} WHERE {legacy}',(cutoff,))
    if 'photo_scout_exploration' in tables:
        db.execute('DELETE FROM photo_scout_exploration WHERE NOT EXISTS (SELECT 1 FROM photo_scout_jobs j WHERE j.id=job) AND updated<?',(now-86400,))
    if owners:
        db.execute('DELETE FROM photo_task_owners WHERE user_id IS NULL AND NOT EXISTS (SELECT 1 FROM photo_guests g WHERE g.hash=guest AND g.expires>?)',(now,))
        if 'photo_hidden_pois' in tables:
            db.execute("DELETE FROM photo_hidden_pois WHERE owner LIKE 'guest:%' AND NOT EXISTS (SELECT 1 FROM photo_guests g WHERE 'guest:'||g.hash=owner AND g.expires>?)",(now,))
        if 'photo_removed_items' in tables:
            db.execute("DELETE FROM photo_removed_items WHERE owner LIKE 'guest:%' AND NOT EXISTS (SELECT 1 FROM photo_guests g WHERE 'guest:'||g.hash=owner AND g.expires>?)",(now,))
        db.execute('DELETE FROM photo_guests WHERE expires<=?',(now,))

class HiddenPoiRequest(BaseModel):
    searchId: str = Field(min_length=1,max_length=80)
    poiId: str = Field(min_length=1,max_length=500)
    hidden: bool = True

class PoiViewRequest(BaseModel):
    scope: Literal['place','selfie'] = 'place'
    framing: Literal['auto','current','90','60','45'] = 'current'
    searchId: str = Field(min_length=1,max_length=80)
    poiId: str = Field(min_length=1,max_length=500)
    heading: float = Field(ge=0,lt=360,allow_inf_nan=False)
    pitch: float = Field(ge=-90,le=90,allow_inf_nan=False)
    fov: float = Field(ge=30,le=120,allow_inf_nan=False)

class RemovedItemRequest(BaseModel):
    kind: Literal['search','portrait']
    id: str = Field(min_length=1,max_length=80)
    removed: bool = True

class TaskStore:
    def __init__(self,path):
        self.path=path
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS photo_removed_items (owner TEXT NOT NULL, kind TEXT NOT NULL, job TEXT NOT NULL, PRIMARY KEY(owner,kind,job))')
            db.execute('CREATE TABLE IF NOT EXISTS photo_hidden_pois (owner TEXT NOT NULL, search TEXT NOT NULL, poi TEXT NOT NULL, PRIMARY KEY(owner,search,poi))')
            db.execute('CREATE TABLE IF NOT EXISTS photo_guests (hash TEXT PRIMARY KEY, expires REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS photo_task_owners (kind TEXT NOT NULL, job TEXT NOT NULL, guest TEXT, user_id TEXT, created REAL NOT NULL, context TEXT NOT NULL, PRIMARY KEY(kind,job))')
            db.execute('CREATE INDEX IF NOT EXISTS photo_task_owner_user ON photo_task_owners(user_id,created)')
            db.execute('CREATE INDEX IF NOT EXISTS photo_task_owner_guest ON photo_task_owners(guest,created)')
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='photo_portraits'").fetchone():
                db.execute("UPDATE photo_portraits SET expires=? WHERE expires<? AND EXISTS (SELECT 1 FROM photo_task_owners o WHERE o.kind='portrait' AND o.job=photo_portraits.id AND o.user_id IS NOT NULL)",(ACCOUNT_EXPIRY,ACCOUNT_EXPIRY))
    def db(self):
        db=sqlite3.connect(self.path,timeout=15);db.row_factory=sqlite3.Row;return db
    def identity(self,request,response=None,*,touch=True):
        now=time.time();guest=None;user=None
        with self.db() as db:
            token=request.cookies.get(GUEST_COOKIE,'')
            if token and len(token)<=200:
                row=db.execute('SELECT hash FROM photo_guests WHERE hash=? AND expires>?',(digest(token),now)).fetchone()
                if row:
                    guest=row['hash']
                    if response is not None and touch:
                        db.execute('UPDATE photo_guests SET expires=? WHERE hash=?',(now+SEARCH_RETENTION,guest))
                        response.set_cookie(GUEST_COOKIE,token,max_age=SEARCH_RETENTION,httponly=True,secure=True,samesite='lax',path='/photo-scout/')
                        if db.execute("SELECT 1 FROM sqlite_master WHERE name='photo_portraits'").fetchone():
                            db.execute("UPDATE photo_portraits SET expires=? WHERE EXISTS (SELECT 1 FROM photo_task_owners o WHERE o.kind='portrait' AND o.job=photo_portraits.id AND o.guest=? AND o.user_id IS NULL)",(now+SEARCH_RETENTION,guest))
            account=request.cookies.get(ACCOUNT_COOKIE,'')
            if account and len(account)<=200 and db.execute("SELECT 1 FROM sqlite_master WHERE name='photo_sessions'").fetchone():
                row=db.execute('SELECT user_id FROM photo_sessions WHERE hash=? AND expires>?',(digest(account),now)).fetchone()
                if row:user=row['user_id']
            if guest is None and response is not None:
                token=secrets.token_urlsafe(32);guest=digest(token)
                db.execute('INSERT INTO photo_guests VALUES(?,?)',(guest,now+SEARCH_RETENTION))
                response.set_cookie(GUEST_COOKIE,token,max_age=SEARCH_RETENTION,httponly=True,secure=True,samesite='lax',path='/photo-scout/')
        return guest,user
    def expiry(self,identity):
        guest,user=identity
        if user:return None
        with self.db() as db:
            row=db.execute('SELECT expires FROM photo_guests WHERE hash=?',(guest,)).fetchone()
        return row['expires'] if row else time.time()
    def bind_in(self,db,kind,job,identity,context):
        guest,user=identity
        # Ownership and admission commit together. Retries never transfer ownership.
        if db.execute('SELECT 1 FROM photo_task_owners WHERE kind=? AND job=?',(kind,job)).fetchone():return
        now=time.time()
        active=db.execute("""
            SELECT count(*) FROM photo_task_owners o
            LEFT JOIN photo_scout_jobs s ON o.kind='search' AND s.id=o.job
            LEFT JOIN photo_portraits p ON o.kind='portrait' AND p.id=o.job
            WHERE (o.user_id=? OR (o.user_id IS NULL AND o.guest=?))
            AND ((s.state IN ('queued','running') AND s.created>?)
                 OR (p.state IN ('queued','checking','running') AND p.expires>?))
            """,(user,guest,now-SEARCH_RETENTION,now)).fetchone()[0]
        if active>=ACTIVE_TASK_LIMIT:
            raise HTTPException(429,'You already have 5 active tasks (searches and selfies combined). Wait for a task to finish, then try again.')
        db.execute('INSERT OR IGNORE INTO photo_task_owners VALUES(?,?,?,?,?,?)',(kind,job,guest,user,time.time(),json.dumps(context)))
    def allowed(self,kind,job,request):
        guest,user=self.identity(request)
        with self.db() as db:
            row=db.execute('SELECT guest,user_id FROM photo_task_owners WHERE kind=? AND job=?',(kind,job)).fetchone()
        return bool(row and ((user and row['user_id']==user) or (guest and row['user_id'] is None and row['guest']==guest)))
    def context(self,kind,job):
        with self.db() as db:
            row=db.execute('SELECT context FROM photo_task_owners WHERE kind=? AND job=?',(kind,job)).fetchone()
            if not row:return None
            context=json.loads(row['context'])
            if kind=='search':
                original=db.execute('SELECT payload FROM photo_scout_jobs WHERE id=?',(job,)).fetchone()
                if original:context['query']=json.loads(original['payload']).get('query','')
            return context
    def update_context(self,kind,job,context):
        with self.db() as db:db.execute('UPDATE photo_task_owners SET context=? WHERE kind=? AND job=?',(json.dumps(context),kind,job))
    def attach_user(self,request,user):
        guest,_=self.identity(request)
        if guest:
            with self.db() as db:
                prune_records(db)
                db.execute('UPDATE photo_task_owners SET user_id=? WHERE guest=? AND user_id IS NULL',(user,guest))
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='photo_publications'").fetchone():
                    db.execute('UPDATE OR IGNORE photo_publications SET owner=? WHERE owner=?',('user:'+user,'guest:'+guest))
                db.execute('INSERT OR IGNORE INTO photo_hidden_pois SELECT ?,search,poi FROM photo_hidden_pois WHERE owner=?',('user:'+user,'guest:'+guest))
                db.execute('DELETE FROM photo_hidden_pois WHERE owner=?',('guest:'+guest,))
                db.execute('INSERT OR IGNORE INTO photo_removed_items SELECT ?,kind,job FROM photo_removed_items WHERE owner=?',('user:'+user,'guest:'+guest))
                db.execute('DELETE FROM photo_removed_items WHERE owner=?',('guest:'+guest,))
                db.execute("UPDATE photo_portraits SET expires=? WHERE EXISTS (SELECT 1 FROM photo_task_owners o WHERE o.kind='portrait' AND o.job=photo_portraits.id AND o.user_id=?)",(ACCOUNT_EXPIRY,user))
    def hidden_pois(self,request):
        guest,user=self.identity(request)
        owner='user:'+user if user else 'guest:'+guest if guest else None
        if not owner:return {}
        with self.db() as db:rows=db.execute('SELECT search,poi FROM photo_hidden_pois WHERE owner=? ORDER BY poi',(owner,)).fetchall()
        result={}
        for row in rows:result.setdefault(row['search'],[]).append(row['poi'])
        return result
    def recent(self,request,response,*,touch=False):
        guest,user=self.identity(request,response,touch=touch);now=time.time();items=[]
        with self.db() as db:
            prune_records(db)
            db.execute('DELETE FROM photo_guests WHERE expires<?',(now,))
            rows=db.execute('SELECT * FROM photo_task_owners WHERE user_id=? OR (user_id IS NULL AND guest=?) ORDER BY created DESC',(user,guest)).fetchall()
            for row in rows:
                owner='user:'+row['user_id'] if row['user_id'] else 'guest:'+row['guest']
                if db.execute('SELECT 1 FROM photo_removed_items WHERE owner=? AND kind=? AND job=?',(owner,row['kind'],row['job'])).fetchone():continue
                table='photo_scout_jobs' if row['kind']=='search' else 'photo_portraits'
                columns='id,created,state,error'+(',expires' if row['kind']=='portrait' else ',payload')
                task=db.execute('SELECT '+columns+' FROM '+table+' WHERE id=?',(row['job'],)).fetchone()
                if not task:continue
                expiry=None if row['user_id'] else db.execute('SELECT expires FROM photo_guests WHERE hash=?',(row['guest'],)).fetchone()[0]
                if expiry is not None and expiry<=now:continue
                context=json.loads(row['context'])
                if row['kind']=='search':context['query']=json.loads(task['payload']).get('query','')
                items.append({'id':row['job'],'kind':row['kind'],'created':task['created'],'expiresAt':expiry,'state':task['state'],'error':task['error'],'context':context})
        return items

def create_tasks_router(settings,require_api):
    router=APIRouter(tags=['Photo Scout']);store=TaskStore(settings.database_path)
    @router.get('/photo-scout/v1/tasks')
    def tasks(request:Request,response:Response,visit:bool=False):
        require_api(request.headers.get('authorization'));response.headers['Cache-Control']='private, no-store'
        items=store.recent(request,response,touch=visit);_,user=store.identity(request)
        return {'items':items,'hiddenPois':store.hidden_pois(request),'searchRetentionDays':None if user else 7,'photoRetentionDays':None if user else 7,'retention':'permanent' if user else 'seven_days_inactive'}
    @router.post('/photo-scout/v1/poi-view')
    def save_poi_view(payload:PoiViewRequest,request:Request,response:Response):
        from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
        from .publications import poi_key
        require_api(request.headers.get('authorization'))
        response.headers['Cache-Control']='private, no-store'
        if request.headers.get('origin')!=os.getenv('PHOTO_SCOUT_WEB_ORIGIN','https://aisoup.net').rstrip('/'):
            raise HTTPException(403,'Invalid viewpoint request')
        guest,user=store.identity(request)
        if not guest and not user:raise HTTPException(401,'Open Photo Scout before editing a view')
        owner='user:'+user if user else 'guest:'+guest
        with store.db() as db:
            if user:
                session=db.execute('SELECT csrf FROM photo_sessions WHERE hash=? AND expires>?',(digest(request.cookies.get(ACCOUNT_COOKIE,'')),time.time())).fetchone()
                if not session or not hmac.compare_digest(request.headers.get('x-csrf-token',''),session['csrf']):raise HTTPException(403,'Invalid account request')
            # Serialize result read-modify-write across places and the independent selfie scope.
            db.execute('BEGIN IMMEDIATE')
            allowed=store.allowed('search',payload.searchId,request)
            legacy=db.execute('SELECT record FROM photo_account_history WHERE user_id=? AND id=?',(user,payload.searchId)).fetchone() if user else None
            if not allowed and not legacy:raise HTTPException(404,'Search history unavailable')
            row=db.execute('SELECT state,result FROM photo_scout_jobs WHERE id=?',(payload.searchId,)).fetchone() if allowed else None
            record=json.loads(legacy['record']) if legacy else None
            result=json.loads(row['result']) if row and row['state']=='complete' and row['result'] else (record or {}).get('result')
            if not result:raise HTTPException(409,'Wait for your search to finish')
            matches=[spot for field in ('poiResults','spots') for spot in result.get(field,[]) if poi_key(spot)==payload.poiId and spot.get('provider')=='google-street-view']
            if not matches:raise HTTPException(404,'Street View place unavailable')
            # Derive the URL from the saved scene, never from a caller-supplied URL.
            url=urlsplit(matches[0]['sourceUrl']);params=dict(parse_qsl(url.query))
            params.update(heading=str(payload.heading),pitch=str(payload.pitch),fov=str(payload.fov))
            override={'sourceUrl':urlunsplit(url._replace(query=urlencode(params))),
                      'viewHeadingDegrees':payload.heading,'viewPitchDegrees':payload.pitch,'viewFovDegrees':payload.fov,
                      'viewAdjusted':True,'imageUrl':None,'streetViewReference':None}
            def update(result):
                for field in ('poiResults','spots'):
                    for spot in result.get(field,[]):
                        if poi_key(spot)==payload.poiId and spot.get('provider')=='google-street-view':spot.update(override)
            if payload.scope=='selfie':
                override['framing']=payload.framing
                result.setdefault('selfieViews',{})[payload.poiId]=override
            else:update(result)
            if row:db.execute('UPDATE photo_scout_jobs SET result=? WHERE id=?',(json.dumps(result),payload.searchId))
            if record:
                if payload.scope=='selfie':record['result'].setdefault('selfieViews',{})[payload.poiId]=override
                else:update(record['result'])
                db.execute('UPDATE photo_account_history SET record=? WHERE user_id=? AND id=?',(json.dumps(record),user,payload.searchId))
            if payload.scope=='place' and db.execute("SELECT 1 FROM sqlite_master WHERE name='photo_publications'").fetchone():
                for published in db.execute("SELECT id,snapshot FROM photo_publications WHERE owner=? AND source=? AND kind IN ('search','place')",(owner,payload.searchId)).fetchall():
                    snapshot=json.loads(published['snapshot']);update(snapshot.get('result',{}))
                    db.execute('UPDATE photo_publications SET snapshot=? WHERE id=?',(json.dumps(snapshot),published['id']))
        return {'ok':True,'view':override}
    @router.post('/photo-scout/v1/hidden-pois')
    def hide_poi(payload:HiddenPoiRequest,request:Request,response:Response):
        response.headers['Cache-Control']='private, no-store'
        require_api(request.headers.get('authorization'))
        if request.headers.get('origin')!=os.getenv('PHOTO_SCOUT_WEB_ORIGIN','https://aisoup.net').rstrip('/'):
            raise HTTPException(403,'Invalid history request')
        guest,user=store.identity(request)
        if not guest and not user:raise HTTPException(401,'Open Photo Scout before editing history')
        with store.db() as db:
            if user:
                session=db.execute('SELECT csrf FROM photo_sessions WHERE hash=? AND expires>?',(digest(request.cookies.get(ACCOUNT_COOKIE,'')),time.time())).fetchone()
                if not session or not hmac.compare_digest(request.headers.get('x-csrf-token',''),session['csrf']):raise HTTPException(403,'Invalid account request')
            allowed=store.allowed('search',payload.searchId,request)
            if not allowed and user:
                allowed=bool(db.execute('SELECT 1 FROM photo_account_history WHERE user_id=? AND id=?',(user,payload.searchId)).fetchone())
            if not allowed:raise HTTPException(404,'Search history unavailable')
            owner='user:'+user if user else 'guest:'+guest
            if payload.hidden:
                db.execute('INSERT OR IGNORE INTO photo_hidden_pois VALUES(?,?,?)',(owner,payload.searchId,payload.poiId))
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='photo_publications'").fetchone():
                    from .publications import poi_key
                    db.execute("UPDATE photo_publications SET active=0 WHERE owner=? AND source=? AND kind='place' AND poi=?",(owner,payload.searchId,payload.poiId))
                    for publication in db.execute("SELECT id,snapshot FROM photo_publications WHERE owner=? AND source=? AND kind='search' AND active=1",(owner,payload.searchId)).fetchall():
                        snapshot=json.loads(publication['snapshot'])
                        snapshot['result']['spots']=[spot for spot in snapshot['result']['spots'] if poi_key(spot)!=payload.poiId]
                        db.execute('UPDATE photo_publications SET snapshot=? WHERE id=?',(json.dumps(snapshot),publication['id']))
            else:db.execute('DELETE FROM photo_hidden_pois WHERE owner=? AND search=? AND poi=?',(owner,payload.searchId,payload.poiId))
        return {'hiddenPois':store.hidden_pois(request)}
    @router.post('/photo-scout/v1/removed-items')
    def remove_item(payload:RemovedItemRequest,request:Request,response:Response):
        require_api(request.headers.get('authorization'))
        response.headers['Cache-Control']='private, no-store'
        if request.headers.get('origin')!=os.getenv('PHOTO_SCOUT_WEB_ORIGIN','https://aisoup.net').rstrip('/'):
            raise HTTPException(403,'Invalid history request')
        guest,user=store.identity(request)
        if not guest and not user:raise HTTPException(401,'Open Photo Scout before editing history')
        with store.db() as db:
            if user:
                session=db.execute('SELECT csrf FROM photo_sessions WHERE hash=? AND expires>?',(digest(request.cookies.get(ACCOUNT_COOKIE,'')),time.time())).fetchone()
                if not session or not hmac.compare_digest(request.headers.get('x-csrf-token',''),session['csrf']):raise HTTPException(403,'Invalid account request')
            allowed=store.allowed(payload.kind,payload.id,request)
            if not allowed and user and payload.kind=='search':
                allowed=bool(db.execute('SELECT 1 FROM photo_account_history WHERE user_id=? AND id=?',(user,payload.id)).fetchone())
            if not allowed:raise HTTPException(404,'History item unavailable')
            owner='user:'+user if user else 'guest:'+guest
            if payload.removed:
                db.execute('INSERT OR IGNORE INTO photo_removed_items VALUES(?,?,?)',(owner,payload.kind,payload.id))
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='photo_publications'").fetchone():
                    kinds=('search','place') if payload.kind=='search' else ('photo','photo')
                    db.execute('UPDATE photo_publications SET active=0 WHERE owner=? AND source=? AND kind IN (?,?)',(owner,payload.id,*kinds))
            else:db.execute('DELETE FROM photo_removed_items WHERE owner=? AND kind=? AND job=?',(owner,payload.kind,payload.id))
        return {'ok':True}
    return router
