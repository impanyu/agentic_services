"""Bounded cross-industry metadata adapters and operator-approved RSS/Atom feeds."""
import asyncio
import hashlib
import html
import json
import os
import re
import time
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

import httpx

from ..snapshot import _validate_public_url
from ..niche_discovery import now

SE_LICENSE='https://creativecommons.org/licenses/by-sa/4.0/'
BUILTINS={
    'stack-exchange': {'flag':'NICHE_COLLECT_STACK_EXCHANGE','license':SE_LICENSE,'scope':'Configured expert-question sites; titles/tags only; not proof of payment'},
    'cpsc': {'flag':'NICHE_COLLECT_CPSC','license':'https://www.cpsc.gov/Recalls/CPSC-Recalls-Application-Program-Interface-API-Information','scope':'Recent product recalls; safety context only'},
    'federal-register': {'flag':'NICHE_COLLECT_FEDERAL_REGISTER','license':'https://www.federalregister.gov/reader-aids/developer-resources/rest-api','scope':'Recent regulatory notices; context only'},
    'nyc-311': {'flag':'NICHE_COLLECT_NYC311','license':'https://opendata.cityofnewyork.us/overview/','scope':'Structured public service requests; no addresses, names or locations'},
    'cfpb': {'flag':'NICHE_COLLECT_CFPB','license':'https://www.consumerfinance.gov/data-research/consumer-complaints/','scope':'Structured consumer complaints; no narratives or personal data'},
}


def feeds():
    definitions=json.loads(os.getenv('NICHE_SOURCE_FEEDS','[]'))
    if not isinstance(definitions,list) or len(definitions)>30:
        raise ValueError('Feed registry must contain at most 30 feeds')
    result={}
    for f in definitions:
        if not re.fullmatch('[a-z0-9-]{1,50}',f.get('id','')) or not f.get('rightsReference') or not f.get('license') or not f.get('attribution'):
            raise ValueError('Feeds require stable ID, rightsReference, license and attribution')
        parsed=urlsplit(f['url'])
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in {None,443}:
            raise ValueError('Feeds require public HTTPS URLs')
        if parsed.hostname=='reddit.com' or parsed.hostname.endswith('.reddit.com') or parsed.hostname.endswith('redd.it'):
            raise ValueError('Reddit content requires the dedicated permitted adapter')
        result['rss:'+f['id']]=f
    return result


def source_registry():
    registry={key:{'enabled':os.getenv(value['flag'])=='1', 'license':value['license'],'scope':value['scope']} for key,value in BUILTINS.items()}
    # NYC-specific reuse/retention must be reviewed before enabling its adapter.
    registry['nyc-311']['enabled'] &= bool(os.getenv('NICHE_NYC311_RIGHTS_REFERENCE'))
    registry.update({key:{'enabled':bool(f.get('enabled',False)),'license':f['license'], 'scope':f.get('scope','Licensed feed headlines only')} for key,f in feeds().items()})
    return registry


