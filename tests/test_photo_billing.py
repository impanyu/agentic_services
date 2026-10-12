import asyncio
import hashlib
import hmac
import json
import sqlite3
import time
from types import SimpleNamespace
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from agentic_services.photo_scout import billing, costs
from agentic_services.photo_scout.tasks import TaskStore, digest

@pytest.fixture
def store(tmp_path,monkeypatch):
    monkeypatch.setenv('PHOTO_SCOUT_CREDITS_ENABLED','1')
    return billing.BillingStore(tmp_path/'db')

def reserve(store,job='s1',limit=100):
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        store.reserve_in(db,job,'search','u',{'creditLimitPoints':limit})

def test_concurrent_holds_and_duplicate_admission(store):
    store.grant('u',150,'Test','grant')
    reserve(store)
    reserve(store)
    with pytest.raises(HTTPException) as error:reserve(store,'s2')
    assert error.value.status_code==402
    with store.db() as db:assert store.balance_in(db,'u')==(150000,100000)
    assert store.wallet('other')['balancePoints']==0

def test_actual_items_unknown_usage_cap_and_idempotent_settlement(store):
    store.grant('u',100,'Test','grant');reserve(store,limit=50)
    @costs.tracked_task(store.path,'search')
    async def run():
        store.start_run('s1',costs.current.get().id)
        costs.record('places-text',quantity=2)
        costs.record('scoring','gpt-6-luna',{'input_tokens':1000,'output_tokens':200})
        costs.record('image-generation','gpt-image-2.5-sunburst',{'input_tokens':1000,'input_tokens_details':{'image_tokens':1000,'text_tokens':0},'output_tokens':10000})
        costs.record('unknown','unpriced',status='failed-usage-unknown')
    asyncio.run(run());store.settle('s1','complete');store.settle('s1','complete')
    with store.db() as db:assert store.balance_in(db,'u')==(50000,0)
    with store.db() as db:bill=json.loads(db.execute('SELECT bill FROM photo_credit_tasks').fetchone()[0])
    assert bill['points']==50
    assert sum(i['points'] for i in bill['items'])==pytest.approx(50)
    assert bill['unknownCostEvents']==1
    assert bill['items'][-1]['points']<0
    assert bill['items'][0]['points']==19.2

def test_failure_releases_full_hold_and_does_not_charge_provider_failures(store):
    store.grant('u',100,'Test','grant');reserve(store)
    @costs.tracked_task(store.path,'search')
    async def run():
        store.start_run('s1',costs.current.get().id);costs.record('places-text')
    asyncio.run(run());store.settle('s1','failed')
    with store.db() as db:assert store.balance_in(db,'u')==(100000,0)
    with store.db() as db:bill=json.loads(db.execute('SELECT bill FROM photo_credit_tasks').fetchone()[0])
    assert bill['knownProviderUsd']==.032 and bill['points']==0

def test_recovery_reconciles_completed_task_across_multiple_cost_runs(store):
    store.grant('u',100,'Test','grant');reserve(store)
    with store.db() as db:
        db.execute('CREATE TABLE photo_scout_jobs(id TEXT,state TEXT)')
        db.execute("INSERT INTO photo_scout_jobs VALUES('s1','running')")
    for _ in range(2):
        @costs.tracked_task(store.path,'search')
        async def run():
            store.start_run('s1',costs.current.get().id);costs.record('places-text')
        asyncio.run(run())
    assert store.wallet('u')['heldPoints']==100
    with store.db() as db:db.execute("UPDATE photo_scout_jobs SET state='complete'")
    assert store.wallet('u')['bills'][0]['points']==19.2
    assert store.wallet('u')['availablePoints']==80.8

def make_router(store,monkeypatch):
    monkeypatch.setenv('CONTRACTOR_STRIPE_SECRET_KEY','sk_test_fake')
    monkeypatch.setenv('PHOTO_SCOUT_BILLING_WEBHOOK_SECRET','whsec_fake')
    with store.db() as db:
        db.execute('CREATE TABLE photo_sessions(hash TEXT,user_id TEXT,user_json TEXT,csrf TEXT,expires REAL)')
        db.execute('INSERT INTO photo_sessions VALUES(?,?,?,?,?)',(digest('cookie'),'u',json.dumps({'email':'test@example.com'}),'csrf',time.time()+600))
    return billing.create_billing_router(SimpleNamespace(database_path=store.path),lambda _:None)

def add_order(store,mode='payment'):
    with store.db() as db:db.execute('''INSERT INTO photo_credit_orders(id,user_id,product,cents,points,mode,token,created,session,state,live)
      VALUES('o','u',?,1000,?,?,'token',?,'cs_test_one','created',0)''',('monthly-1200' if mode=='subscription' else 'points-1000',1200 if mode=='subscription' else 1000,mode,time.time()))

