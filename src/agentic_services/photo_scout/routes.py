from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .agent import explore
from .sources import candidates, nearby_pois


class ExploreRequest(BaseModel):
    lat: float = Field(ge=-85,le=85,allow_inf_nan=False)
    lon: float = Field(ge=-180,le=180,allow_inf_nan=False)
    radius: int = Field(default=1000,ge=100,le=5000)
    limit: int = Field(default=3,ge=1,le=5)
    preferences: str = Field(default='Scenic, distinctive public places for photography',max_length=500)


class PhotoStore:
    def __init__(self,path):
        self.path=path
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS photo_scout_budget (day TEXT PRIMARY KEY, runs INTEGER NOT NULL)')
            db.execute('''CREATE TABLE IF NOT EXISTS photo_scout_jobs (
                id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, payload TEXT NOT NULL,
                created REAL NOT NULL, session TEXT UNIQUE, price INTEGER NOT NULL,
                state TEXT NOT NULL DEFAULT 'unpaid', result TEXT, error TEXT)''')
    def connect(self):
        db=sqlite3.connect(self.path,timeout=15); db.row_factory=sqlite3.Row; return db
    def create(self,payload,price):
        job='ps_'+secrets.token_hex(16); token=secrets.token_urlsafe(32)
        with self.connect() as db:
            # Delete expired query/results; keep no indefinite location history.
            db.execute('DELETE FROM photo_scout_jobs WHERE created<?',(time.time()-30*86400,))
            db.execute('INSERT INTO photo_scout_jobs(id,token_hash,payload,created,price) VALUES(?,?,?,?,?)',
                (job,hashlib.sha256(token.encode()).hexdigest(),payload.model_dump_json(),time.time(),price))
        return job,token
    def reserve_run(self):
        from datetime import datetime, timezone
        day=datetime.now(timezone.utc).date().isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO photo_scout_budget VALUES(?,0)",(day,))
            runs=db.execute("SELECT runs FROM photo_scout_budget WHERE day=?",(day,)).fetchone()[0]
            if runs>=int(os.getenv("PHOTO_SCOUT_DAILY_RUN_LIMIT","30")):
                raise HTTPException(429,"Daily exploration limit reached; paid jobs will retry later")
            db.execute("UPDATE photo_scout_budget SET runs=runs+1 WHERE day=?",(day,))
    def get(self,job):
        with self.connect() as db:
            row=db.execute('SELECT * FROM photo_scout_jobs WHERE id=? AND created>?',(job,time.time()-30*86400)).fetchone()
        return dict(row) if row else None
    def update(self,job,**values):
        with self.connect() as db:
            db.execute('UPDATE photo_scout_jobs SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),job))


def create_photo_router(settings,require_api,verification_store):
    router=APIRouter(tags=['Photo Scout']); store=PhotoStore(settings.database_path)
    lock=asyncio.Lock()
    source_requests=[]
    def source_limit():
        now=time.monotonic()
        source_requests[:]=[t for t in source_requests if t>now-60]
        if len(source_requests)>=10: raise HTTPException(429,"Source lookup limit reached; try again in a minute")
        source_requests.append(now)

    def price():
        return int(os.getenv('PHOTO_SCOUT_PRICE_CENTS','0'))
    def enabled():
        if os.getenv('PHOTO_SCOUT_ENABLED','')!='1' or not settings.openai_api_key:
            raise HTTPException(503,'Photo Scout exploration is not enabled')
    def stripe_key():
        key=os.getenv('PHOTO_SCOUT_STRIPE_SECRET_KEY') or os.getenv('CONTRACTOR_STRIPE_SECRET_KEY','')
        if not key.startswith(('sk_live_','rk_live_','sk_test_','rk_test_')):
            raise HTTPException(503,'Stripe Checkout is not configured')
        return key
    async def stripe_request(method,path,data=None):
        async with httpx.AsyncClient(timeout=20) as client:
            try:
                r=await client.request(method,'https://api.stripe.com/v1/'+path,auth=(stripe_key(),''),data=data)
                r.raise_for_status(); return r.json()
            except httpx.HTTPError as e: raise HTTPException(503,'Stripe is temporarily unavailable') from e
    async def run(payload):
        enabled()
        if lock.locked(): raise HTTPException(429,'An exploration is in progress; try again shortly')
        async with lock, asyncio.timeout(240):
            rows,statuses=await candidates(payload.lat,payload.lon,payload.radius)
            if not any(s['status']=='ok' for s in statuses.values()):
                raise HTTPException(503,'Image sources are temporarily unavailable')
            store.reserve_run()
            pois,poi_status=await nearby_pois(payload.lat,payload.lon,payload.radius)
            result=await explore(settings,payload,rows,statuses)
            result["nearbyPois"]=pois
            result["sources"]["openstreetmap"]=poi_status
            return result

    @router.get('/photo-scout/v1/status')
    def status():
        return {'serviceId':'photo-scout','enabled':os.getenv('PHOTO_SCOUT_ENABLED')=='1' and bool(settings.openai_api_key),
            'humanPriceUsd':f'{price()/100:.2f}' if price()>0 else None,
            'sources':{'wikimedia-commons':'enabled',
                'panoramax':'enabled' if os.getenv('PHOTO_SCOUT_PANORAMAX_ENABLED','1')=='1' else 'disabled',
                'mapillary':'configured' if os.getenv('PHOTO_SCOUT_MAPILLARY_TOKEN') else 'needs_token',
                'google-street-view':'disabled_pending_appropriate_authorization',
                'kartaview':'not_connected'},
            'limits':{'radiusMeters':5000,'sampledImages':12,'inspectedImages':6,'timeoutSeconds':240},
            'privacy':'Coordinates/preferences are sent to imagery providers/OpenAI; paid reports retained for 30 days.'}

    @router.post('/photo-scout/v1/candidates')
    async def preview(payload: ExploreRequest,authorization: str | None=Header(None)):
        require_api(authorization)
        source_limit()
        rows,statuses=await candidates(payload.lat,payload.lon,payload.radius)
        return {'candidates':rows,'sources':statuses,'visuallyAnalyzed':False}

    @router.post('/photo-scout/v1/discover')
    async def discover(payload: ExploreRequest,authorization: str | None=Header(None)):
        # Gateway must validate payment before injecting the private service credential.
        require_api(authorization)
        if not settings.service_api_key: raise HTTPException(503,'Private gateway credential is required')
        try: return await run(payload)
        except HTTPException: raise
        except Exception as e: raise HTTPException(503,'Visual exploration failed; contact support if charged') from e

    @router.post('/photo-scout/v1/checkout')
    async def checkout(payload: ExploreRequest,authorization: str | None=Header(None)):
        require_api(authorization); enabled(); source_limit(); cents=price()
        if cents<50: raise HTTPException(503,'Checkout pricing has not been enabled')
        # Verify imagery coverage before accepting payment; no model calls here.
        rows,statuses=await candidates(payload.lat,payload.lon,payload.radius)
        if not rows: raise HTTPException(422,'No eligible imagery found here. Choose another location.')
        job,token=store.create(payload,cents)
        session=await stripe_request('POST','checkout/sessions',{
            'mode':'payment','payment_method_types[0]':'card',
            'line_items[0][price_data][currency]':'usd',
            'line_items[0][price_data][unit_amount]':str(cents),
            'line_items[0][price_data][product_data][name]':'Photo Scout nearby photo spot exploration',
            'line_items[0][quantity]':'1','client_reference_id':job,
            'metadata[serviceId]':'photo-scout',
            'success_url':'https://aisoup.net/photo-scout/?job='+job+'&session_id={CHECKOUT_SESSION_ID}',
            'cancel_url':'https://aisoup.net/photo-scout/'})
        sid,url=session.get('id'),session.get('url')
        if not isinstance(sid,str) or not isinstance(url,str) or not url.startswith('https://checkout.stripe.com/'):
            raise HTTPException(503,'Stripe did not return a valid Checkout session')
        store.update(job,session=sid)
        return {'checkoutUrl':url,'jobId':job,'reportToken':token}

    async def retrieve_paid(session_id):
        if not re.fullmatch(r'cs_(?:test|live)_[A-Za-z0-9]+',session_id):
            raise HTTPException(422,'Invalid Checkout session')
        session=await stripe_request('GET','checkout/sessions/'+session_id)
        job=store.get(session.get('client_reference_id',''))
        if (not job or job['session']!=session_id or session.get('id')!=session_id
            or session.get('metadata',{}).get('serviceId')!='photo-scout'
            or session.get('currency')!='usd' or session.get('amount_total')!=job['price']
            or session.get('mode')!='payment' or session.get('payment_status')!='paid'
            or session.get('livemode')!=stripe_key().startswith(('sk_live_','rk_live_'))):
            raise HTTPException(402,'Checkout does not match a paid Photo Scout report')
        return session

    async def fulfill(session):
        job=store.get(session['client_reference_id'])
        if not job: raise HTTPException(404,'Report expired')
        if job['state']=='complete': return
        # Shared durable Stripe worker serializes jobs; report reads never invoke a model.
        store.update(job['id'],state='running',error=None)
        try:
            result=await run(ExploreRequest.model_validate_json(job['payload']))
            store.update(job['id'],state='complete',result=json.dumps(result))
        except Exception:
            store.update(job['id'],state='failed',error='Exploration failed. Retry or contact support for a refund review.')
            raise

    @router.get('/photo-scout/v1/report/{job_id}')
    async def report(job_id: str,response: Response,x_report_token: str | None=Header(None)):
        job=store.get(job_id)
        if not job or not hmac.compare_digest(job['token_hash'],hashlib.sha256((x_report_token or '').encode()).hexdigest()):
            raise HTTPException(404,'Report not found')
        response.headers['Cache-Control']='private, no-store'
        if job['state'] in ('unpaid','failed'):
            session=await retrieve_paid(job['session'])
            if job['state']=='unpaid':
                with store.connect() as db:
                    db.execute("UPDATE photo_scout_jobs SET state='queued' WHERE id=? AND state='unpaid'",(job_id,))
            verification_store.enqueue_stripe_fulfillment(job['session'],'photo-scout',job_id)
        return {'jobId':job_id,'state':store.get(job_id)['state'],
            'result':json.loads(job['result']) if job['result'] else None,'error':job['error']}

    @router.get('/photo-scout/openapi.json')
    def openapi():
        from fastapi import FastAPI
        api=FastAPI(title='Photo Scout',version='0.1.0'); api.include_router(router)
        return api.openapi()

    @router.get('/photo-scout/.well-known/agent-service.json')
    def manifest():
        return {'id':'photo-scout','name':'Photo Scout','version':'0.1.0','audience':'both',
            'status':'preview','humanUrl':'https://aisoup.net/photo-scout/',
            'apiUrl':settings.base_url+'/photo-scout/v1/discover',
            'openapiUrl':settings.base_url+'/photo-scout/openapi.json',
            'payment':{'protocol':'mpp','perCallUsd':f'{price()/100:.2f}' if price()>0 else None},
            'description':'Image-grounded nearby photography locations from a bounded licensed image sample.'}
    return router,retrieve_paid,fulfill
