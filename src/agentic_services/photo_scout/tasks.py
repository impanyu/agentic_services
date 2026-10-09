"""Server-owned task recovery for anonymous browsers and Google accounts."""
import hashlib
import json
import secrets
import sqlite3
import time
from fastapi import APIRouter, HTTPException, Request, Response

GUEST_COOKIE='photo_scout_guest'
ACCOUNT_COOKIE='photo_scout_session'
SEARCH_RETENTION=7*86400
ACCOUNT_EXPIRY=253402300799.0
PAID_SEARCH_RETENTION=30*86400
ACTIVE_TASK_LIMIT=5

def digest(token):return hashlib.sha256(token.encode()).hexdigest()

def prune_records(db):
    """Account ownership protects both records and generated image bytes."""
    now=time.time()
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    owners='photo_task_owners' in tables
    if 'photo_scout_jobs' in tables:
        if owners:
            db.execute("""DELETE FROM photo_scout_jobs WHERE
                NOT EXISTS (SELECT 1 FROM photo_task_owners o WHERE o.kind='search' AND o.job=photo_scout_jobs.id AND o.user_id IS NOT NULL)
                AND created < ? - CASE WHEN EXISTS (SELECT 1 FROM photo_task_owners o WHERE o.kind='search' AND o.job=photo_scout_jobs.id) THEN ? ELSE ? END""",(now,SEARCH_RETENTION,PAID_SEARCH_RETENTION))
        else:db.execute('DELETE FROM photo_scout_jobs WHERE created<?',(now-PAID_SEARCH_RETENTION,))
    if 'photo_portraits' in tables:
        guard="AND NOT EXISTS (SELECT 1 FROM photo_task_owners o WHERE o.kind='portrait' AND o.job=photo_portraits.id AND o.user_id IS NOT NULL)" if owners else ''
        db.execute('DELETE FROM photo_portraits WHERE (expires<? OR created<?) '+guard,(now,now-SEARCH_RETENTION))
    if owners:db.execute('DELETE FROM photo_task_owners WHERE user_id IS NULL AND created<?',(now-SEARCH_RETENTION,))

class TaskStore:
    def __init__(self,path):
        self.path=path
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS photo_guests (hash TEXT PRIMARY KEY, expires REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS photo_task_owners (kind TEXT NOT NULL, job TEXT NOT NULL, guest TEXT, user_id TEXT, created REAL NOT NULL, context TEXT NOT NULL, PRIMARY KEY(kind,job))')
            db.execute('CREATE INDEX IF NOT EXISTS photo_task_owner_user ON photo_task_owners(user_id,created)')
            db.execute('CREATE INDEX IF NOT EXISTS photo_task_owner_guest ON photo_task_owners(guest,created)')
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='photo_portraits'").fetchone():
                db.execute("UPDATE photo_portraits SET expires=? WHERE expires<? AND EXISTS (SELECT 1 FROM photo_task_owners o WHERE o.kind='portrait' AND o.job=photo_portraits.id AND o.user_id IS NOT NULL)",(ACCOUNT_EXPIRY,ACCOUNT_EXPIRY))
    def db(self):
        db=sqlite3.connect(self.path,timeout=15);db.row_factory=sqlite3.Row;return db
    def identity(self,request,response=None):
        now=time.time();guest=None;user=None
        with self.db() as db:
            token=request.cookies.get(GUEST_COOKIE,'')
            if token and len(token)<=200:
                row=db.execute('SELECT hash FROM photo_guests WHERE hash=? AND expires>?',(digest(token),now)).fetchone()
                if row:
                    guest=row['hash']
                    if response is not None:
                        db.execute('UPDATE photo_guests SET expires=? WHERE hash=?',(now+SEARCH_RETENTION,guest))
                        response.set_cookie(GUEST_COOKIE,token,max_age=SEARCH_RETENTION,httponly=True,secure=True,samesite='lax',path='/photo-scout/')
            account=request.cookies.get(ACCOUNT_COOKIE,'')
            if account and len(account)<=200 and db.execute("SELECT 1 FROM sqlite_master WHERE name='photo_sessions'").fetchone():
                row=db.execute('SELECT user_id FROM photo_sessions WHERE hash=? AND expires>?',(digest(account),now)).fetchone()
                if row:user=row['user_id']
            if guest is None and response is not None:
                token=secrets.token_urlsafe(32);guest=digest(token)
                db.execute('INSERT INTO photo_guests VALUES(?,?)',(guest,now+SEARCH_RETENTION))
                response.set_cookie(GUEST_COOKIE,token,max_age=SEARCH_RETENTION,httponly=True,secure=True,samesite='lax',path='/photo-scout/')
        return guest,user
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
        with self.db() as db:row=db.execute('SELECT context FROM photo_task_owners WHERE kind=? AND job=?',(kind,job)).fetchone()
        return json.loads(row['context']) if row else None
    def update_context(self,kind,job,context):
        with self.db() as db:db.execute('UPDATE photo_task_owners SET context=? WHERE kind=? AND job=?',(json.dumps(context),kind,job))
    def attach_user(self,request,user):
        guest,_=self.identity(request)
        if guest:
            with self.db() as db:
                prune_records(db)
                db.execute('UPDATE photo_task_owners SET user_id=? WHERE guest=? AND user_id IS NULL',(user,guest))
                db.execute("UPDATE photo_portraits SET expires=? WHERE EXISTS (SELECT 1 FROM photo_task_owners o WHERE o.kind='portrait' AND o.job=photo_portraits.id AND o.user_id=?)",(ACCOUNT_EXPIRY,user))
    def recent(self,request,response):
        guest,user=self.identity(request,response);now=time.time();items=[]
        with self.db() as db:
            prune_records(db)
            db.execute('DELETE FROM photo_guests WHERE expires<?',(now,))
            rows=db.execute('SELECT * FROM photo_task_owners WHERE user_id=? OR (user_id IS NULL AND guest=?) ORDER BY created DESC',(user,guest)).fetchall()
            for row in rows:
                table='photo_scout_jobs' if row['kind']=='search' else 'photo_portraits'
                columns='id,created,state,error'+(',expires' if row['kind']=='portrait' else '')
                task=db.execute('SELECT '+columns+' FROM '+table+' WHERE id=?',(row['job'],)).fetchone()
                if not task:continue
                expiry=None if row['user_id'] else (task['created']+SEARCH_RETENTION if row['kind']=='search' else min(task['expires'],task['created']+SEARCH_RETENTION))
                if expiry is not None and expiry<=now:continue
                items.append({'id':row['job'],'kind':row['kind'],'created':task['created'],'expiresAt':expiry,'state':task['state'],'error':task['error'],'context':json.loads(row['context'])})
        return items

def create_tasks_router(settings,require_api):
    router=APIRouter(tags=['Photo Scout']);store=TaskStore(settings.database_path)
    @router.get('/photo-scout/v1/tasks')
    def tasks(request:Request,response:Response):
        require_api(request.headers.get('authorization'));response.headers['Cache-Control']='private, no-store'
        items=store.recent(request,response);_,user=store.identity(request)
        return {'items':items,'searchRetentionDays':None if user else 7,'photoRetentionDays':None if user else 7,'retention':'permanent' if user else 'seven_days'}
    return router