def test_paid_confirmation_owner_validation_and_duplicate_webhook(store,monkeypatch):
    router=make_router(store,monkeypatch);add_order(store)
    session={'id':'cs_test_one','mode':'payment','client_reference_id':'o','metadata':{'serviceId':'photo-scout-credits','orderId':'o'},'livemode':False,'currency':'usd','amount_total':1000,'payment_status':'unpaid','customer':'cus_a','payment_intent':'pi_a'}
    async def fake(*args,**kwargs):return session
    monkeypatch.setattr(billing,'stripe',fake)
    asyncio.run(router.fund_session('cs_test_one'));assert store.wallet('u')['balancePoints']==0
    session['payment_status']='paid'
    asyncio.run(router.fund_session('cs_test_one','u'));asyncio.run(router.fund_session('cs_test_one','u'))
    assert store.wallet('u')['balancePoints']==1000
    with pytest.raises(HTTPException):asyncio.run(router.fund_session('cs_test_one','other'))
    session['amount_total']=1
    with pytest.raises(HTTPException):asyncio.run(router.fund_session('cs_test_one','u'))

def test_monthly_renewal_paid_invoice_and_mismatch(store,monkeypatch):
    router=make_router(store,monkeypatch);add_order(store,'subscription')
    sub={'id':'sub_a','status':'active','livemode':False,'customer':'cus_a','metadata':{'serviceId':'photo-scout-credits','orderId':'o'},'items':{'data':[{'price':{'currency':'usd','unit_amount':1000,'recurring':{'interval':'month','interval_count':1}}}]}}
    async def fake(*args,**kwargs):return sub
    monkeypatch.setattr(billing,'stripe',fake)
    invoice={'id':'in_a','subscription':'sub_a','livemode':False,'currency':'usd','amount_paid':1000,'billing_reason':'subscription_create','status':'paid','customer':'cus_a','payment_intent':'pi_invoice'}
    asyncio.run(router.fund_invoice(invoice));asyncio.run(router.fund_invoice(invoice));assert store.wallet('u')['balancePoints']==1200
    invoice.update(id='in_b',billing_reason='subscription_cycle',payment_intent='pi_renewal');asyncio.run(router.fund_invoice(invoice));assert store.wallet('u')['balancePoints']==2400
    invoice.update(id='in_c',amount_paid=1)
    with pytest.raises(HTTPException):asyncio.run(router.fund_invoice(invoice))
    assert store.wallet('u')['balancePoints']==2400

def test_partial_refund_reversal_is_idempotent(store,monkeypatch):
    router=make_router(store,monkeypatch);add_order(store)
    with store.db() as db:db.execute("UPDATE photo_credit_orders SET payment_intent='pi_a'")
    store.grant('u',1000,'Payment','stripe-checkout:cs_test_one')
    with store.db() as db:db.execute("INSERT INTO photo_credit_paid_units VALUES('stripe-checkout:cs_test_one','u','o','pi_a',1000,1000)")
    refund={'id':'re_a','status':'succeeded','currency':'usd','payment_intent':'pi_a','amount':400}
    async def fake(*args,**kwargs):return refund
    monkeypatch.setattr(billing,'stripe',fake)
    asyncio.run(router.reverse_refund('re_a'));asyncio.run(router.reverse_refund('re_a'))
    assert store.wallet('u')['balancePoints']==600

def test_api_requires_identity_csrf_and_signed_webhook(store,monkeypatch):
    router=make_router(store,monkeypatch);app=FastAPI();app.include_router(router);client=TestClient(app)
    assert client.get('/photo-scout/v1/billing').json()['signInRequired']
    assert client.post('/photo-scout/v1/billing/checkout',json={'product':'points-500','requestToken':'a'*32}).status_code==401
    client.cookies.set('photo_scout_session','cookie')
    assert client.post('/photo-scout/v1/billing/checkout',json={'product':'points-500','requestToken':'a'*32}).status_code==403
    assert client.post('/photo-scout/v1/billing/webhook',content=b'{}').status_code==400
    body=json.dumps({'id':'evt_one','type':'unhandled','data':{'object':{}}}).encode();timestamp=int(time.time())
    signature=hmac.new(b'whsec_fake',str(timestamp).encode()+b'.'+body,hashlib.sha256).hexdigest()
    assert client.post('/photo-scout/v1/billing/webhook',content=body,headers={'stripe-signature':f't={timestamp},v1={signature}'}).status_code==200

def test_checkout_is_idempotent_and_server_owns_price(store,monkeypatch):
    router=make_router(store,monkeypatch);app=FastAPI();app.include_router(router);client=TestClient(app);client.cookies.set('photo_scout_session','cookie');seen=[]
    async def fake(method,path,data=None,idempotency=None):
        if method=='POST':seen.append((data,idempotency))
        return {'id':'cs_test_created','url':'https://checkout.stripe.com/test'}
    monkeypatch.setattr(billing,'stripe',fake)
    payload={'product':'points-500','requestToken':'a'*32};headers={'origin':'https://photo-scout.aisoup.net','x-csrf-token':'csrf'}
    assert client.post('/photo-scout/v1/billing/checkout',json=payload,headers=headers).status_code==200
    assert client.post('/photo-scout/v1/billing/checkout',json=payload,headers=headers).status_code==200
    assert len(seen)==1 and seen[0][0]['line_items[0][price_data][unit_amount]']=='500'
    assert client.post('/photo-scout/v1/billing/checkout',json=payload|{'product':'points-2500'},headers=headers).status_code==409
