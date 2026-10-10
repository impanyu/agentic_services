"""Google OAuth sessions and account-owned Photo Scout search history."""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from urllib.parse import urlencode
import httpx
import jwt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from .tasks import TaskStore

COOKIE='photo_scout_session'
STATE_COOKIE='photo_scout_oauth_state'

class HistoryItem(BaseModel):
    id: str = Field(min_length=1,max_length=80,pattern=r'^[A-Za-z0-9:_-]+$')
    created: int = Field(ge=0)
    label: str = Field(max_length=1000)
    radius: int = Field(ge=100,le=20000)
    checked: bool = True
    result: dict


def digest(value):return hashlib.sha256(value.encode()).hexdigest()


def create_accounts_router(settings,require_api):
    router=APIRouter()
    origin=os.getenv('PHOTO_SCOUT_WEB_ORIGIN','https://aisoup.net').rstrip('/')
    callback=os.getenv('PHOTO_SCOUT_OAUTH_CALLBACK','https://api.aisoup.net/photo-scout/v1/auth/callback')
    secure=callback.startswith('https:')
    def config():return os.getenv('PHOTO_SCOUT_GOOGLE_CLIENT_ID',''),os.getenv('PHOTO_SCOUT_GOOGLE_CLIENT_SECRET','')
    def db():
        d=sqlite3.connect(settings.database_path,timeout=15);d.row_factory=sqlite3.Row;return d
    with db() as d:
        d.execute('CREATE TABLE IF NOT EXISTS photo_oauth_states (hash TEXT PRIMARY KEY, verifier TEXT NOT NULL, nonce TEXT NOT NULL, expires REAL NOT NULL)')
        d.execute('CREATE TABLE IF NOT EXISTS photo_sessions (hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, user_json TEXT NOT NULL, csrf TEXT NOT NULL, expires REAL NOT NULL)')
        d.execute('CREATE TABLE IF NOT EXISTS photo_removed_items (owner TEXT NOT NULL, kind TEXT NOT NULL, job TEXT NOT NULL, PRIMARY KEY(owner,kind,job))')
        d.execute('CREATE TABLE IF NOT EXISTS photo_account_history (user_id TEXT NOT NULL, id TEXT NOT NULL, created INTEGER NOT NULL, record TEXT NOT NULL, PRIMARY KEY(user_id,id))')
    def session(request):
        require_api(request.headers.get('authorization'))
        token=request.cookies.get(COOKIE,'')
        if len(token)>200:return None
        with db() as d:return d.execute('SELECT * FROM photo_sessions WHERE hash=? AND expires>?',(digest(token),time.time())).fetchone()
    def user_session(request,write=False):
        row=session(request)
        if not row:raise HTTPException(401,'Sign in to access account history')
        if write and (request.headers.get('origin')!=origin or not hmac.compare_digest(request.headers.get('x-csrf-token',''),row['csrf'])):
            raise HTTPException(403,'Invalid account request')
        return row
    def cookie(response,name,value,age):
        response.set_cookie(name,value,max_age=age,httponly=True,secure=secure,samesite='lax',path='/photo-scout/v1/auth/' if name==STATE_COOKIE else '/photo-scout/')
        return response
    @router.get('/photo-scout/v1/auth/me')
    def me(request:Request):
        row=session(request)
        return JSONResponse({'configured':all(config()),'user':json.loads(row['user_json']) if row else None,'csrfToken':row['csrf'] if row else None},headers={'Cache-Control':'private, no-store'})
    @router.get('/photo-scout/v1/auth/login')
    def login(request:Request):
        require_api(request.headers.get('authorization'))
        client,secret=config()
        if not client or not secret:raise HTTPException(503,'Google sign-in is not configured yet')
        state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(48);nonce=secrets.token_urlsafe(32)
        with db() as d:
            d.execute('DELETE FROM photo_oauth_states WHERE expires<?',(time.time(),))
            d.execute('INSERT INTO photo_oauth_states VALUES(?,?,?,?)',(digest(state),verifier,nonce,time.time()+600))
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        url='https://accounts.google.com/o/oauth2/v2/auth?'+urlencode({'client_id':client,'redirect_uri':callback,'response_type':'code','scope':'openid email profile','state':state,'nonce':nonce,'code_challenge':challenge,'code_challenge_method':'S256','prompt':'select_account'})
        return cookie(RedirectResponse(url,status_code=302),STATE_COOKIE,state,600)
    @router.get('/photo-scout/v1/auth/callback')
    async def finish(request:Request,state:str='',code:str='',error:str=''):
        require_api(request.headers.get('authorization'))
        expected=request.cookies.get(STATE_COOKIE,'')
        if not state or len(state)>200 or not hmac.compare_digest(state,expected):raise HTTPException(400,'Invalid sign-in state')
        with db() as d:
            d.execute('BEGIN IMMEDIATE')
            pending=d.execute('SELECT * FROM photo_oauth_states WHERE hash=? AND expires>?',(digest(state),time.time())).fetchone()
            d.execute('DELETE FROM photo_oauth_states WHERE hash=?',(digest(state),))
        if not pending:raise HTTPException(400,'Sign-in expired; please try again')
        if error:return cookie(RedirectResponse(origin+'/photo-scout/?login=cancelled',302),STATE_COOKIE,'',0)
        client,secret=config()
        if not code or len(code)>4000:raise HTTPException(400,'Missing sign-in code')
        try:
            async with httpx.AsyncClient(timeout=20,follow_redirects=False) as c:
                response=await c.post('https://oauth2.googleapis.com/token',data={'client_id':client,'client_secret':secret,'code':code,'code_verifier':pending['verifier'],'grant_type':'authorization_code','redirect_uri':callback})
                response.raise_for_status();id_token=response.json()['id_token']
                header=jwt.get_unverified_header(id_token)
                keys=await c.get('https://www.googleapis.com/oauth2/v3/certs');keys.raise_for_status()
                key=next(k for k in keys.json()['keys'] if k['kid']==header['kid'])
                claims=jwt.decode(id_token,jwt.PyJWK.from_dict(key).key,algorithms=['RS256'],audience=client,issuer=['accounts.google.com','https://accounts.google.com'],options={'require':['exp','iat','sub','aud','iss','nonce']},leeway=30)
                if not hmac.compare_digest(claims['nonce'],pending['nonce']) or not claims.get('email_verified'):raise ValueError('Identity not verified')
        except Exception as e:raise HTTPException(400,'Google sign-in could not be verified; please try again') from e
        user={'id':'google:'+claims['sub'],'name':str(claims.get('name') or claims.get('email') or 'Google user')[:200],'email':str(claims.get('email',''))[:320]}
        token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
        with db() as d:
            d.execute('DELETE FROM photo_sessions WHERE expires<?',(time.time(),))
            d.execute('INSERT INTO photo_sessions VALUES(?,?,?,?,?)',(digest(token),user['id'],json.dumps(user),csrf,time.time()+30*86400))
        TaskStore(settings.database_path).attach_user(request,user['id'])
        response=cookie(RedirectResponse(origin+'/photo-scout/',302),COOKIE,token,30*86400)
        return cookie(response,STATE_COOKIE,'',0)
    @router.post('/photo-scout/v1/auth/logout')
    def logout(request:Request):
        row=user_session(request,True)
        with db() as d:d.execute('DELETE FROM photo_sessions WHERE hash=?',(row['hash'],))
        return cookie(JSONResponse({'ok':True}),COOKIE,'',0)
    def map_history_result(result):
        result.pop('imageAssessments',None)
        result.pop('reportToken',None)
        for field in ('spots','poiResults'):
            for spot in result.get(field,[]):
                # Keep proof that this was a scored image even after removing temporary URLs.
                spot['verifiedImageAvailable']=bool(spot.get('verifiedImageAvailable') or spot.get('imageUrl') or spot.get('imageReference') or spot.get('streetViewReference') or (spot.get('provider')=='google-street-view' and spot.get('sourceUrl')))
                spot.pop('imageUrl',None)
                spot.pop('streetViewReference',None)
        return result

    @router.get('/photo-scout/v1/history')
    def history(request:Request):
        row=user_session(request);owner='user:'+row['user_id'];hidden={}
        with db() as d:
            items={r['id']:json.loads(r['record']) for r in d.execute("SELECT id,record FROM photo_account_history h WHERE user_id=? AND NOT EXISTS (SELECT 1 FROM photo_removed_items r WHERE r.owner=? AND r.kind='search' AND r.job=h.id)",(row['user_id'],owner))}
            tables={r[0] for r in d.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            # Completed tasks are the durable source of truth; browser sync may be missing.
            if {'photo_task_owners','photo_scout_jobs'}<=tables:
                reports=d.execute("SELECT o.job,o.created,o.context,j.payload,j.result FROM photo_task_owners o JOIN photo_scout_jobs j ON j.id=o.job WHERE o.user_id=? AND o.kind='search' AND j.state='complete' AND j.result IS NOT NULL AND NOT EXISTS (SELECT 1 FROM photo_removed_items r WHERE r.owner=? AND r.kind='search' AND r.job=o.job)",(row['user_id'],owner))
                for report in reports:
                    context=json.loads(report['context']);context['query']=json.loads(report['payload']).get('query',context.get('query',''))
                    result=json.loads(report['result']);result['searchContext']=context
                    previous=items.get(report['job'],{})
                    items[report['job']]={'id':report['job'],'created':int(report['created']*1000),'label':previous.get('label') or context.get('query') or context.get('locationLabel') or f"Around {context.get('lat')}, {context.get('lon')}",'radius':context.get('radius') or 5000,'checked':previous.get('checked',True),'result':result}
            if 'photo_hidden_pois' in tables:
                for place in d.execute('SELECT search,poi FROM photo_hidden_pois WHERE owner=?',(owner,)):
                    hidden.setdefault(place['search'],[]).append(place['poi'])
        for record in items.values():map_history_result(record['result'])
        return JSONResponse({'items':sorted(items.values(),key=lambda h:h['created'],reverse=True),'hiddenPois':hidden},headers={'Cache-Control':'private, no-store'})
    @router.post('/photo-scout/v1/history')
    def save_history(item:HistoryItem,request:Request):
        row=user_session(request,True);record=item.model_dump()
        # Persist derived reports, not image bytes, private proxies, or report tokens.
        result=record['result'];result.pop('imageAssessments',None)
        for name in ('spots','poiResults'):
            views=result.get(name,[])
            if not isinstance(views,list) or len(views)>50:raise HTTPException(422,'Invalid history views')
            for spot in views:
                if not isinstance(spot,dict):raise HTTPException(422,'Invalid history record')
                spot['verifiedImageAvailable']=bool(spot.get('verifiedImageAvailable') or spot.get('imageUrl') or spot.get('imageReference') or spot.get('streetViewReference') or (spot.get('provider')=='google-street-view' and spot.get('sourceUrl')))
                spot.pop('imageUrl',None);spot.pop('streetViewReference',None)
        result.pop('reportToken',None)
        encoded=json.dumps(record)
        if len(encoded.encode())>150000:raise HTTPException(413,'History record is too large')
        with db() as d:
            d.execute('INSERT OR REPLACE INTO photo_account_history VALUES(?,?,?,?)',(row['user_id'],item.id,item.created,encoded))
        return {'ok':True}
    return router
