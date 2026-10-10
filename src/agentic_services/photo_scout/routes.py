from __future__ import annotations

import asyncio
import base64
from urllib.parse import urlencode, urlsplit, parse_qs
import hashlib
import hmac
import json
import logging
import traceback
import os
import re
import secrets
import sqlite3
import time
from typing import Literal

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field, model_validator, ValidationError

from .tasks import TaskStore, SEARCH_RETENTION, prune_records
from .styles import PHOTO_STYLES, mapped_categories, style_briefs
from .scoring import explore, assess_images, rank_assessments
from .geography import GeographicKind, fetch_region, filter_places
from .search import branch_contexts, filter_branch_images, branch_parameters
from .program import SearchProgram, CONTRACTS, PipelineHooks, execute_program
from .conditions import SearchBranch,validate_branch_scope
from .osm_features import OSMFeatureQuery,fetch_features
from .intent import IntentRequest, PoiQuery, resolve_intent
from .places import nearby_places
from .search import SearchParameters, SearchProviders, SearchUnavailable, compile_search, search_locations
from .sources import candidates, nearby_pois, google_enabled, google_image_data, MAX_SCORED_IMAGES


class ThumbnailRequest(BaseModel):
    sourceUrls: list[str] = Field(max_length=24)


DEFAULT_PHOTO_PREFERENCES = 'Scenic, distinctive public places for photography'


class ExploreRequest(BaseModel):
    searchProgram: SearchProgram | None = None
    searchBranches: list[SearchBranch] = Field(default_factory=list,max_length=6)
    query: str = Field(default="",max_length=1000)
    lat: float = Field(ge=-85,le=85,allow_inf_nan=False)
    lon: float = Field(ge=-180,le=180,allow_inf_nan=False)
    radius: int = Field(default=1000,ge=100,le=20000)
    limit: int = Field(default=3,ge=1,le=5,deprecated=True,description='Legacy compatibility field; ignored. All verified matching results are returned.')
    photoStyles: list[Literal['nature','urban','vintage','iconic','artistic','waterside','minimal','adventure']] | None = Field(default=None,min_length=1,max_length=8)
    categories: list[Literal['viewpoint','park','attraction','museum','artwork','historic','nature','recreation','cafe','restaurant','bar','shop']] | None = Field(default=None,min_length=1,max_length=8)
    poiQueries: list[PoiQuery] = Field(default_factory=list,max_length=4)
    geographicCombination: Literal['all','any'] = 'all'
    featureCombination: Literal['all','any'] = 'all'
    osmFeatures: list[OSMFeatureQuery] = Field(default_factory=list,max_length=6)
    geographicKinds: list[GeographicKind] = Field(default_factory=list,max_length=6)
    scoringIntent: str = Field(default="",max_length=1000)
    selectedPoiIds: list[str] | None = Field(default=None,max_length=50)
    poiCatalogToken: str | None = Field(default=None,max_length=40000)
    preferences: str = Field(default=DEFAULT_PHOTO_PREFERENCES,max_length=500)

    @model_validator(mode='after')
    def validate_style_filters(self):
        if self.photoStyles is not None and self.categories is not None:
            raise ValueError('Use photoStyles or categories, not both')
        return validate_branch_scope(self)

    def poi_categories(self):
        return mapped_categories(self.photoStyles) if self.photoStyles is not None else self.categories


class SearchTaskRequest(ExploreRequest):
    query: str = Field(default='',max_length=1000)


def current_website_task(payload: SearchTaskRequest) -> SearchTaskRequest:
    # There is no editable preferences field in the website. Cached clients may
    # still submit a previous report's hidden value, even with an empty query.
    # A signed catalog instead carries preferences resolved for that catalog.
    if payload.selectedPoiIds is None:
        return payload.model_copy(update={'preferences': DEFAULT_PHOTO_PREFERENCES})
    return payload


