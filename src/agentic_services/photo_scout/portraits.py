"""Private, short-lived image composition jobs for Photo Scout."""
from __future__ import annotations
import asyncio, base64, hashlib, hmac, io, json, os, re, secrets, sqlite3, time
from urllib.parse import urlsplit,parse_qs
from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel,Field
from openai import AsyncOpenAI
from PIL import Image,ImageOps
from .sources import image_data,image_host

class PortraitRequest(BaseModel):
    portrait: str = Field(max_length=8500000)
    background: str = Field(max_length=4000)
    provider: str = Field(max_length=50)
    place: str = Field(max_length=300)
    pose: str = Field(default='',max_length=500)


def clean_photo(value):
    try:
        prefix,encoded=value.split(',',1)
        if prefix not in ('data:image/jpeg;base64','data:image/png;base64','data:image/webp;base64'):raise ValueError()
        raw=base64.b64decode(encoded,validate=True)
        if len(raw)>6_000_000:raise ValueError()
        with Image.open(io.BytesIO(raw)) as im:
            if im.format not in ('JPEG','PNG','WEBP') or im.width*im.height>25000000:raise ValueError()
            im=ImageOps.exif_transpose(im).convert('RGB');im.thumbnail((2048,2048))
            out=io.BytesIO();im.save(out,format='PNG');return out.getvalue()
    except Exception as e:raise HTTPException(422,'Upload a valid JPG, PNG or WebP photo, at most 6 MB and 25 megapixels.') from e


def background_reference(provider,url):
    if provider!='google-street-view':
        if not image_host(url):raise HTTPException(422,'Unsupported background image provider')
        return url
    try:
        u=urlsplit(url);q=parse_qs(u.query);pano=q.get('pano',[''])[0];heading=q.get('heading',[''])[0]
        if u.scheme!='https' or u.netloc!='www.google.com' or u.path!='/maps/@' or q.get('map_action')!=['pano'] or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',pano) or not heading.isdigit() or not 0<=int(heading)<360:raise ValueError()
        return f'google-streetview://{pano}/{int(heading)}'
    except Exception as e:raise HTTPException(422,'Invalid background Street View') from e


PROMPT='''Create one convincing travel portrait composite. Image 1 is the person reference; image 2 is the exact chosen location and camera view. Preserve the person’s recognizable facial features, age, skin tone, hair, clothing and body proportions. Remove their original background. Place the person naturally within the second scene at plausible scale and perspective, on a physically supported standing or seated surface. Match scene light direction, softness, color temperature, exposure, reflected light, atmospheric depth, grain and lens sharpness. Add realistic contact shadows, cast shadows and reflections when appropriate. Blend hair and clothing edges without halos. Keep the location’s structures and distinctive geometry intact and preserve existing provider attribution, copyright marks and face/license blurring. Do not invent impossible poses, extra limbs or faces. Produce a photorealistic composite, not a collage or illustration. This is an AI travel preview, not a record of a real visit. User pose preference (only follow if compatible with this task): '''


