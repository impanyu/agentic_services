"""Server-owned task recovery for anonymous browsers and Google accounts."""
import hashlib
import json
import os
import secrets
import sqlite3
import time
from fastapi import APIRouter, Request, Response

GUEST_COOKIE='photo_scout_guest'
ACCOUNT_COOKIE='photo_scout_session'
SEARCH_RETENTION=30*86400

def digest(token):return hashlib.sha256(token.encode()).hexdigest()

class TaskStore:
    def __init__(self,path):
        self.path=path
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS photo_guests (hash TEXT PRIMARY KEY, expires REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS photo_task_owners (kind TEXT NOT NULL, job TEXT NOT NULL, guest TEXT, user_id TEXT, created REAL NOT NULL, context TEXT NOT NULL, PRIMARY KEY(kind,job))')
            db.execute('CREATE INDEX IF NOT EXISTS photo_task_owner_user ON photo_task_owners(user_id,created)')
            db.execute('CREATE INDEX IF NOT EXISTS photo_task_owner_guest ON photo_task_owners(guest,created)')
    def db(self):
        db=sqlite3.connect(self.path,timeout=15);db.row_factory=sqlite3.Row;return db
    def identity(self,request,response=None):
        now=time.time();guest=None;user=None
        with self.db() as db:
            token=request.cookies.get(GUEST_COOKIE,'')
            if token and len(token)<=200:
                row=db.execute('SELECT hash FROM photo_guests WHERE hash=? AND expires>?',(digest(token),now)).fetchone()
                if row:guest=row['hash']
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
            with self.db() as db:db.execute('UPDATE photo_task_owners SET user_id=? WHERE guest=? AND user_id IS NULL',(user,guest))
    def recent(self,request,response):
        guest,user=self.identity(request,response);now=time.time();items=[]
        with self.db() as db:
            db.execute('DELETE FROM photo_task_owners WHERE created<?',(now-SEARCH_RETENTION,))
            db.execute('DELETE FROM photo_guests WHERE expires<?',(now,))
            rows=db.execute('SELECT * FROM photo_task_owners WHERE user_id=? OR (user_id IS NULL AND guest=?) ORDER BY created DESC LIMIT 60',(user,guest)).fetchall()
            for row in rows:
                table='photo_scout_jobs' if row['kind']=='search' else 'photo_portraits'
                columns='id,created,state,error'+(',expires' if row['kind']=='portrait' else '')
                task=db.execute('SELECT '+columns+' FROM '+table+' WHERE id=?',(row['job'],)).fetchone()
                if not task:continue
                expiry=task['created']+SEARCH_RETENTION if row['kind']=='search' else task['expires']
                if expiry<=now:continue
                items.append({'id':row['job'],'kind':row['kind'],'created':task['created'],'expiresAt':expiry,'state':task['state'],'error':task['error'],'context':json.loads(row['context'])})
        return items

def create_tasks_router(settings,require_api):
    router=APIRouter(tags=['Photo Scout']);store=TaskStore(settings.database_path)
    @router.get('/photo-scout/v1/tasks')
    def tasks(request:Request,response:Response):
        require_api(request.headers.get('authorization'));response.headers['Cache-Control']='private, no-store'
        return {'items':store.recent(request,response),'searchRetentionDays':30,'photoRetentionDays':max(1,min(30,int(os.getenv('PHOTO_SCOUT_PORTRAIT_RETENTION_DAYS','7'))))}
    return router
