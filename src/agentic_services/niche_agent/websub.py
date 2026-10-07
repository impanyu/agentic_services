"""WebSub subscription registration, verified leases, renewal and signed wakeups.
Only feed URLs and hubs explicitly approved in the operator registry are used.
"""
import hashlib
import hmac
import os
import re
import time

from fastapi import APIRouter, HTTPException, Request, Response

from .sources import feeds, request as source_request


def init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS niche_websub (
        source TEXT PRIMARY KEY, topic TEXT NOT NULL, hub TEXT NOT NULL, status TEXT NOT NULL,
        lease_expires REAL NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
        secret_env TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 1)''')


def capability(row):
    secret=os.getenv(row['secret_env'],'')
    if not secret or not row['secret_env'].startswith('NICHE_CALLBACK_'):
        raise ValueError('WebSub callback secret is not configured')
    return hmac.new(secret.encode(),f"websub:{row['source']}:{row['generation']}".encode(),hashlib.sha256).hexdigest()


def public_status(store):
    with store.connect() as db:
        init(db)
        return [{k:r[k] for k in ('source','topic','hub','status','lease_expires','next_attempt')} for r in db.execute('SELECT * FROM niche_websub')]


async def maintain(store):
    definitions=feeds()
    with store.connect() as db:
        init(db)
        for source,f in definitions.items():
            if not f.get('hub') or not f.get('enabled'): continue
            # Hub is supplied by the operator after checking the feed's advertised hub.
            hub=f['hub']; topic=f['url']; reference=f.get('secretEnv','NICHE_CALLBACK_INTERNAL')
            if not re.fullmatch('NICHE_CALLBACK_[A-Z0-9_]+',reference) or not os.getenv(reference): continue
            db.execute('INSERT OR IGNORE INTO niche_websub(source,topic,hub,status,secret_env) VALUES(?,?,?,\'pending\',?)',(source,topic,hub,reference))
        rows=[dict(r) for r in db.execute('SELECT * FROM niche_websub')]
    for row in rows:
        f=definitions.get(row['source'])
        changed=bool(f and (f.get('hub')!=row['hub'] or f['url']!=row['topic']))
        if changed and (row['status']=='unsubscribed' or (row['lease_expires']>0 and row['lease_expires']<time.time())):
            with store.connect() as db:
                db.execute("UPDATE niche_websub SET topic=?,hub=?,generation=generation+1,status='pending',lease_expires=0,next_attempt=0 WHERE source=?",(f['url'],f['hub'],row['source']))
            continue
        enabled=bool(f and f.get('enabled') and f.get('hub')==row['hub'] and f['url']==row['topic'])
        if enabled and row['next_attempt']>time.time(): continue
        if enabled and row['lease_expires']>time.time()+3600: continue
        if not enabled and row['status']=='unsubscribed': continue
        if not enabled and row['status'] in {'unsubscribing','registration_error'} and row['next_attempt']>time.time(): continue
        mode='subscribe' if enabled else 'unsubscribe'
        callback='https://api.aisoup.net/niche-discovery/v1/websub/'+row['source'].removeprefix('rss:')+'/'+capability(row)
        with store.connect() as db:
            db.execute('UPDATE niche_websub SET status=?,next_attempt=? WHERE source=?',('pending' if enabled else 'unsubscribing',time.time()+3600,row['source']))
        try:
            response=await source_request(store,'websub:'+row['source'],row['hub'],method='POST',data={
                'hub.mode':mode,'hub.topic':row['topic'],'hub.callback':callback,
                'hub.secret':os.environ[row['secret_env']],'hub.lease_seconds':'86400'})
            # 202 only means pending. The verification GET is authoritative.
            if response is None or response.status_code!=202:
                raise ValueError('Hub did not accept pending registration')
        except Exception as error:
            with store.connect() as db:
                db.execute("UPDATE niche_websub SET status='registration_error' WHERE source=? AND status IN ('pending','unsubscribing')",(row['source'],))
            store.record_collection_run('websub:'+row['source'],'error',{'errorType':type(error).__name__})


def create_websub_router(settings):
    from .store import ManagerStore
    store=ManagerStore(settings.database_path)
    router=APIRouter(prefix='/niche-discovery/v1',include_in_schema=False)

    def lookup(identifier,token):
        with store.connect() as db:
            init(db); row=db.execute('SELECT * FROM niche_websub WHERE source=?',('rss:'+identifier,)).fetchone()
        if not row:
            raise HTTPException(404,'Subscription not found')
        row=dict(row)
        try: valid=hmac.compare_digest(capability(row),token)
        except ValueError: valid=False
        if not valid: raise HTTPException(404,'Subscription not found')
        return row

    @router.get('/websub/{identifier}/{token}')
    def verify(identifier: str, token: str, request: Request):
        row=lookup(identifier,token); q=request.query_params
        mode=q.get('hub.mode'); challenge=q.get('hub.challenge','')
        expected='unsubscribe' if row['status']=='unsubscribing' else 'subscribe'
        f=feeds().get(row['source'])
        if mode!=expected or q.get('hub.topic')!=row['topic'] or not re.fullmatch('[A-Za-z0-9_+=-]{1,256}',challenge):
            raise HTTPException(404,'Verification intent does not match')
        if mode=='subscribe' and (not f or not f.get('enabled') or f.get('hub')!=row['hub'] or f['url']!=row['topic']):
            raise HTTPException(404,'Source is disabled')
        try: lease=int(q.get('hub.lease_seconds','0'))
        except ValueError: raise HTTPException(422,'Invalid lease') from None
        if mode=='subscribe' and not 3600<=lease<=864000:
            raise HTTPException(422,'Unsupported lease duration')
        with store.connect() as db:
            db.execute('UPDATE niche_websub SET status=?,lease_expires=?,next_attempt=? WHERE source=?',
                ('active' if mode=='subscribe' else 'unsubscribed',time.time()+lease if mode=='subscribe' else 0,time.time()+max(60,lease-3600),row['source']))
        return Response(challenge,media_type='application/octet-stream',headers={'X-Content-Type-Options':'nosniff','Cache-Control':'no-store'})

    @router.post('/websub/{identifier}/{token}',status_code=202)
    async def delivery(identifier: str, token: str, request: Request):
        row=lookup(identifier,token); f=feeds().get(row['source'])
        if not f or not f.get('enabled') or row['status']!='active' or row['lease_expires']<=time.time():
            raise HTTPException(404,'Subscription is not active')
        body=bytearray()
        async for part in request.stream():
            body.extend(part)
            if len(body)>2*1024*1024: raise HTTPException(413,'Delivery exceeds 2 MiB')
        method,_,signature=request.headers.get('x-hub-signature','').partition('=')
        if method not in {'sha1','sha256','sha384','sha512'}:
            raise HTTPException(401,'Supported signed delivery required')
        expected=hmac.new(os.environ[row['secret_env']].encode(),bytes(body),getattr(hashlib,method)).hexdigest()
        if not hmac.compare_digest(expected,signature): raise HTTPException(401,'Invalid delivery signature')
        # A signed push is a wake-up hint. Re-fetch canonical source through adapter.
        digest=hashlib.sha256(body).hexdigest()
        with store.connect() as db:
            # Drop per-query polling cache so the push can fetch fresh metadata.
            db.execute('UPDATE niche_source_state SET next_allowed=0 WHERE key LIKE ?',(row['source']+':%',))
        event=store.enqueue('source.callback',{'source':row['source'],'subscription':'websub:'+identifier,'delivery':digest},'websub:'+identifier+':'+digest)
        return {'accepted':True,'eventId':event}
    return router
