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

class CommentRequest(BaseModel):
    text: str = Field(min_length=1,max_length=2000)
    poiId: str = Field(default='',max_length=500)

class ReactionRequest(BaseModel):
    kind: Literal['like','favorite']
    active: bool
    poiId: str = Field(default='',max_length=500)

def poi_key(spot):
    poi=spot.get('poi') or {}
    return str(poi.get('id') or spot.get('id') or f"geo:{poi.get('lat')},{poi.get('lon')}:{spot.get('name')}")

def public_spot(spot):
    # Never publish internal fetch references, job tokens, or uploaded originals.
    fields=('id','name','score','visualScore','locationPriorityBonus','centerDistanceMeters','confidence','recommend','provider','poi','sourceUrl','imageUrl','viewHeadingDegrees','viewPitchDegrees','viewFovDegrees','viewAdjusted','visible_evidence','photo_tip','uncertainty','coordinateWarning','author','license','licenseUrl','sourceDate','capturedAt','locationType','distanceMeters')
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
        db.execute('''CREATE TABLE IF NOT EXISTS photo_publication_comments (
            id TEXT PRIMARY KEY, publication TEXT NOT NULL, poi TEXT NOT NULL DEFAULT '',
            author TEXT NOT NULL, name TEXT NOT NULL, text TEXT NOT NULL, created REAL NOT NULL,
            deleted INTEGER NOT NULL DEFAULT 0)''')
        db.execute('CREATE INDEX IF NOT EXISTS photo_comment_thread ON photo_publication_comments(publication,poi,created)')
        db.execute('CREATE INDEX IF NOT EXISTS photo_comment_author ON photo_publication_comments(author,created)')
        db.execute('''CREATE TABLE IF NOT EXISTS photo_publication_reactions (
            publication TEXT NOT NULL, poi TEXT NOT NULL DEFAULT '', owner TEXT NOT NULL,
            kind TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(publication,poi,owner,kind))''')
        db.execute('CREATE INDEX IF NOT EXISTS photo_reaction_owner ON photo_publication_reactions(owner,kind,created)')
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
    def comment_publication(db,ident,poi=''):
        row=db.execute('SELECT owner,kind,snapshot FROM photo_publications WHERE id=? AND active=1',(ident,)).fetchone()
        if not row:raise HTTPException(404,'This publication is unavailable or has been withdrawn')
        if poi and (row['kind']!='search' or not any(poi_key(s)==poi for s in json.loads(row['snapshot']).get('result',{}).get('spots',[]))):
            raise HTTPException(404,'Published place unavailable')
        return row
    def social_state(db,ident,identity):
        publication=comment_publication(db,ident)
        scopes=['']+[poi_key(s) for s in json.loads(publication['snapshot']).get('result',{}).get('spots',[])] if publication['kind']=='search' else ['']
        threads={poi:{'likes':0,'liked':False,'favorited':False} for poi in scopes}
        for row in db.execute('SELECT poi,kind,count(*) AS total,max(owner=?) AS mine FROM photo_publication_reactions WHERE publication=? GROUP BY poi,kind',(identity or '',ident)):
            if row['poi'] in threads:
                if row['kind']=='like':threads[row['poi']]['likes']=row['total'];threads[row['poi']]['liked']=bool(row['mine'])
                else:threads[row['poi']]['favorited']=bool(row['mine'])
        return {'threads':threads,'canReact':bool(identity and identity.startswith('user:'))}
    @router.get('/photo-scout/v1/publications/{ident}/social')
    def social(ident:str,request:Request,response:Response):
        response.headers['Cache-Control']='private, no-store'
        with store.db() as db:return social_state(db,ident,owner(request))
    @router.post('/photo-scout/v1/publications/{ident}/reactions')
    def react(ident:str,payload:ReactionRequest,request:Request):
        identity=write_owner(request)
        if not identity.startswith('user:'):raise HTTPException(401,'Sign in to like or save')
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE');comment_publication(db,ident,payload.poiId)
            if payload.active:
                exists=db.execute('SELECT 1 FROM photo_publication_reactions WHERE publication=? AND poi=? AND owner=? AND kind=?',(ident,payload.poiId,identity,payload.kind)).fetchone()
                if payload.kind=='favorite' and not exists and db.execute("SELECT count(*) FROM photo_publication_reactions WHERE owner=? AND kind='favorite'",(identity,)).fetchone()[0]>=1000:raise HTTPException(429,'Your saved list is full. Remove an item before saving more.')
                db.execute('INSERT OR IGNORE INTO photo_publication_reactions VALUES(?,?,?,?,?)',(ident,payload.poiId,identity,payload.kind,time.time()))
            else:db.execute('DELETE FROM photo_publication_reactions WHERE publication=? AND poi=? AND owner=? AND kind=?',(ident,payload.poiId,identity,payload.kind))
            return social_state(db,ident,identity)
    @router.get('/photo-scout/v1/favorites')
    def favorites(request:Request,response:Response,before:float|None=None):
        require_api(request.headers.get('authorization'));identity=owner(request)
        response.headers['Cache-Control']='private, no-store'
        if not identity or not identity.startswith('user:'):raise HTTPException(401,'Sign in to view your saved items')
        with store.db() as db:
            rows=db.execute("SELECT p.id,p.kind,p.title,p.created,p.snapshot,r.poi,r.created AS saved FROM photo_publication_reactions r JOIN photo_publications p ON p.id=r.publication WHERE r.owner=? AND r.kind='favorite' AND p.active=1 ORDER BY r.created DESC",(identity,)).fetchall()
        items=[]
        for row in rows:
            if before is not None and row['saved']>=before:continue
            spot=next((s for s in json.loads(row['snapshot']).get('result',{}).get('spots',[]) if poi_key(s)==row['poi']),None) if row['poi'] else None
            if row['poi'] and not spot:continue
            items.append({**metadata(row),'poiId':row['poi'],'savedAt':row['saved'],**({'title':spot.get('name') or row['title'],'kind':'place'} if spot else {})})
            if len(items)==51:break
        return {'items':items[:50],'nextBefore':items[49]['savedAt'] if len(items)>50 else None}
    @router.get('/photo-scout/v1/publications/{ident}/comments')
    def comments(ident:str,request:Request,response:Response,poiId:str='',before:float|None=None):
        identity=owner(request);response.headers['Cache-Control']='private, no-store'
        with store.db() as db:
            publication=comment_publication(db,ident,poiId)
            rows=db.execute('SELECT * FROM photo_publication_comments WHERE publication=? AND poi=? AND deleted=0 AND created<? ORDER BY created DESC,id DESC LIMIT 31',(ident,poiId,before or time.time()+1)).fetchall()
        return {'items':[{'id':r['id'],'name':r['name'],'text':r['text'],'created':r['created'],'mine':identity==r['author'],'canDelete':identity in (r['author'],publication['owner'])} for r in rows[:30]],'nextBefore':rows[29]['created'] if len(rows)>30 else None,'canComment':bool(identity and identity.startswith('user:'))}
    @router.post('/photo-scout/v1/publications/{ident}/comments')
    def add_comment(ident:str,payload:CommentRequest,request:Request):
        identity=write_owner(request)
        if not identity.startswith('user:'):raise HTTPException(401,'Sign in to comment')
        text=payload.text.strip()
        if not text:raise HTTPException(422,'Write a comment before posting')
        now=time.time();comment_id=secrets.token_urlsafe(18)
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            comment_publication(db,ident,payload.poiId)
            if db.execute('SELECT count(*) FROM photo_publication_comments WHERE author=? AND created>?',(identity,now-3600)).fetchone()[0]>=20:
                raise HTTPException(429,'Please wait before posting more comments')
            session=db.execute('SELECT user_json FROM photo_sessions WHERE hash=? AND expires>?',(digest(request.cookies.get(ACCOUNT_COOKIE,'')),now)).fetchone()
            name=str(json.loads(session['user_json']).get('name') or 'Photo Scout member')[:100]
            db.execute('INSERT INTO photo_publication_comments VALUES(?,?,?,?,?,?,?,0)',(comment_id,ident,payload.poiId,identity,name,text,now))
        return {'id':comment_id,'name':name,'text':text,'created':now,'mine':True,'canDelete':True}
    @router.delete('/photo-scout/v1/publications/{ident}/comments/{comment_id}')
    def delete_comment(ident:str,comment_id:str,request:Request):
        identity=write_owner(request)
        with store.db() as db:
            publication=comment_publication(db,ident)
            changed=db.execute('UPDATE photo_publication_comments SET deleted=1 WHERE id=? AND publication=? AND deleted=0 AND (author=? OR ?=?)',(comment_id,ident,identity,identity,publication['owner'])).rowcount
        if not changed:raise HTTPException(404,'Comment unavailable')
        return {'ok':True}
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
