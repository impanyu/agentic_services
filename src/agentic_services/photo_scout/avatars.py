"""Private, owner-scoped avatar images for the selfie picker."""
import base64,hmac,io,os,secrets,time
from fastapi import APIRouter,HTTPException,Request,Response
from pydantic import BaseModel,Field
from PIL import Image,ImageOps,UnidentifiedImageError
from pillow_heif import register_heif_opener
register_heif_opener(thumbnails=False,decode_threads=2)
from .tasks import TaskStore,ACCOUNT_COOKIE,digest,prune_records

class AvatarUpload(BaseModel):
    name:str=Field(min_length=1,max_length=80)
    image:str=Field(max_length=28000000)

def normalize_image(data):
    try:
        prefix,encoded=data.split(',',1)
        if prefix not in ('data:image/jpeg;base64','data:image/png;base64','data:image/webp;base64','data:image/heic;base64','data:image/heif;base64','data:application/octet-stream;base64'):raise ValueError()
        raw=base64.b64decode(encoded,validate=True)
        if len(raw)>20000000:raise ValueError()
        with Image.open(io.BytesIO(raw)) as original:
            if original.format not in ('JPEG','PNG','WEBP','HEIF') or original.width*original.height>80000000:raise ValueError()
            image=ImageOps.exif_transpose(original).convert('RGB');image.thumbnail((1600,1600))
            output=io.BytesIO();image.save(output,format='JPEG',quality=90,optimize=True)
        return output.getvalue()
    except (ValueError,UnidentifiedImageError,OSError,Image.DecompressionBombError):
        raise HTTPException(422,'Upload a valid JPG, PNG, WebP or HEIC image, up to 20 MB and 80 megapixels.')

def create_avatars_router(settings,require_api):
    router=APIRouter(tags=['Photo Scout']);store=TaskStore(settings.database_path)
    with store.db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS photo_avatars (id TEXT PRIMARY KEY,owner TEXT NOT NULL,name TEXT NOT NULL,created REAL NOT NULL,image BLOB NOT NULL,removed INTEGER NOT NULL DEFAULT 0)')
        db.execute('CREATE INDEX IF NOT EXISTS photo_avatar_owner ON photo_avatars(owner,created)')
    def owner(request,response,write=False):
        require_api(request.headers.get('authorization'))
        if write and request.headers.get('origin')!=os.getenv('PHOTO_SCOUT_WEB_ORIGIN','https://aisoup.net').rstrip('/'):
            raise HTTPException(403,'Invalid avatar request')
        guest,user=store.identity(request,response)
        if write and user:
            with store.db() as db:row=db.execute('SELECT csrf FROM photo_sessions WHERE hash=? AND expires>?',(digest(request.cookies.get(ACCOUNT_COOKIE,'')),time.time())).fetchone()
            if not row or not hmac.compare_digest(row['csrf'],request.headers.get('x-csrf-token','')):raise HTTPException(403,'Invalid account request')
        response.headers['Cache-Control']='private, no-store'
        return 'user:'+user if user else 'guest:'+guest if guest else None
    def item(row):return {'id':row['id'],'name':row['name'],'created':row['created'],'imageUrl':'/photo-scout/v1/avatars/'+row['id']+'/image'}
    @router.get('/photo-scout/v1/avatars')
    def listing(request:Request,response:Response):
        identity=owner(request,response)
        with store.db() as db:
            prune_records(db)
            rows=db.execute('SELECT id,name,created FROM photo_avatars WHERE owner=? AND removed=0 ORDER BY created DESC',(identity,)).fetchall()
        return {'items':[item(r) for r in rows],'limit':50,'retention':'permanent' if identity.startswith('user:') else 'seven_days_inactive'}
    @router.post('/photo-scout/v1/avatars')
    def upload(payload:AvatarUpload,request:Request,response:Response):
        identity=owner(request,response,True);data=normalize_image(payload.image)
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE');prune_records(db)
            existing=db.execute('SELECT id,name,created FROM photo_avatars WHERE owner=? AND removed=0 AND image=? LIMIT 1',(identity,data)).fetchone()
            if existing:return item(existing)
            if db.execute('SELECT count(*) FROM photo_avatars WHERE owner=? AND removed=0',(identity,)).fetchone()[0]>=50:raise HTTPException(429,'Your library can hold 50 avatars. Remove one before uploading another.')
            record={'id':secrets.token_urlsafe(18),'name':payload.name.strip() or 'My avatar','created':time.time()}
            db.execute('INSERT INTO photo_avatars(id,owner,name,created,image) VALUES(?,?,?,?,?)',(record['id'],identity,record['name'],record['created'],data))
        return item(record)
    @router.get('/photo-scout/v1/avatars/{avatar_id}/image')
    def image(avatar_id:str,request:Request,response:Response):
        identity=owner(request,response)
        with store.db() as db:row=db.execute('SELECT image FROM photo_avatars WHERE id=? AND owner=? AND removed=0',(avatar_id,identity)).fetchone()
        if not row:raise HTTPException(404,'Avatar not found')
        response.headers['Content-Type']='image/jpeg';response.headers['X-Content-Type-Options']='nosniff';response.body=row['image'];response.status_code=200
        response.headers['Content-Length']=str(len(row['image']));return response
    @router.delete('/photo-scout/v1/avatars/{avatar_id}')
    def remove(avatar_id:str,request:Request,response:Response):
        identity=owner(request,response,True)
        with store.db() as db:db.execute('UPDATE photo_avatars SET removed=1 WHERE id=? AND owner=?',(avatar_id,identity))
        return {'removed':True}
    return router
