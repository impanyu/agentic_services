"""Owner-authorized public snapshots, independent of private task retention."""
import hmac
import io
from PIL import Image, ImageOps
import json
import os
import secrets
import time
from typing import Literal
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from .tasks import ACCOUNT_COOKIE, TaskStore, digest

class PublishRequest(BaseModel):
    kind: Literal['search', 'place', 'photo']
    id: str = Field(min_length=1, max_length=80)
    poiId: str | None = Field(default=None, max_length=500)

class WithdrawRequest(BaseModel):
    id: str = Field(min_length=1, max_length=80)

def poi_key(spot):
    poi=spot.get('poi') or {}
    return str(poi.get('id') or spot.get('id') or f"geo:{poi.get('lat')},{poi.get('lon')}:{spot.get('name')}")

def public_spot(spot):
    # Never publish internal fetch references, job tokens, or uploaded originals.
    fields=('id','name','score','confidence','recommend','provider','poi','sourceUrl','imageUrl','viewHeadingDegrees','viewPitchDegrees','viewFovDegrees','visible_evidence','photo_tip','uncertainty','coordinateWarning','author','license','licenseUrl','sourceDate','capturedAt','locationType','distanceMeters')
    result={k:spot[k] for k in fields if k in spot}
    if result.get('provider')=='google-street-view':result.pop('imageUrl',None)
    return result

