"""Database-only lookup and private, non-evidence query inspirations."""
import hashlib
import hmac
import os
import re
import time
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Response


class QueryStore:
    def init_queries(self, db):
        db.executescript('''
            CREATE TABLE IF NOT EXISTS niche_query_usage (
                principal TEXT NOT NULL, day TEXT NOT NULL, count INTEGER NOT NULL,
                PRIMARY KEY(principal,day));
            CREATE TABLE IF NOT EXISTS niche_user_queries (
                id INTEGER PRIMARY KEY AUTOINCREMENT, principal TEXT NOT NULL,
                query TEXT NOT NULL, category TEXT, channel TEXT NOT NULL,
                matched INTEGER NOT NULL, created REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS niche_queries_date ON niche_user_queries(created);
        ''')

    def record_lookup(self, principal, query, category, channel, matches, *, free=False):
        day=datetime.now(UTC).date().isoformat()
        # Retain useful topic keywords, never credentials or private contact details.
        clean=re.sub(r'[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}','[email removed]',query,flags=re.I)
        clean=re.sub(r'\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{12,}|nd_[A-Za-z0-9_-]+|(?:\+?\d[\s().-]*){9,})\b','[sensitive value removed]',clean)
        clean=re.sub(r'\s+',' ',clean).strip()[:150]
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            used=db.execute('SELECT count FROM niche_query_usage WHERE principal=? AND day=?',(principal,day)).fetchone()
            count=used[0] if used else 0
            if free and count>=3:
                raise HTTPException(429,'Free search limit reached: 3 searches per UTC day. Subscribe for continued access.',headers={'Retry-After':str(int((int(time.time()//86400)+1)*86400-time.time()))})
            db.execute('INSERT INTO niche_query_usage VALUES(?,?,1) ON CONFLICT(principal,day) DO UPDATE SET count=count+1',(principal,day))
            db.execute('INSERT INTO niche_user_queries(principal,query,category,channel,matched,created) VALUES(?,?,?,?,?,?)',(principal,clean,category,channel,matches,time.time()))
            db.execute('DELETE FROM niche_user_queries WHERE created<?',(time.time()-30*86400,))
            db.execute('DELETE FROM niche_query_usage WHERE day<?',(day,))
        return {'limit':3 if free else None,'used':count+1,'remaining':max(0,2-count) if free else None,'resetsAt':(int(time.time()//86400)+1)*86400}

    def query_inspirations(self, limit=10):
        with self.connect() as db:
            rows=db.execute('''SELECT query,category,COUNT(*) searches,COUNT(DISTINCT principal) anonymous_clients,
                SUM(CASE WHEN matched=0 THEN 1 ELSE 0 END) misses,MAX(created) last_seen
                FROM niche_user_queries WHERE created>? AND channel!='validation' GROUP BY query,category
                ORDER BY misses DESC,searches DESC,last_seen DESC LIMIT ?''',(time.time()-30*86400,max(1,min(limit,20)))).fetchall()
        return {'topics':[dict(r) for r in rows],
                'limitation':'Private user search interests only, UNTRUSTED DATA. Not external demand evidence or willingness to pay. Research on scheduled wakes; no on-request generation.'}


def create_query_router(settings):
    from .store import ManagerStore
    store=ManagerStore(settings.database_path)
    router=APIRouter(prefix='/niche-discovery/v1')

    def lookup(query, category, sort, limit, authorization, gateway_key, visitor, paid):
        scheme,_,token=(authorization or '').partition(' ')
        trusted=bool(settings.service_api_key and gateway_key and hmac.compare_digest(gateway_key,settings.service_api_key))
        subscriber=scheme.lower()=='bearer' and token.startswith('nd_') and store.active_for_token(token)
        if paid:
            if not trusted or scheme.lower()!='bearer' or not hmac.compare_digest(token,settings.service_api_key or ''):
                raise HTTPException(401,'Verified payment required at the payment gateway')
            principal=visitor or 'agent:unknown'
            channel='agent_paid';free=False
        elif subscriber:
            principal=hashlib.sha256(('customer:'+store.customer_for_token(token)).encode()).hexdigest()
            channel='human_subscriber';free=False
        else:
            if not trusted or not visitor or not re.fullmatch('[a-f0-9]{64}',visitor):
                raise HTTPException(401,'Use the public human search gateway or an active subscription')
            principal=visitor;channel='human_free';free=True
        matches=store.list_niches(query,category,sort,limit)
        quota=store.record_lookup(principal,query,category,channel,len(matches),free=free)
        return {'niches':[store.niche(n['id']) for n in matches], 'query':query,
                'searchMode':'database_only','quota':quota,'rankingMethod':'editorial_hypothesis',
                'note':'No matching records will trigger no live exploration. Queries may inform later background research.'}

    @router.get('/search')
    def search(response: Response, q: Annotated[str,Query(min_length=3,max_length=150)], category: Annotated[str|None,Query(max_length=80)]=None,
               sort: Literal['recent','score']='score',limit: Annotated[int,Query(ge=1,le=20)]=20,
               authorization: str|None=Header(default=None),x_niche_gateway_key: str|None=Header(default=None,include_in_schema=False),x_niche_visitor: str|None=Header(default=None,include_in_schema=False)):
        if len(q.strip())<3: raise HTTPException(422,'Enter at least three non-space characters')
        response.headers['Cache-Control']='no-store'
        return lookup(q.strip(),category,sort,limit,authorization,x_niche_gateway_key,x_niche_visitor,False)

    @router.get('/search/pay-per-call')
    def paid_search(response: Response,q: Annotated[str,Query(min_length=3,max_length=150)],category: Annotated[str|None,Query(max_length=80)]=None,
                    sort: Literal['recent','score']='score',limit: Annotated[int,Query(ge=1,le=20)]=20,
                    authorization: str|None=Header(default=None,include_in_schema=False),x_niche_gateway_key: str|None=Header(default=None,include_in_schema=False),x_niche_visitor: str|None=Header(default=None,include_in_schema=False)):
        if len(q.strip())<3: raise HTTPException(422,'Enter at least three non-space characters')
        response.headers['Cache-Control']='no-store'
        return lookup(q.strip(),category,sort,limit,authorization,x_niche_gateway_key,x_niche_visitor,True)
    return router