def init(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS niche_source_state (key TEXT PRIMARY KEY, day TEXT NOT NULL,
            requests INTEGER NOT NULL, next_allowed REAL NOT NULL, etag TEXT, modified TEXT, last_success REAL);
        CREATE TABLE IF NOT EXISTS niche_signal_provenance (signal_id TEXT PRIMARY KEY, metadata TEXT NOT NULL);
    ''')


async def request(store, source, url, params=None, *, method='GET', data=None):
    """Operator/fixed endpoint only, no redirects; enforce daily quotas and provider backoff."""
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.username or parsed.password or parsed.port not in {None,443}:
        raise ValueError('Public HTTPS source endpoint required')
    await asyncio.to_thread(_validate_public_url,url)
    day=datetime.now(UTC).date().isoformat()
    key=source+':'+hashlib.sha256(json.dumps([method,url,params,data],sort_keys=True).encode()).hexdigest()[:24]
    with store.connect() as db:
        init(db); db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM niche_source_state WHERE key=?',(source,)).fetchone()
        cached=db.execute('SELECT * FROM niche_source_state WHERE key=?',(key,)).fetchone()
        if row and (row['next_allowed']>time.time() or (row['day']==day and row['requests']>=80)):
            return None
        if method=='GET' and cached and cached['next_allowed']>time.time():
            return None
        db.execute('INSERT INTO niche_source_state VALUES(?,?,1,?,NULL,NULL,NULL) ON CONFLICT(key) DO UPDATE SET requests=CASE WHEN day=excluded.day THEN requests+1 ELSE 1 END,day=excluded.day,next_allowed=excluded.next_allowed',(source,day,0))
    headers={'User-Agent':'AISoup-NicheDiscovery/1.1 (+https://aisoup.net/niche-discovery/)'}
    if cached and method=='GET':
        if cached['etag']: headers['If-None-Match']=cached['etag']
        if cached['modified']: headers['If-Modified-Since']=cached['modified']
    async with httpx.AsyncClient(timeout=20,follow_redirects=False) as client:
        async with client.stream(method,url,params=params,data=data,headers=headers) as response:
            if response.status_code in {429,503}:
                try: wait=max(60,min(86400,int(response.headers.get('retry-after','3600'))))
                except ValueError: wait=3600
                with store.connect() as db: db.execute('UPDATE niche_source_state SET next_allowed=? WHERE key=?',(time.time()+wait,source))
            if response.status_code==304:
                with store.connect() as db:
                    db.execute('UPDATE niche_source_state SET next_allowed=?,last_success=? WHERE key=?',(time.time()+900,time.time(),key))
                return None
            response.raise_for_status()
            raw=bytearray()
            async for part in response.aiter_bytes():
                raw.extend(part)
                if len(raw)>2*1024*1024: raise ValueError('Source response exceeds 2 MiB')
            response._content=bytes(raw)
    with store.connect() as db:
        db.execute('INSERT INTO niche_source_state VALUES(?,?,0,?,?,?,?) ON CONFLICT(key) DO UPDATE SET next_allowed=excluded.next_allowed,etag=excluded.etag,modified=excluded.modified,last_success=excluded.last_success',
            (key,day,time.time()+900,response.headers.get('etag'),response.headers.get('last-modified'),time.time()))
        db.execute('UPDATE niche_source_state SET last_success=? WHERE key=?',(time.time(),source))
    return response


def record(store, source, identifier, text, link, date, kind, metadata):
    try:
        parsed_date=datetime.fromisoformat(str(date).replace('Z','+00:00'))
        date=parsed_date.replace(tzinfo=UTC).isoformat() if parsed_date.tzinfo is None else parsed_date.astimezone(UTC).isoformat()
    except (ValueError,TypeError):
        return False
    parsed=urlsplit(link)
    if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password:
        return False
    metadata={k:v for k,v in metadata.items() if k!='query'}
    if metadata.get('authorUrl') and urlsplit(metadata['authorUrl']).scheme not in {'http','https'}: metadata.pop('authorUrl')
    external=source+':'+str(identifier)
    added=store.ingest_external_signal(external_id=external,origin=source,kind=kind,text=text,source_url=link,observed_at=date)
    with store.connect() as db:
        init(db)
        row=db.execute('SELECT id FROM niche_signals WHERE external_id=?',(external,)).fetchone()
        if row:
            db.execute('INSERT OR REPLACE INTO niche_signal_provenance VALUES(?,?)',(row[0],json.dumps({'platform':source,'sourceId':str(identifier),'collectedAt':now(),**metadata})))
    return added


def feed_records(raw, definition):
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('XML entities and DTDs are not supported')
    root=ET.fromstring(raw)
    atom='{http://www.w3.org/2005/Atom}'
    entries=root.findall('.//item') if root.tag!=atom+'feed' else root.findall(atom+'entry')
    records=[]
    for item in entries[:40]:
        def text(tag):
            return ''.join(item.findtext(tag,default='')).strip()
        title=html.unescape(text('title') or text(atom+'title'))
        link=text('link')
        if not link:
            link=next((e.get('href','') for e in item.findall(atom+'link') if e.get('rel','alternate')=='alternate'),'')
        identifier=text('guid') or text(atom+'id') or link
        author=text('{http://purl.org/dc/elements/1.1/}creator') or text(atom+'author/'+atom+'name') or definition['attribution']
        date=text('pubDate') or text(atom+'published') or text(atom+'updated')
        try:
            date=parsedate_to_datetime(date).isoformat()
        except (ValueError,TypeError): pass
        records.append((identifier,title,link,date,author))
    return records


async def collect_registered(store, source, query=''):
    registry=source_registry()
    if source not in registry or not registry[source]['enabled']:
        return {'status':'disabled','source':source,'signalsAdded':0}
    if len(query)>200: raise ValueError('Search keywords exceed 200 characters')
    added=0; examined=0
    try:
        if source=='stack-exchange':
            sites=[x.strip() for x in os.getenv('NICHE_STACK_EXCHANGE_SITES','diy,gardening,money,travel,bicycles,cooking').split(',') if re.fullmatch('[a-z.]+',x.strip())][:8]
            for site in sites:
                params={'site':site,'pagesize':15,'sort':'creation','order':'desc'}
                if query: params['q']=query
                r=await request(store,source,'https://api.stackexchange.com/2.3/'+('search/advanced' if query else 'questions'),params)
                if r is None: continue
                body=r.json()
                if body.get('backoff'):
                    with store.connect() as db: db.execute('UPDATE niche_source_state SET next_allowed=? WHERE key=?',(time.time()+int(body['backoff']),source))
                for q in body.get('items',[]):
                    examined+=1
                    license=q.get('content_license','')
                    if license!='CC BY-SA 4.0': continue
                    owner=q.get('owner',{})
                    added+=record(store,source,site+':'+str(q['question_id']),html.unescape(q['title']),q['link'],datetime.fromtimestamp(q['creation_date'],UTC).isoformat(),'question',
                        {'scope':site,'query':query,'license':SE_LICENSE,'attribution':'Stack Exchange — '+owner.get('display_name','anonymous'),'authorUrl':owner.get('link'),'excerptChanges':'Headline only; whitespace normalized; emails redacted','limitations':'Question does not demonstrate willingness to pay'})
        elif source=='federal-register':
            params={'per_page':30,'order':'newest'}
            if query: params['conditions[term]']=query
            r=await request(store,source,'https://www.federalregister.gov/api/v1/documents.json',params)
            if r:
                for d in r.json().get('results',[]):
                    examined+=1
                    added+=record(store,source,d['document_number'],d['title'],d['html_url'],d['publication_date']+'T00:00:00Z','regulatory_notice',{'query':query,'license':registry[source]['license'],'attribution':'Federal Register','limitations':'Regulatory context, not demonstrated demand'})
        elif source=='cpsc':
            params={'format':'json','RecallDateStart':(datetime.now(UTC)-timedelta(days=45)).date().isoformat()}
            # Official API has no general text search; filter bounded recent titles locally.
            r=await request(store,source,'https://www.saferproducts.gov/RestWebServices/Recall',params)
            if r:
                for d in r.json()[:100]:
                    examined+=1
                    if query and not any(w.lower() in d['Title'].lower() for w in query.split()): continue
                    added+=record(store,source,d['RecallID'],d['Title'],d['URL'],d['RecallDate'],'recall_context',{'query':query,'license':registry[source]['license'],'attribution':'U.S. Consumer Product Safety Commission','limitations':'Recall/safety context only; not evidence of paid demand'})
        elif source=='nyc-311':
            params={'$select':'unique_key,created_date,complaint_type,descriptor,agency,borough,status','$order':'created_date DESC','$limit':50}
            if query:
                term=query.replace("'","''")
                params['$where']=f"contains(lower(complaint_type),lower('{term}'))"
            r=await request(store,source,'https://data.cityofnewyork.us/resource/erm2-nwe9.json',params)
            if r:
                for d in r.json():
                    examined+=1
                    text=' | '.join(str(d.get(k,'')) for k in ('complaint_type','descriptor','borough','status'))
                    added+=record(store,source,d['unique_key'],text,'https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2020-to-Present/erm2-nwe9',datetime.fromisoformat(d['created_date']).replace(tzinfo=__import__('zoneinfo').ZoneInfo('America/New_York')).astimezone(UTC).isoformat(),'complaint',{'query':query,'license':registry[source]['license'],'attribution':'NYC Open Data / 311','limitations':'Bounded recent administrative sample, not representative market demand'})
        elif source=='cfpb':
            params={'size':30,'sort':'created_date_desc'}
            if query: params['search_term']=query
            r=await request(store,source,'https://www.consumerfinance.gov/data-research/consumer-complaints/search/api/v1/',params)
            if r:
                body=r.json(); hits=body.get('hits',{}).get('hits',[])
                for hit in hits:
                    d=hit.get('_source',{}); examined+=1
                    identifier=d.get('complaint_id')
                    if not identifier: continue
                    text=' | '.join(str(d.get(k,'')) for k in ('product','sub_product','issue','sub_issue','company_response'))
                    added+=record(store,source,identifier,text,'https://www.consumerfinance.gov/data-research/consumer-complaints/',str(d.get('date_received','')),'complaint',{'query':query,'license':registry[source]['license'],'attribution':'Consumer Financial Protection Bureau','limitations':'Complaint metadata only; no narratives; sample not representative'})
        else:
            f=feeds()[source]
            r=await request(store,source,f['url'])
            if r:
                for identifier,title,link,date,author in feed_records(r.content,f):
                    examined+=1
                    if query and not any(w.lower() in title.lower() for w in query.split()): continue
                    added+=record(store,source,identifier,title,link,date,'news_report',{'query':query,'scope':f['url'],'license':f['license'],'attribution':f['attribution']+' — '+author,'excerptChanges':'Headline only; whitespace normalized; emails redacted','rightsReference':f['rightsReference'],'limitations':'Headline context; not a direct complaint'})
        result={'status':'pending_review','source':source,'signalsAdded':added,'examined':examined,'query':query,'limitation':'Bounded sample; not whole-web coverage or validated paid demand'}
    except Exception as error:
        with store.connect() as db:
            init(db); db.execute('UPDATE niche_source_state SET next_allowed=? WHERE key=?',(time.time()+3600,source))
        result={'status':'error','source':source,'signalsAdded':added,'errorType':type(error).__name__}
    store.record_collection_run(source,result['status'],{k:v for k,v in result.items() if k!='query'})
    return result
