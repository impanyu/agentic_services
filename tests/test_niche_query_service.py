import hashlib
import json
import time
from fastapi import FastAPI
from fastapi.testclient import TestClient
from agentic_services.config import Settings
from agentic_services.niche_agent.store import ManagerStore
from agentic_services.niche_agent.query_service import create_query_router
from test_niche_manager import draft


def setup(tmp_path):
    settings=Settings(None,'gpt-6-sol',tmp_path/'db','https://api.aisoup.net',service_api_key='trusted-gateway')
    s=ManagerStore(settings.database_path)
    n=s.save_niche(draft(s,publish=True))
    app=FastAPI();app.include_router(create_query_router(settings));c=TestClient(app)
    headers={'X-Niche-Gateway-Key':'trusted-gateway','X-Niche-Visitor':hashlib.sha256(b'anonymous').hexdigest()}
    return c,s,n,headers


def test_three_free_queries_full_database_results_and_no_wake(tmp_path):
    c,s,n,headers=setup(tmp_path)
    path='/niche-discovery/v1/search?q=invoice'
    for used in (1,2,3):
        r=c.get(path,headers=headers);assert r.status_code==200
        d=r.json();assert d['searchMode']=='database_only' and d['quota']['used']==used
        assert d['niches'][0]['id']==n['id'] and d['niches'][0]['evidence']
        assert r.headers['cache-control']=='no-store'
    assert c.get(path,headers=headers).status_code==429
    assert s.status()['queue']=={}
    assert s.query_inspirations()['topics'][0]['searches']==3


def test_subscriber_not_limited_to_three_and_customer_token_rotation(tmp_path):
    c,s,n,headers=setup(tmp_path)
    for suffix in ('one','two'):
        s.upsert_subscription('sub-'+suffix,'same-customer',suffix+'@example.com','active',hashlib.sha256(('nd_'+suffix).encode()).hexdigest())
    for i in range(5):
        r=c.get('/niche-discovery/v1/search?q=invoice',headers={'Authorization':'Bearer nd_'+('one' if i%2 else 'two')})
        assert r.status_code==200 and r.json()['quota']['limit'] is None
    assert s.query_inspirations()['topics'][0]['anonymous_clients']==1


def test_paid_agent_cannot_spoof_auth_and_logs_no_generation(tmp_path):
    c,s,n,headers=setup(tmp_path)
    path='/niche-discovery/v1/search/pay-per-call?q=invoice'
    assert c.get(path).status_code==401
    assert c.get(path,headers={**headers,'Authorization':'Bearer fake'}).status_code==401
    r=c.get(path,headers={**headers,'Authorization':'Bearer trusted-gateway'})
    assert r.status_code==200 and r.json()['quota']['limit'] is None
    with s.connect() as db:assert db.execute('SELECT channel FROM niche_user_queries').fetchone()[0]=='agent_paid'
    assert not s.status()['queue']


def test_free_identity_bound_to_trusted_gateway_and_no_extra_quota_on_invalid_query(tmp_path):
    c,s,n,headers=setup(tmp_path)
    assert c.get('/niche-discovery/v1/search?q=invoice').status_code==401
    assert c.get('/niche-discovery/v1/search?q=invoice',headers={'X-Niche-Visitor':headers['X-Niche-Visitor']}).status_code==401
    assert c.get('/niche-discovery/v1/search?q=%20%20%20',headers=headers).status_code==422
    assert not s.query_inspirations()['topics']


def test_query_redaction_retention_and_not_evidence(tmp_path):
    c,s,n,headers=setup(tmp_path)
    before=len(s.signals())
    s.record_lookup('client','invoice email test@example.com nd_credentialsecret sk-proj-abcdef1234567890',None,'human_free',0,free=True)
    topic=s.query_inspirations()['topics'][0]['query']
    assert 'test@example.com' not in topic and 'credentialsecret' not in topic and 'abcdef1234567890' not in topic
    assert len(s.signals())==before
    with s.connect() as db:db.execute('UPDATE niche_user_queries SET created=0')
    s.record_lookup('client','fresh query',None,'human_free',0,free=True)
    assert len(s.query_inspirations()['topics'])==1