def create_publications_router(settings,require_api):
    router=APIRouter(tags=['Photo Scout']);store=TaskStore(settings.database_path)
    with store.db() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS photo_publications (
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL, source TEXT NOT NULL,
            poi TEXT NOT NULL DEFAULT '', title TEXT NOT NULL, created REAL NOT NULL,
            snapshot TEXT NOT NULL, image BLOB, active INTEGER NOT NULL DEFAULT 1,
            UNIQUE(owner,kind,source,poi))''')
        db.execute('CREATE INDEX IF NOT EXISTS photo_publications_active ON photo_publications(active,created)')
    def owner(request):
        guest,user=store.identity(request)
        return 'user:'+user if user else 'guest:'+guest if guest else None
    def write_owner(request):
        require_api(request.headers.get('authorization'))
        if request.headers.get('origin')!=os.getenv('PHOTO_SCOUT_WEB_ORIGIN','https://aisoup.net').rstrip('/'):
            raise HTTPException(403,'Invalid publication request')
        identity=owner(request)
        if not identity:raise HTTPException(401,'Open Photo Scout before publishing')
        if identity.startswith('user:'):
            with store.db() as db:
                session=db.execute('SELECT csrf FROM photo_sessions WHERE hash=? AND expires>?',(digest(request.cookies.get(ACCOUNT_COOKIE,'')),time.time())).fetchone()
            if not session or not hmac.compare_digest(request.headers.get('x-csrf-token',''),session['csrf']):
                raise HTTPException(403,'Invalid account request')
        return identity
    def metadata(row,mine=False):
        data={'id':row['id'],'kind':row['kind'],'title':row['title'],'created':row['created'],'mine':mine,
              'url':'https://aisoup.net/photo-scout/?published='+row['id']}
        if row['kind']=='photo':data['imageUrl']=settings.base_url.rstrip('/')+'/photo-scout/v1/publications/'+row['id']+'/image'
        return data
    @router.post('/photo-scout/v1/publications')
    def publish(payload:PublishRequest,request:Request,response:Response):
        identity=write_owner(request);kind='portrait' if payload.kind=='photo' else 'search'
        allowed=store.allowed(kind,payload.id,request)
        legacy=None
        with store.db() as db:
            if not allowed and kind=='search' and identity.startswith('user:'):
                row=db.execute('SELECT record FROM photo_account_history WHERE user_id=? AND id=?',(identity[5:],payload.id)).fetchone()
                if row:legacy=json.loads(row['record']);allowed=True
            if not allowed or db.execute('SELECT 1 FROM photo_removed_items WHERE owner=? AND kind=? AND job=?',(identity,kind,payload.id)).fetchone():raise HTTPException(404,'Only your own visible, completed items can be published')
            image=None
            if kind=='portrait':
                row=db.execute('SELECT state,output FROM photo_portraits WHERE id=?',(payload.id,)).fetchone()
                if not row or row['state']!='complete' or not row['output']:raise HTTPException(409,'Wait for your photo to finish')
                context=store.context(kind,payload.id) or {};title=context.get('name') or 'Selfie'
                spot=public_spot(context)
                generation=context.get('generation') or {}
                if generation:spot['generation']={k:generation[k] for k in ('style','posture','weather','expression','framing') if k in generation}
                snapshot={'context':spot};image=row['output']
            else:
                row=db.execute('SELECT state,result FROM photo_scout_jobs WHERE id=?',(payload.id,)).fetchone()
                if legacy:result=legacy.get('result') or {};context=result.get('searchContext') or {}
                else:
                    if not row or row['state']!='complete' or not row['result']:raise HTTPException(409,'Wait for your search to finish')
                    result=json.loads(row['result']);context=store.context(kind,payload.id) or {}
                hidden=store.hidden_pois(request).get(payload.id,[]);seen=set();spots=[]
                for spot in sorted([*(result.get('poiResults') or []),*(result.get('spots') or [])],key=lambda s:-(s.get('score') or 0)):
                    key=poi_key(spot)
                    if key in seen or key in hidden or spot.get('score') is None or not (spot.get('imageUrl') or spot.get('provider')=='google-street-view'):continue
                    seen.add(key);spots.append(public_spot(spot))
                if payload.kind=='place':
                    spots=[s for s in spots if poi_key(s)==payload.poiId]
                    if not spots:raise HTTPException(404,'Photo place unavailable')
                    title=spots[0].get('name') or 'Photo place'
                    snapshot={'result':{'spots':spots,'summary':spots[0].get('visible_evidence','')}}
                else:
                    title=context.get('query') or context.get('locationLabel') or (legacy or {}).get('label') or 'Photo search'
                    snapshot={'result':{'spots':spots,'summary':result.get('summary',''),'photoStyles':result.get('photoStyles',[])},
                              'searchContext':{k:context[k] for k in ('lat','lon','radius','query','locationLabel') if k in context}}
            key=payload.poiId if payload.kind=='place' else ''
            old=db.execute('SELECT id,created,active FROM photo_publications WHERE owner=? AND kind=? AND source=? AND poi=?',(identity,payload.kind,payload.id,key)).fetchone()
            if (not old or not old['active']) and db.execute('SELECT count(*) FROM photo_publications WHERE owner=? AND active=1',(identity,)).fetchone()[0]>=500:
                raise HTTPException(429,'Publication limit reached')
            ident=old['id'] if old else secrets.token_urlsafe(18);created=old['created'] if old else time.time()
            db.execute('''INSERT INTO photo_publications VALUES(?,?,?,?,?,?,?,?,?,1)
                ON CONFLICT(owner,kind,source,poi) DO UPDATE SET snapshot=excluded.snapshot,image=excluded.image,title=excluded.title,active=1''',
                (ident,identity,payload.kind,payload.id,key,str(title)[:500],created,json.dumps(snapshot),image))
            saved=db.execute('SELECT * FROM photo_publications WHERE id=?',(ident,)).fetchone()
        response.headers['Cache-Control']='private, no-store'
        return metadata(saved,True)
    @router.post('/photo-scout/v1/publications/withdraw')
    def withdraw(payload:WithdrawRequest,request:Request):
        identity=write_owner(request)
        with store.db() as db:
            changed=db.execute('UPDATE photo_publications SET active=0 WHERE id=? AND owner=? AND active=1',(payload.id,identity)).rowcount
        if not changed:raise HTTPException(404,'Publication unavailable')
        return {'ok':True}
    @router.get('/photo-scout/v1/publications')
    def listing(request:Request,response:Response,before:float | None=None):
        identity=owner(request);response.headers['Cache-Control']='no-store'
        with store.db() as db:
            rows=db.execute('SELECT id,owner,kind,title,created,snapshot FROM photo_publications WHERE active=1 AND created<? ORDER BY created DESC LIMIT 51',(before or time.time()+1,)).fetchall()
        return {'items':[{**metadata(r,r['owner']==identity),**json.loads(r['snapshot'])} for r in rows[:50]],'nextBefore':rows[49]['created'] if len(rows)>50 else None}
    @router.get('/photo-scout/v1/publications/mine')
    def mine(request:Request,response:Response):
        require_api(request.headers.get('authorization'))
        response.headers['Cache-Control']='private, no-store'
        identity=owner(request)
        if not identity:return {'items':[]}
        with store.db() as db:rows=db.execute('SELECT id,kind,title,created,source,poi FROM photo_publications WHERE owner=? AND active=1',(identity,)).fetchall()
        return {'items':[{**metadata(row,True),'sourceId':row['source'],'poiId':row['poi']} for row in rows]}
    @router.get('/photo-scout/v1/publications/{ident}')
    def detail(ident:str,request:Request,response:Response):
        with store.db() as db:row=db.execute('SELECT * FROM photo_publications WHERE id=? AND active=1',(ident,)).fetchone()
        if not row:raise HTTPException(404,'This publication is unavailable or has been withdrawn')
        response.headers['Cache-Control']='no-store'
        return {**metadata(row,row['owner']==owner(request)),**json.loads(row['snapshot'])}
    @router.get('/photo-scout/v1/publications/{ident}/thumbnail')
    def thumbnail(ident:str):
        with store.db() as db:row=db.execute("SELECT image FROM photo_publications WHERE id=? AND kind='photo' AND active=1",(ident,)).fetchone()
        if not row or not row['image']:raise HTTPException(404,'Published photo unavailable')
        with Image.open(io.BytesIO(row['image'])) as source:
            preview=ImageOps.exif_transpose(source).convert('RGB');preview.thumbnail((160,160))
            output=io.BytesIO();preview.save(output,format='JPEG',quality=78,optimize=True)
        return Response(output.getvalue(),media_type='image/jpeg',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
    @router.get('/photo-scout/v1/publications/{ident}/image')
    def image(ident:str):
        with store.db() as db:row=db.execute("SELECT image FROM photo_publications WHERE id=? AND kind='photo' AND active=1",(ident,)).fetchone()
        if not row or not row['image']:raise HTTPException(404,'Published photo unavailable')
        return Response(bytes(row['image']),media_type='image/png',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
    return router