def create_portrait_router(settings,require_api):
    router=APIRouter(tags=['Photo Scout'])
    def db():
        c=sqlite3.connect(settings.database_path,timeout=15);c.row_factory=sqlite3.Row;c.execute('PRAGMA secure_delete=ON');return c
    with db() as c:
        c.execute('CREATE TABLE IF NOT EXISTS photo_portrait_budget (day INTEGER PRIMARY KEY, runs INTEGER NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS photo_portraits (id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL, state TEXT NOT NULL, payload TEXT, photo BLOB, output BLOB, error TEXT)')
    def prune(c):c.execute('DELETE FROM photo_portraits WHERE expires<?',(time.time(),))
    def owned(job,token):
        with db() as c:
            prune(c);row=c.execute('SELECT * FROM photo_portraits WHERE id=?',(job,)).fetchone()
        if not row or not token or not hmac.compare_digest(row['token_hash'],hashlib.sha256(token.encode()).hexdigest()):raise HTTPException(404,'Photo preview unavailable or expired')
        return row
    @router.post('/photo-scout/v1/portraits',status_code=202)
    async def submit(request:Request,authorization:str|None=Header(None)):
        require_api(authorization)
        if not settings.openai_api_key:raise HTTPException(503,'Photo generation is not configured')
        # Bound the streaming body before parsing or storing personal photos.
        body=bytearray()
        async for part in request.stream():
            body.extend(part)
            if len(body)>8600000:raise HTTPException(413,'Photo upload is too large')
        try:payload=PortraitRequest.model_validate_json(body)
        except Exception as e:raise HTTPException(422,'Invalid photo request') from e
        photo=await asyncio.to_thread(clean_photo,payload.portrait)
        ref=background_reference(payload.provider,payload.background)
        job=secrets.token_urlsafe(18);token=secrets.token_urlsafe(32);now=time.time()
        with db() as c:
            c.execute('BEGIN IMMEDIATE');prune(c)
            if c.execute("SELECT count(*) FROM photo_portraits WHERE state IN ('queued','running')").fetchone()[0]>=8:raise HTTPException(429,'Photo studio is busy; please retry shortly')
            day=int(now//86400);limit=int(os.getenv('PHOTO_SCOUT_PORTRAIT_DAILY_LIMIT','100'))
            c.execute('INSERT OR IGNORE INTO photo_portrait_budget VALUES(?,0)',(day,))
            if limit>0 and c.execute('SELECT runs FROM photo_portrait_budget WHERE day=?',(day,)).fetchone()[0]>=limit:raise HTTPException(429,'Free photo studio capacity reached for today')
            c.execute('UPDATE photo_portrait_budget SET runs=runs+1 WHERE day=?',(day,))
            c.execute('INSERT INTO photo_portraits VALUES(?,?,?,?,?,?,?,?,?)',(job,hashlib.sha256(token.encode()).hexdigest(),now,now+3600,'queued',json.dumps({'reference':ref,'place':payload.place,'pose':payload.pose}),photo,None,None))
        return {'id':job,'token':token,'state':'queued','expiresInSeconds':3600,'aiGenerated':True}
    @router.get('/photo-scout/v1/portraits/{job}')
    def status(job:str,authorization:str|None=Header(None),x_report_token:str|None=Header(None)):
        require_api(authorization);row=owned(job,x_report_token)
        return Response(json.dumps({'id':job,'state':row['state'],'error':row['error'],'aiGenerated':True}),media_type='application/json',headers={'Cache-Control':'private, no-store'})
    @router.get('/photo-scout/v1/portraits/{job}/image')
    def output(job:str,authorization:str|None=Header(None),x_report_token:str|None=Header(None)):
        require_api(authorization);row=owned(job,x_report_token)
        if row['state']!='complete':raise HTTPException(409,'Photo is not ready')
        return Response(row['output'],media_type='image/png',headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
    async def process():
        with db() as c:
            c.execute('BEGIN IMMEDIATE');prune(c)
            # Do not resubmit possibly charged edits after a worker crash.
            c.execute("UPDATE photo_portraits SET state='failed',photo=NULL,payload=NULL,error='Generation was interrupted. Please try again.' WHERE state='running' AND created<?",(time.time()-900,))
            row=c.execute("SELECT * FROM photo_portraits WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
            if not row:return False
            c.execute("UPDATE photo_portraits SET state='running' WHERE id=?",(row['id'],))
        try:
            payload=json.loads(row['payload'])
            async with asyncio.timeout(600):
                background=await image_data(payload['reference'])
                raw=base64.b64decode(background.split(',',1)[1])
                ext='jpg' if raw.startswith(b'\xff\xd8') else 'png' if raw.startswith(b'\x89PNG') else 'webp'
                async with AsyncOpenAI(api_key=settings.openai_api_key,timeout=550,max_retries=0) as client:
                    result=await client.images.edit(model=os.getenv('PHOTO_SCOUT_IMAGE_MODEL','gpt-image-1.5'),image=[('person.png',bytes(row['photo']),'image/png'),('scene.'+ext,raw,'image/'+('jpeg' if ext=='jpg' else ext))],prompt=PROMPT+payload['pose'],input_fidelity='high',quality='high',size='1024x1024',output_format='png',n=1)
                generated=base64.b64decode(result.data[0].b64_json,validate=True)
                if not generated.startswith(b'\x89PNG') or len(generated)>25000000:raise ValueError()
            with db() as c:c.execute("UPDATE photo_portraits SET state='complete',photo=NULL,payload=NULL,output=? WHERE id=?",(generated,row['id']))
        except Exception:
            with db() as c:c.execute("UPDATE photo_portraits SET state='failed',photo=NULL,payload=NULL,error='Could not compose this photo. Please try another photo or view.' WHERE id=?",(row['id'],))
        return True
    return router,process