class PhotoStore:
    def __init__(self,path):
        self.path=path
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS photo_scout_intent_budget (day TEXT PRIMARY KEY, runs INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS photo_scout_budget (day TEXT PRIMARY KEY, runs INTEGER NOT NULL)')
            db.execute('''CREATE TABLE IF NOT EXISTS photo_scout_jobs (
                id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, payload TEXT NOT NULL,
                created REAL NOT NULL, session TEXT UNIQUE, price INTEGER NOT NULL,
                state TEXT NOT NULL DEFAULT 'unpaid', result TEXT, error TEXT)''')
            columns={row[1] for row in db.execute('PRAGMA table_info(photo_scout_jobs)')}
            for name,definition in [('kind',"TEXT NOT NULL DEFAULT 'paid'"),('lease_until','REAL NOT NULL DEFAULT 0'),('attempts','INTEGER NOT NULL DEFAULT 0')]:
                if name not in columns: db.execute(f'ALTER TABLE photo_scout_jobs ADD COLUMN {name} {definition}')
    def prune(self):
        with self.connect() as db:
            prune_records(db)
    def enqueue_preview(self,payload,token,on_admit=None):
        self.prune()
        job='ps_'+hashlib.sha256(token.encode()).hexdigest()[:32]
        encoded=payload.model_dump_json()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing=db.execute('SELECT payload,kind FROM photo_scout_jobs WHERE id=?',(job,)).fetchone()
            if existing:
                if existing['kind']!='preview' or existing['payload']!=encoded:
                    raise HTTPException(409,'This request token was already used for another search')
                if on_admit:on_admit(db,job)
                return job
            count=db.execute("SELECT COUNT(*) FROM photo_scout_jobs WHERE kind='preview' AND state IN ('queued','running')").fetchone()[0]
            if count>=10: raise HTTPException(429,'The search queue is full; try again later')
            db.execute("INSERT INTO photo_scout_jobs(id,token_hash,payload,created,price,state,kind) VALUES(?,?,?,?,0,'queued','preview')",(job,hashlib.sha256(token.encode()).hexdigest(),encoded,time.time()))
            if on_admit:on_admit(db,job)
        return job
    def claim_preview(self):
        self.prune()
        now=time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE photo_scout_jobs SET state='failed',error='Search interrupted repeatedly. Please submit a new search.' WHERE kind='preview' AND state='running' AND lease_until<? AND attempts>=2",(now,))
            row=db.execute("SELECT * FROM photo_scout_jobs WHERE kind='preview' AND (state='queued' OR (state='running' AND lease_until<?)) ORDER BY created LIMIT 1",(now,)).fetchone()
            if row:
                db.execute("UPDATE photo_scout_jobs SET state='running',lease_until=?,attempts=attempts+1 WHERE id=?",(now+720,row['id']))
        return dict(row) if row else None
    def connect(self):
        db=sqlite3.connect(self.path,timeout=15); db.row_factory=sqlite3.Row; return db
    def create(self,payload,price):
        job='ps_'+secrets.token_hex(16); token=secrets.token_urlsafe(32)
        with self.connect() as db:
            # Account searches persist; guests and paid delivery have bounded retention.
            prune_records(db)
            db.execute('INSERT INTO photo_scout_jobs(id,token_hash,payload,created,price) VALUES(?,?,?,?,?)',
                (job,hashlib.sha256(token.encode()).hexdigest(),payload.model_dump_json(),time.time(),price))
        return job,token
    def reserve_intent(self):
        from datetime import datetime,timezone
        day=datetime.now(timezone.utc).date().isoformat()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO photo_scout_intent_budget VALUES(?,0)',(day,))
            if db.execute('SELECT runs FROM photo_scout_intent_budget WHERE day=?',(day,)).fetchone()[0]>=int(os.getenv('PHOTO_SCOUT_DAILY_INTENT_LIMIT','100')):
                raise HTTPException(429,'Daily search planning capacity reached. Please try again later.')
            db.execute('UPDATE photo_scout_intent_budget SET runs=runs+1 WHERE day=?',(day,))
    def reserve_run(self):
        from datetime import datetime, timezone
        day=datetime.now(timezone.utc).date().isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO photo_scout_budget VALUES(?,0)",(day,))
            runs=db.execute("SELECT runs FROM photo_scout_budget WHERE day=?",(day,)).fetchone()[0]
            if runs>=int(os.getenv("PHOTO_SCOUT_DAILY_RUN_LIMIT","30")):
                raise HTTPException(429,"Today's photo search capacity has been reached. Please try again later.")
            db.execute("UPDATE photo_scout_budget SET runs=runs+1 WHERE day=?",(day,))
    def get(self,job):
        with self.connect() as db:
            prune_records(db)
            row=db.execute('SELECT * FROM photo_scout_jobs WHERE id=?',(job,)).fetchone()
        return dict(row) if row else None
    def update(self,job,**values):
        with self.connect() as db:
            db.execute('UPDATE photo_scout_jobs SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),job))


def create_photo_router(settings,require_api,verification_store,sign_receipt=None):
    router=APIRouter(tags=['Photo Scout']); store=PhotoStore(settings.database_path); tasks=TaskStore(settings.database_path)
    lock=asyncio.Semaphore(max(1,min(4,int(os.getenv('PHOTO_SCOUT_SEARCH_CONCURRENCY','2')))))
    source_requests=[]
    image_requests=[]
    def source_limit():
        now=time.monotonic()
        source_requests[:]=[t for t in source_requests if t>now-60]
        if len(source_requests)>=10: raise HTTPException(429,"Source lookup limit reached; try again in a minute")
        source_requests.append(now)

    def image_limit():
        now=time.monotonic()
        image_requests[:]=[t for t in image_requests if t>now-60]
        if len(image_requests)>=240: raise HTTPException(429,'Image preview limit reached; try again in a minute',headers={'Retry-After':'60'})
        image_requests.append(now)

    def free_preview():
        return os.getenv("PHOTO_SCOUT_HUMAN_FREE_PREVIEW", "0") == "1"
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
    def geographic_kinds(payload):
        if payload.searchBranches or payload.searchProgram is not None:return []
        return payload.geographicKinds or (['waterside'] if 'waterside' in (payload.photoStyles or []) else [])

    def search_parameters(payload):
        return SearchParameters.model_validate(payload.model_dump(include=set(SearchParameters.model_fields)))

    async def lookup_pois(payload,include_geometry=False):
        try:
            result=await search_locations(search_parameters(payload),database_path=settings.database_path,
                providers=SearchProviders(nearby_places,nearby_pois,fetch_region),
                poi_provider=os.getenv('PHOTO_SCOUT_POI_PROVIDER','openstreetmap'))
        except SearchUnavailable as error:
            raise HTTPException(503,str(error)) from error
        status=dict(result.status)
        if include_geometry:
            status['_features']=result.features
            if result.branch_results:status['_branchContexts']=branch_contexts(result)
            if result.program_execution is not None:status['_programExecution']=result.program_execution
        return result.places,status

    def sign_catalog(payload,pois,status):
        if not settings.service_api_key: raise HTTPException(503,'Private gateway credential is required')
        data={'lat':payload.lat,'lon':payload.lon,'radius':payload.radius,'categories':sorted(set(payload.poi_categories())) if payload.poi_categories() is not None else None,'photoStyles':sorted(set(payload.photoStyles)) if payload.photoStyles is not None else None,'poiQueries':payload.poiQueries,'osmFeatures':[q.model_dump() for q in payload.osmFeatures],'geographicKinds':geographic_kinds(payload),'searchProgram':payload.searchProgram.model_dump() if payload.searchProgram else None,'searchBranches':[b.model_dump() for b in payload.searchBranches],'geographicCombination':payload.geographicCombination,'featureCombination':payload.featureCombination,'expires':int(time.time())+3600,'pois':pois,'status':status}
        if payload.searchBranches or payload.searchProgram is not None:
            data['status']={k:v for k,v in status.items() if k not in ('searchPlan','branchSearches')}
        encoded=base64.urlsafe_b64encode(json.dumps(data,separators=(',',':')).encode()).decode().rstrip('=')
        signature=hmac.new(settings.service_api_key.encode(),('poi-catalog:'+encoded).encode(),hashlib.sha256).hexdigest()
        return encoded+'.'+signature

    async def chosen_pois(payload,allow_expired=False,include_geometry=False):
        if payload.selectedPoiIds is None:
            if payload.poiCatalogToken: raise HTTPException(422,'Select places from the supplied catalog')
            return await lookup_pois(payload,include_geometry)
        if not payload.selectedPoiIds or len(set(payload.selectedPoiIds))!=len(payload.selectedPoiIds):
            raise HTTPException(422,'Select at least one place, without duplicates')
        try:
            encoded,signature=payload.poiCatalogToken.rsplit('.',1)
            expected=hmac.new(settings.service_api_key.encode(),('poi-catalog:'+encoded).encode(),hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature,expected): raise ValueError()
            data=json.loads(base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4)))
            if not allow_expired and data['expires']<time.time(): raise ValueError()
            if any(data[k]!=getattr(payload,k) for k in ('lat','lon','radius')): raise ValueError()
            if data.get('categories')!=(sorted(set(payload.poi_categories())) if payload.poi_categories() is not None else None): raise ValueError()
            if data.get('photoStyles')!=(sorted(set(payload.photoStyles)) if payload.photoStyles is not None else None): raise ValueError()
            if data.get('poiQueries',[])!=payload.poiQueries:raise ValueError()
            if data.get('searchBranches',[])!=[b.model_dump() for b in payload.searchBranches]:raise ValueError()
            if data.get('searchProgram')!=(payload.searchProgram.model_dump() if payload.searchProgram else None):raise ValueError()
            if data.get('osmFeatures',[])!=[q.model_dump() for q in payload.osmFeatures]:raise ValueError()
            if data.get('geographicKinds',[])!=geographic_kinds(payload):raise ValueError()
            if any(data.get(k,'all')!=getattr(payload,k) for k in ('geographicCombination','featureCombination')):raise ValueError()
            wanted=set(payload.selectedPoiIds)
            pois=[p for p in data['pois'] if p['id'] in wanted]
            if len(pois)!=len(wanted): raise ValueError()
        except (AttributeError,ValueError,KeyError,TypeError):
            raise HTTPException(422,'Place selection is invalid or expired; find nearby places again')
        status={**data['status'],'count':len(pois),'catalogCount':len(data['pois'])}
        if payload.searchBranches or payload.searchProgram is not None:status['searchPlan']=compile_search(search_parameters(payload)).model_dump()
        if include_geometry and geographic_kinds(payload):
            features,_,geo_status=await fetch_region(payload.lat,payload.lon,payload.radius,geographic_kinds(payload),settings.database_path)
            if geo_status['status']!='ok':raise HTTPException(503,'Geographic search is temporarily unavailable')
            status['_features']=features
        if include_geometry and payload.searchProgram is not None:
            _,fresh_status=await lookup_pois(payload,include_geometry=True)
            execution=fresh_status['_programExecution']
            if not set(p['id'] for p in pois).issubset(execution.output.paths):
                raise HTTPException(503,'Selected places changed; search again')
            execution.output.rows=[p for p in execution.output.rows if p['id'] in {r['id'] for r in pois}]
            status['_programExecution']=execution
        if include_geometry and payload.searchBranches:
            async def context(index,branch):
                p=branch_parameters(search_parameters(payload),branch)
                (features,_,gs),(groups,fs)=await asyncio.gather(
                    fetch_region(p.lat,p.lon,p.radius,p.geographicKinds,settings.database_path),
                    fetch_features(p.lat,p.lon,p.radius,p.osmFeatures,settings.database_path))
                if (p.geographicKinds and gs['status']!='ok') or (p.osmFeatures and fs['status']!='ok'):
                    raise HTTPException(503,'Branch spatial search is temporarily unavailable')
                return {'parameters':p.model_dump(),'features':features,'featureGroups':groups,
                    'poiIds':[r['id'] for r in pois if index in r.get('searchBranchIndexes',[])]}
            status['_branchContexts']=await asyncio.gather(*(context(i,b) for i,b in enumerate(payload.searchBranches)))
        return pois,status

    def visual_exploration(payload):
        return compile_search(search_parameters(payload)).mergeStrategy=='area-imagery' and payload.selectedPoiIds is None

    async def catalog(payload,allow_expired=False):
        visual=visual_exploration(payload)
        try:pois,poi_status=await chosen_pois(payload,allow_expired,include_geometry=True)
        except HTTPException as error:
            if not visual or error.status_code!=503:raise
            pois=[];poi_status={'status':'unavailable','role':'optional-place-context'}
        point=bool(payload.searchProgram and payload.searchProgram.retrieval().steps[-1].tool=='point_imagery')
        rows,statuses=await candidates(payload.lat,payload.lon,payload.radius,pois,**({'visual_exploration':True, **({'point_only':True} if point else {}),
            **({'photo_styles':payload.photoStyles} if payload.photoStyles else {})} if visual else {}))
        features=poi_status.pop('_features',[])
        contexts=poi_status.pop('_branchContexts',[])
        execution=poi_status.pop('_programExecution',None)
        if execution is not None:
            eligible=execution.filter_images(rows)
            for name,status in statuses.items():
                status['geographicallyExcludedImages']=sum(r['provider']==name for r in rows)-sum(r['provider']==name for r in eligible)
                status['sampledImages']=sum(r['provider']==name for r in eligible)
            rows=eligible
        if contexts:
            eligible=filter_branch_images(rows,contexts)
            for name,status in statuses.items():
                status['geographicallyExcludedImages']=sum(r['provider']==name for r in rows)-sum(r['provider']==name for r in eligible)
                status['sampledImages']=sum(r['provider']==name for r in eligible)
            rows=eligible
        if geographic_kinds(payload):
            eligible=filter_places(rows,features,geographic_kinds(payload),payload.lat,payload.lon,combination=payload.geographicCombination)
            for name,status in statuses.items():
                status['geographicallyExcludedImages']=sum(r['provider']==name for r in rows)-sum(r['provider']==name for r in eligible)
                status['sampledImages']=sum(r['provider']==name for r in eligible)
            rows=eligible
            statuses['openstreetmap-geography']=poi_status.get('geographicSearch',{'status':'ok'})
        statuses['openstreetmap-features' if poi_status.get('provider')=='openstreetmap-features' else 'google-places' if os.getenv('PHOTO_SCOUT_POI_PROVIDER')=='google-places' else 'openstreetmap']=poi_status
        return rows,statuses,pois

    @router.post('/photo-scout/v1/resolve')
    async def resolve(payload: IntentRequest,response: Response,authorization: str | None=Header(None)):
        require_api(authorization); enabled(); source_limit()
        if not free_preview(): raise HTTPException(403,'Natural-language search is currently available during free website testing')
        store.reserve_intent()
        response.headers['Cache-Control']='private, no-store'
        try:
            async with asyncio.timeout(45): return await resolve_intent(settings,payload)
        except Exception as error:
            logging.getLogger(__name__).warning('Photo Scout text resolution failed: %s',type(error).__name__)
            raise HTTPException(503,'Search planning is temporarily unavailable. Please try again later.') from error

    @router.get('/photo-scout/v1/search-tools')
    def search_tools(authorization: str | None=Header(None)):
        require_api(authorization)
        return {'tools':[{'name':name,'inputTypes':inputs,'outputType':output} for name,(inputs,output) in CONTRACTS.items()],
                'programSchema':SearchProgram.model_json_schema(),
                'limits':{'steps':24,'sourceSearches':8,'candidatePlaces':50},
                'execution':'One validated program; independent steps execute concurrently.'}

    @router.post('/photo-scout/v1/search',operation_id='search_photo_locations')
    async def search_tool(payload: SearchParameters,response: Response,authorization: str | None=Header(None)):
        require_api(authorization);enabled();source_limit()
        try:
            result=await search_locations(payload,database_path=settings.database_path,
                providers=SearchProviders(nearby_places,nearby_pois,fetch_region),
                poi_provider=os.getenv('PHOTO_SCOUT_POI_PROVIDER','openstreetmap'))
        except SearchUnavailable as error:
            raise HTTPException(503,str(error)) from error
        response.headers['Cache-Control']='private, no-store'
        return {'nearbyPois':result.places,'source':result.status,'searchPlan':result.plan.model_dump(),
                'searchCounts':result.status['searchCounts'],'imageryTargets':result.imagery_targets,
                'imagerySampling':'area' if result.plan.mergeStrategy=='area-imagery' else 'poi'}

    @router.post('/photo-scout/v1/pois')
    async def list_pois(payload: ExploreRequest,authorization: str | None=Header(None)):
        require_api(authorization); source_limit()
        pois,status=await lookup_pois(payload)
        return Response(json.dumps({'nearbyPois':pois,'source':status,'poiCatalogToken':sign_catalog(payload,pois,status),
            'selectionExpiresInSeconds':3600,'photoStyles':style_briefs(payload.photoStyles),'visuallyAnalyzed':False}),media_type='application/json',headers={'Cache-Control':'private, no-store'})

    def image_links(result):
        if not result or not settings.service_api_key: return result
        for spot in result.get('spots',[])+result.get('poiResults',[]):
            ref=spot.get('streetViewReference')
            if not ref: continue
            expires=int(time.time())+1200
            sig=hmac.new(settings.service_api_key.encode(),f'{ref}|{expires}'.encode(),hashlib.sha256).hexdigest()
            spot['imageUrl']=settings.base_url+'/photo-scout/v1/street-view-image?'+urlencode({'reference':ref,'expires':expires,'signature':sig})
        return result

    @router.post('/photo-scout/v1/thumbnails')
    def thumbnails(payload:ThumbnailRequest,authorization:str|None=Header(None)):
        require_api(authorization)
        results=[]
        for url in payload.sourceUrls:
            if len(url)>4000:raise HTTPException(422,'Invalid Street View URL')
            u=urlsplit(url);q=parse_qs(u.query)
            pano=q.get('pano',[''])[0];heading=q.get('heading',[''])[0];pitch=q.get('pitch',['0'])[0]
            if u.scheme!='https' or u.netloc!='www.google.com' or u.path!='/maps/@' or q.get('map_action')!=['pano'] or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',pano) or not heading.isdigit() or not 0<=int(heading)<360 or not re.fullmatch(r'-?\d{1,2}',pitch) or not -90<=int(pitch)<=90:
                raise HTTPException(422,'Invalid Street View URL')
            try:fov=int(q.get('fov',['120'])[0])
            except ValueError:raise HTTPException(422,'Invalid field of view')
            if not 30<=fov<=120:raise HTTPException(422,'Invalid field of view')
            result=image_links({'spots':[{'streetViewReference':f'google-streetview://{pano}/{int(heading)}'+(f'/{int(pitch)}/{fov}' if fov!=120 else f'/{int(pitch)}' if int(pitch) else '')}]})
            results.append(result['spots'][0].get('imageUrl'))
        return Response(json.dumps({'imageUrls':results}),media_type='application/json',headers={'Cache-Control':'private, no-store'})

    @router.get('/photo-scout/v1/street-view-image')
    async def street_view_image(reference:str,expires:int,signature:str,authorization:str|None=Header(None)):
        require_api(authorization)
        if not settings.service_api_key: raise HTTPException(503,'Private gateway credential is required')
        now=int(time.time())
        expected=hmac.new(settings.service_api_key.encode(),f'{reference}|{expires}'.encode(),hashlib.sha256).hexdigest()
        if expires<now or expires>now+1200 or not hmac.compare_digest(signature,expected):
            raise HTTPException(403,'Image link expired or invalid; reload the report')
        image_limit()
        try:
            data=await google_image_data(reference)
            return Response(base64.b64decode(data.split(',',1)[1]),media_type='image/jpeg',
                headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
        except Exception as e: raise HTTPException(503,'Street View image is temporarily unavailable') from e

    async def interpret_search(payload,website=False):
        store.reserve_intent()
        inputs=payload.model_dump(include=set(IntentRequest.model_fields))
        inputs['photoStyles']=payload.photoStyles or []
        if website:inputs['preferences']=''
        plan=await resolve_intent(settings,IntentRequest.model_validate(inputs))
        location=plan['locations'][0]
        parsed=ExploreRequest.model_validate({**payload.model_dump(),'query':'',
            'lat':location['lat'],'lon':location['lon'],'radius':plan['radiusMeters'],
            'photoStyles':plan['photoStyles'] or None,'preferences':plan['preferences'],
            'searchProgram':plan.get('searchProgram'),'searchBranches':plan.get('searchBranches') or [],
            'poiQueries':plan.get('poiQueries') or [],'scoringIntent':plan.get('scoringIntent') or plan['preferences'],
            'geographicKinds':plan.get('geographicKinds') or [],'osmFeatures':plan.get('osmFeatures') or [],
            'geographicCombination':plan.get('geographicCombination','all'),'featureCombination':plan.get('featureCombination','all'),
            'categories':plan.get('categories'),'selectedPoiIds':None,'poiCatalogToken':None})
        return parsed,plan

    async def run(payload,allow_expired=False,task_id=None,parsed=False):
        enabled()
        async with lock, asyncio.timeout(660):
            started=time.monotonic()
            # Raw text and UI-only requests share one interpretation entry.
            # Already compiled programs/catalog selections are execution inputs.
            if not parsed and (payload.query.strip() or not (payload.searchProgram or payload.searchBranches or payload.selectedPoiIds is not None)):
                payload,_=await interpret_search(payload)
            retrieval_started=time.monotonic()
            async def before_score(images):
                nonlocal retrieval_seconds,scoring_started
                rows,statuses,pois=images.rows,images.statuses,images.places
                retrieval_seconds=time.monotonic()-retrieval_started
                if task_id:
                    context=tasks.context('search',task_id) or payload.model_dump()
                    context['nearbyPois']=pois
                    if images.area:
                        points={}
                        for row in rows:
                            key=(round(row['lat'],5),round(row['lon'],5))
                            points.setdefault(key,{'lat':row['lat'],'lon':row['lon'],'name':row.get('poi',{}).get('name') or 'Photo viewpoint'})
                        context['sampledViewLocations']=list(points.values())
                    context['stage']='scoring';tasks.update_context('search',task_id,context)
                if rows and not any(s['status']=='ok' for n,s in statuses.items() if n not in ('openstreetmap','google-places','openstreetmap-features')):
                    raise HTTPException(503,'Image sources are temporarily unavailable')
                store.reserve_run()
                scoring_started=time.monotonic()
            retrieval_seconds=0;scoring_started=retrieval_started
            if payload.searchProgram is not None and payload.searchProgram.complete:
                # A signed selection must be verified before any image/model work.
                if payload.selectedPoiIds is not None:await chosen_pois(payload,allow_expired)
                async def score(p,rows,statuses):return await assess_images(settings,p,rows,statuses)
                hooks=PipelineHooks(payload,candidates,score,rank_assessments,before_score,payload.selectedPoiIds)
                execution=await execute_program(payload.searchProgram,search_parameters(payload),
                    SearchProviders(nearby_places,nearby_pois,fetch_region),settings.database_path,
                    os.getenv('PHOTO_SCOUT_POI_PROVIDER','openstreetmap'),pipeline=hooks)
                report=execution.output
                rows,statuses,pois=report.images.rows,report.images.statuses,report.images.places
                result=report.result
                source_name='google-places' if os.getenv('PHOTO_SCOUT_POI_PROVIDER')=='google-places' else 'openstreetmap'
                statuses[source_name]={'status':'ok',**statuses.get(source_name,{}),'searchPlan':compile_search(search_parameters(payload)).model_dump(),
                    'executionTrace':execution.trace,'searchCounts':{'returnedCandidates':len(pois),
                        'programSteps':len(execution.trace),'sourceSearches':sum(s.tool.startswith('search_') for s in payload.searchProgram.steps)}}
                result['executionTrace']=execution.trace
            else:
                from .program import Images
                rows,statuses,pois=await catalog(payload,allow_expired)
                await before_score(Images(rows,statuses,pois,visual_exploration(payload)))
                result=await explore(settings,payload,rows,statuses)
            result['timings']={'retrievalSeconds':round(retrieval_seconds,3),'scoringSeconds':round(time.monotonic()-scoring_started,3),'totalSeconds':round(time.monotonic()-started,3)}
            assessed={p['poi']['id'] for p in result.get('poiResults',[]) if p.get('poi')}
            result.setdefault('poiResults',[]).extend({'poi':p,'name':p['name'],'score':None,
                'assessmentStatus':'no_verified_view','viewHeadingDegrees':None,
                'visible_evidence':'No scored image could be confidently matched to this place.',
                'uncertainty':'No verified camera direction is available.','coordinateWarning':'Candidate POI; imagery not verified.'}
                for p in pois if p['id'] not in assessed)
            result['nearbyPois']=pois
            source=statuses.get('google-places') or statuses.get('openstreetmap') or statuses.get('openstreetmap-features') or {}
            result['searchPlan']=source.get('searchPlan') or compile_search(search_parameters(payload)).model_dump()
            result['searchCounts']=source.get('searchCounts',{})
            result['discoveryMethod']='fixed-geographic-and-poi'
            result['geographicKinds']=geographic_kinds(payload)
            result['photoLocationCount']=sum(p.get('poi',{}).get('category')=='photo-location' for p in result.get('poiResults',[]))
            result['candidatePoiCount']=len(pois)
            result['poiProvider']='google-places' if os.getenv('PHOTO_SCOUT_POI_PROVIDER')=='google-places' else 'openstreetmap'
            result['photoStyles']=style_briefs(payload.photoStyles)
            return image_links(result)

    @router.get('/photo-scout/v1/status')
    def status():
        return {'serviceId':'photo-scout','enabled':os.getenv('PHOTO_SCOUT_ENABLED')=='1' and bool(settings.openai_api_key),
            'humanFreePreview':free_preview(),
            'photoStyles':[{'id':key,**value} for key,value in PHOTO_STYLES.items()],
            'humanPriceUsd':'0.00' if free_preview() else (f'{price()/100:.2f}' if price()>0 else None),
            'sources':{'wikimedia-commons':'enabled',
                'panoramax':'enabled' if os.getenv('PHOTO_SCOUT_PANORAMAX_ENABLED','1')=='1' else 'disabled',
                'mapillary':'configured' if os.getenv('PHOTO_SCOUT_MAPILLARY_TOKEN') else 'needs_token',
                'google-street-view':'enabled' if google_enabled() else 'disabled',
                'kartaview':'not_connected'},
            'googleStreetView':{'credentialConfigured':bool(os.getenv('PHOTO_SCOUT_GOOGLE_API_KEY')),
                'imageAnalysisEnabled':google_enabled(),
                'dailyImageRequestLimit':max(0,int(os.getenv('PHOTO_SCOUT_GOOGLE_DAILY_IMAGE_LIMIT','0'))) or None},
            'limits':{'radiusMeters':20000,'candidatePlaces':50,'sampledImages':MAX_SCORED_IMAGES,'inspectedImages':MAX_SCORED_IMAGES,'imagesPerBatch':max(1,min(32,int(os.getenv('PHOTO_SCOUT_SCORING_BATCH_SIZE','24')))),'parallelBatches':max(1,min(16,int(os.getenv('PHOTO_SCOUT_SCORING_CONCURRENCY','12')))),'viewsPerPanorama':8,'googleQueryLocations':50,'timeoutSeconds':660},
            'analysisMethod':'fixed-batch-scoring','discoveryMethod':'fixed-geographic-and-poi','discoveryMethods':['fixed-geographic-and-poi'],'geographicProvider':'openstreetmap','poiProviders':{'openstreetmap':'standby' if os.getenv('PHOTO_SCOUT_POI_PROVIDER')=='google-places' else 'enabled','google-places':'enabled' if os.getenv('PHOTO_SCOUT_POI_PROVIDER')=='google-places' else 'not_connected'},
            'privacy':'Coordinates/preferences are sent to imagery providers/OpenAI; paid reports retained for 30 days.'}

    @router.post('/photo-scout/v1/candidates')
    async def preview(payload: ExploreRequest,authorization: str | None=Header(None)):
        require_api(authorization)
        source_limit()
        rows,statuses,pois=await catalog(payload)
        return {'candidates':rows,'sources':statuses,'nearbyPois':pois,'discoveryMethod':'fixed-geographic-and-poi','visuallyAnalyzed':False}

    @router.post('/photo-scout/v1/discover')
    async def discover(payload: ExploreRequest,request: Request,response: Response,authorization: str | None=Header(None)):
        require_api(authorization);enabled()
        if not settings.service_api_key: raise HTTPException(503,'Private gateway credential is required')
        order=request.headers.get('X-Agentic-Order-Id','');token_hash=request.headers.get('X-Agentic-Order-Token-Hash','')
        amount=price()*10000
        if (not re.fullmatch(r'ord_[a-f0-9]{32}',order) or not re.fullmatch(r'[a-f0-9]{64}',token_hash)
            or request.headers.get('X-Agentic-Order-Amount-Microusd')!=str(amount) or amount<500000 or not sign_receipt):
            raise HTTPException(403,'Valid paid gateway order required')
        job='ps_'+order;prior=store.get(job)
        if prior and prior['state']=='complete':
            if prior['token_hash']!=token_hash or prior['payload']!=payload.model_dump_json():raise HTTPException(409,'Order already used')
            response.headers['X-Agentic-Receipt-Id']=verification_store.get_order(order)['receiptId']
            return json.loads(prior['result'])
        protocol=request.headers.get('X-Agentic-Payment-Protocol','unknown')
        verification_store.create_order(order_id=order,service_id='photo-scout',tier='discovery',price_microusd=amount,payment_protocol=protocol,
            order_token_hash=token_hash,request_hash=hashlib.sha256(payload.model_dump_json().encode()).hexdigest(),customer_key=None,customer_reference=None)
        try:
            result=await run(payload)
            from datetime import datetime,UTC
            receipt={'receiptId':'rcpt_'+secrets.token_hex(16),'orderId':order,'serviceId':'photo-scout','amountMicrousd':amount,'currency':'USD',
                'paymentProtocol':protocol,'resultSha256':hashlib.sha256(json.dumps(result,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                'issuedAt':datetime.now(UTC).isoformat(),'signatureAlgorithm':'hmac-sha256','costAccounting':'Not measured for this workflow'}
            result['commerce']={'orderId':order,'receiptId':receipt['receiptId'],'orderUrl':settings.base_url+'/v1/orders/'+order,
                'reportUrl':settings.base_url+'/photo-scout/v1/report/'+job,'reportRetentionDays':30}
            with store.connect() as db:
                db.execute("INSERT INTO photo_scout_jobs(id,token_hash,payload,created,price,state,result,kind) VALUES(?,?,?,?,?,'complete',?,'agent')",(job,token_hash,payload.model_dump_json(),time.time(),price(),json.dumps(result)))
            verification_store.complete_order(order_id=order,values={'verification_id':None,'provider_response_id':None,'model':settings.openai_model,
                'input_tokens':0,'cached_input_tokens':0,'output_tokens':0,'web_search_calls':0,'model_cost_microusd':0,'search_cost_microusd':0,'total_cost_microusd':0},receipt=receipt,signature=sign_receipt(receipt))
            response.headers['X-Agentic-Receipt-Id']=receipt['receiptId'];return result
        except Exception as e:
            verification_store.fail_order(order,'photo_discovery_failed')
            if isinstance(e,HTTPException):raise
            raise HTTPException(503,'Visual exploration failed; contact support with your order ID if charged') from e

    @router.post('/photo-scout/v1/preview')
    async def free_exploration(payload: ExploreRequest,authorization: str | None=Header(None)):
        require_api(authorization)
        if not settings.service_api_key: raise HTTPException(503,'Private gateway credential is required')
        if not free_preview(): raise HTTPException(403,'Free website testing is not enabled')
        source_limit()
        try: return await run(payload)
        except HTTPException: raise
        except Exception as e:
            logging.getLogger(__name__).warning('Photo Scout exploration failed: %s frames=%s',type(e).__name__,[(f.name,f.lineno) for f in traceback.extract_tb(e.__traceback__)[-6:]])
            raise HTTPException(503,'Visual exploration failed; please try again later') from e

    @router.post('/photo-scout/v1/jobs',status_code=202)
    async def submit_preview(payload: SearchTaskRequest,request: Request,response: Response,authorization: str | None=Header(None),x_request_token: str | None=Header(None)):
        require_api(authorization); enabled()
        if not settings.service_api_key: raise HTTPException(503,'Private gateway credential is required')
        if not free_preview(): raise HTTPException(403,'Free website testing is not enabled')
        if not x_request_token or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}',x_request_token):
            raise HTTPException(422,'A private request token of at least 32 characters is required')
        source_limit()
        payload=current_website_task(payload)
        # Validate signed place selection before durable admission, without fetching images.
        if payload.selectedPoiIds is not None: await chosen_pois(payload)
        identity=tasks.identity(request,response)
        job=store.enqueue_preview(payload,x_request_token,lambda db,job:tasks.bind_in(db,'search',job,identity,payload.model_dump()))
        response.headers['Cache-Control']='private, no-store'
        return {'jobId':job,'reportToken':x_request_token,'state':store.get(job)['state'],'expiresAt':tasks.expiry(identity),'context':tasks.context('search',job)}

    async def process_preview():
        if lock.locked(): return False
        job=store.claim_preview()
        if not job: return False
        try:
            submitted=current_website_task(SearchTaskRequest.model_validate_json(job['payload']))
            context=tasks.context('search',job['id']) or submitted.model_dump()
            if submitted.selectedPoiIds is None:
                context['stage']='planning';tasks.update_context('search',job['id'],context)
                payload,plan=await interpret_search(submitted,website=True)
                context['locationLabel']=plan['locations'][0]['label'];context['explanation']=plan['explanation']
            else:
                payload=ExploreRequest.model_validate(submitted.model_dump())
            context.update(payload.model_dump(exclude={'query'}));context['query']=submitted.query
            context['stage']='sources';tasks.update_context('search',job['id'],context)
            result=await run(payload,allow_expired=True,task_id=job['id'],parsed=True)
            store.update(job['id'],state='complete',result=json.dumps(result),error=None,lease_until=0)
        except Exception as error:
            logging.getLogger(__name__).warning('Photo Scout background search failed: %s fields=%s',type(error).__name__,[(e['loc'],e['type']) for e in error.errors(include_input=False,include_url=False)] if isinstance(error,ValidationError) else [])
            store.update(job['id'],state='failed',lease_until=0,error=str(error.detail) if isinstance(error,HTTPException) else 'Search could not be completed. Please try again later.')
        return True

    router.process_preview=process_preview

    @router.post('/photo-scout/v1/checkout')
    async def checkout(payload: ExploreRequest,authorization: str | None=Header(None)):
        require_api(authorization)
        if free_preview(): raise HTTPException(409,'Website testing is free; use the preview endpoint')
        enabled(); source_limit(); cents=price()
        if cents<50: raise HTTPException(503,'Checkout pricing has not been enabled')
        # Freeze the interpreted plan before coverage checking/payment.
        if payload.query.strip() or not (payload.searchProgram or payload.searchBranches or payload.selectedPoiIds is not None):
            payload,_=await interpret_search(payload)
        rows,statuses,pois=await catalog(payload)
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
            result=await run(ExploreRequest.model_validate_json(job['payload']),allow_expired=True)
            store.update(job['id'],state='complete',result=json.dumps(result))
        except Exception:
            store.update(job['id'],state='failed',error='Exploration failed. Retry or contact support for a refund review.')
            raise

    @router.get('/photo-scout/v1/report/{job_id}')
    async def report(job_id: str,request: Request,response: Response,x_report_token: str | None=Header(None)):
        job=store.get(job_id)
        if not job or not (hmac.compare_digest(job['token_hash'],hashlib.sha256((x_report_token or '').encode()).hexdigest()) or tasks.allowed('search',job_id,request)):
            raise HTTPException(404,'Report not found')
        response.headers['Cache-Control']='private, no-store'
        if job['kind']=='paid' and job['state'] in ('unpaid','failed'):
            session=await retrieve_paid(job['session'])
            if job['state']=='unpaid':
                with store.connect() as db:
                    db.execute("UPDATE photo_scout_jobs SET state='queued' WHERE id=? AND state='unpaid'",(job_id,))
            verification_store.enqueue_stripe_fulfillment(job['session'],'photo-scout',job_id)
        return {'jobId':job_id,'state':store.get(job_id)['state'],
            'result':image_links(json.loads(job['result'])) if job['result'] else None,'error':job['error'],'context':tasks.context('search',job_id)}

    @router.get('/photo-scout/openapi.json')
    def openapi():
        from fastapi import FastAPI
        api=FastAPI(title='Photo Scout',version='0.1.0'); api.include_router(router)
        document=api.openapi()
        document['servers']=[{'url':settings.base_url}]
        operation=document['paths']['/photo-scout/v1/discover']['post']
        operation['description']='Paid discovery: $'+f'{price()/100:.2f}'+' per call via MPP; MCP uses x402 Base USDC. No private API key is issued to agents. Results include a token-protected report and signed order receipt.'
        operation['x-payment-info']={'amount':f'{price()/100:.2f}','currency':'USD','protocols':['mpp','x402-mcp']}
        operation['responses']['402']={'description':'Payment required; use the public gateway payment challenge.'}
        # Internal credentials and gateway commerce headers are never client inputs.
        operation['parameters']=[]
        document['paths']={k:v for k,v in document['paths'].items() if k in ['/photo-scout/v1/discover','/photo-scout/v1/status','/photo-scout/v1/report/{job_id}']}
        return document

    @router.get('/photo-scout/.well-known/agent-service.json')
    def manifest():
        return {'id':'photo-scout','name':'Photo Scout','version':'0.1.0','audience':'both',
            'status':'preview','humanUrl':'https://aisoup.net/photo-scout/',
            'apiUrl':settings.base_url+'/photo-scout/v1/discover',
            'openapiUrl':settings.base_url+'/photo-scout/openapi.json',
            'mcpUrl':settings.base_url+'/photo-scout/mcp','tools':['list_photo_scout_prices','discover_photo_spots'],
            'payment':{'protocol':'mpp','mcpProtocol':'x402','network':'eip155:8453','currency':'USDC','perCallUsd':f'{price()/100:.2f}' if price()>0 else None},
            'description':'Image-grounded nearby photography locations from a bounded provider image sample.'}
    return router,retrieve_paid,fulfill
