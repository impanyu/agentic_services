"""Account credit ledger, cost-based task bills, and verified Stripe funding.

Money is never inferred from a redirect. Funding requires a provider-confirmed
payment, and every grant/debit has a unique durable reference. Milli-points keep
small API calls visible without rounding each call to a whole point.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from decimal import Decimal, ROUND_CEILING

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .tasks import TaskStore, ACCOUNT_COOKIE, digest
from .apple_billing import ready as apple_ready
from .web_origin import allowed_web_origin, CANONICAL_ORIGIN
from ..stripe_webhook import verify_stripe_event, InvalidStripeSignature

POINTS_PER_USD = 100
MULTIPLIER = Decimal('3')
VERSION = '2026-10-v1'
CATALOG = {
    'points-500': {'mode':'payment', 'cents':500, 'points':500, 'name':'500 Photo Scout points'},
    'points-1000': {'mode':'payment', 'cents':1000, 'points':1000, 'name':'1,000 Photo Scout points'},
    'points-2500': {'mode':'payment', 'cents':2500, 'points':2500, 'name':'2,500 Photo Scout points'},
    'monthly-1200': {'mode':'subscription', 'cents':1000, 'points':1200, 'name':'Photo Scout Starter · 1,200 points / month'},
    'monthly-3250': {'mode':'subscription', 'cents':2500, 'points':3250, 'name':'Photo Scout Plus · 3,250 points / month'},
    'monthly-7000': {'mode':'subscription', 'cents':5000, 'points':7000, 'name':'Photo Scout Studio · 7,000 points / month'},
}


def enabled():
    return os.getenv('PHOTO_SCOUT_CREDITS_ENABLED', '0') == '1'


def stripe_key():
    value = os.getenv('PHOTO_SCOUT_STRIPE_SECRET_KEY') or os.getenv('CONTRACTOR_STRIPE_SECRET_KEY', '')
    if not value.startswith(('sk_live_', 'rk_live_', 'sk_test_', 'rk_test_')):
        raise HTTPException(503, 'Point purchases are not configured yet')
    return value


def payments_ready():
    try:
        stripe_key()
        return os.getenv('PHOTO_SCOUT_BILLING_WEBHOOK_SECRET', '').startswith('whsec_')
    except HTTPException:
        return False


async def stripe(method, path, data=None, idempotency=None):
    headers = {'Idempotency-Key': idempotency} if idempotency else {}
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.request(method, 'https://api.stripe.com/v1/'+path,
                auth=(stripe_key(), ''), data=data, headers=headers)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as error:
        raise HTTPException(503, 'Payment provider is temporarily unavailable. Please retry.') from error


class BillingStore:
    def __init__(self, path):
        self.path = path
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS photo_credit_ledger (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, milli_points INTEGER NOT NULL,
                reason TEXT NOT NULL, reference TEXT UNIQUE NOT NULL, created REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS photo_credit_user ON photo_credit_ledger(user_id,created);
            CREATE TABLE IF NOT EXISTS photo_credit_tasks (
                job TEXT PRIMARY KEY, kind TEXT NOT NULL, user_id TEXT NOT NULL,
                held INTEGER NOT NULL, state TEXT NOT NULL, created REAL NOT NULL,
                settled REAL, charged INTEGER, bill TEXT, pricing TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS photo_credit_pending ON photo_credit_tasks(state);
            CREATE TABLE IF NOT EXISTS photo_credit_runs (job TEXT NOT NULL, run TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS photo_credit_orders (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, product TEXT NOT NULL,
                cents INTEGER NOT NULL, points INTEGER NOT NULL, mode TEXT NOT NULL,
                token TEXT NOT NULL, created REAL NOT NULL, session TEXT UNIQUE,
                customer TEXT, subscription TEXT UNIQUE, state TEXT NOT NULL,
                live INTEGER NOT NULL, UNIQUE(user_id,token));
            CREATE TABLE IF NOT EXISTS photo_credit_paid_units (reference TEXT PRIMARY KEY,user_id TEXT NOT NULL,order_id TEXT NOT NULL,intent TEXT UNIQUE,cents INTEGER NOT NULL,points INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS photo_credit_refunds (id TEXT PRIMARY KEY, order_id TEXT NOT NULL, cents INTEGER NOT NULL);
            ''')
            columns={r[1] for r in db.execute('PRAGMA table_info(photo_credit_orders)')}
            if 'payment_intent' not in columns:db.execute('ALTER TABLE photo_credit_orders ADD COLUMN payment_intent TEXT')

    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def balance_in(db, user):
        total = db.execute('SELECT coalesce(sum(milli_points),0) FROM photo_credit_ledger WHERE user_id=?',(user,)).fetchone()[0]
        held = db.execute("SELECT coalesce(sum(held),0) FROM photo_credit_tasks WHERE user_id=? AND state='pending'",(user,)).fetchone()[0]
        return total, held

    @staticmethod
    def grant_in(db, user, points, reason, reference):
        db.execute('INSERT OR IGNORE INTO photo_credit_ledger VALUES(?,?,?,?,?,?)',
            (secrets.token_hex(16), user, points*1000, reason, reference, time.time()))

    def grant(self, user, points, reason, reference):
        if not isinstance(points,int) or not 0 < points <= 10_000_000:
            raise ValueError('Invalid grant')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.grant_in(db,user,points,reason,reference)

    @staticmethod
    def reserve_in(db, job, kind, user, context):
        if not enabled():
            return
        if not user:
            raise HTTPException(401, 'Sign in to use points for a search or photo')
        if db.execute('SELECT 1 FROM photo_credit_tasks WHERE job=?',(job,)).fetchone():
            return
        limit = int(context.get('creditLimitPoints', 250 if kind=='search' else 100))
        if not 10 <= limit <= 2000:
            raise HTTPException(422,'Choose a task limit between 10 and 2,000 points')
        total, held = BillingStore.balance_in(db,user)
        if total-held < limit*1000:
            raise HTTPException(402, f'Not enough available points to reserve {limit} points. Open Points & bills to top up or lower your task limit.')
        pricing=json.dumps({'version':VERSION,'pointsPerUsd':POINTS_PER_USD,'multiplier':str(MULTIPLIER)})
        db.execute('INSERT INTO photo_credit_tasks VALUES(?,?,?,?,?,?,NULL,NULL,NULL,?)',
            (job,kind,user,limit*1000,'pending',time.time(),pricing))

    def start_run(self, job, run):
        with self.db() as db:
            if db.execute('SELECT 1 FROM photo_credit_tasks WHERE job=?',(job,)).fetchone():
                db.execute('INSERT OR IGNORE INTO photo_credit_runs VALUES(?,?)',(job,run))

    def settle(self, job, state):
        if state not in ('complete','failed'):
            return
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            task=db.execute("SELECT * FROM photo_credit_tasks WHERE job=? AND state='pending'",(job,)).fetchone()
            if not task:
                return
            events=[]
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='photo_scout_cost_events'").fetchone():
                events=db.execute('''SELECT e.* FROM photo_scout_cost_events e
                    JOIN photo_credit_runs r ON r.run=e.run WHERE r.job=? ORDER BY e.created,e.id''',(job,)).fetchall()
            pricing=json.loads(task['pricing']);factor=Decimal(pricing['multiplier'])*pricing['pointsPerUsd']*1000
            items=[];known=Decimal(0);charged=0;unknown=0
            for event in events:
                cost=None if event['estimated_usd'] is None else max(Decimal(0),Decimal(str(event['estimated_usd'])))
                if cost is None:unknown+=1
                else:known+=cost
                amount=int((cost*factor).to_integral_value(rounding=ROUND_CEILING)) if cost is not None and state=='complete' else 0
                charged+=amount
                items.append({'stage':event['stage'],'model':event['model'],'requests':event['quantity'],
                    'status':event['status'],'usage':json.loads(event['usage']),
                    'basisUsd':float(cost) if cost is not None else None,'points':amount/1000,
                    'note':'Usage unavailable; no charge' if cost is None else 'Failed task; no charge' if state=='failed' else ''})
            if charged>task['held']:
                items.append({'stage':'task-limit-adjustment','model':'','requests':0,'points':(task['held']-charged)/1000,
                    'note':'Discount: task charge cannot exceed your authorized limit'})
                charged=task['held']
            bill={'jobId':job,'kind':task['kind'],'state':state,'created':task['created'],
                'settled':time.time(),'points':charged/1000,'limitPoints':task['held']/1000,
                'releasedPoints':(task['held']-charged)/1000,'pricing':pricing,
                'basis':'Recorded API usage × published standard prices; provider invoice adjustments and taxes are not included. Unknown usage is not charged.',
                'knownProviderUsd':float(known),'unknownCostEvents':unknown,'items':items}
            if charged:
                db.execute('INSERT OR IGNORE INTO photo_credit_ledger VALUES(?,?,?,?,?,?)',
                    (secrets.token_hex(16),task['user_id'],-charged,'Task bill','task:'+job,time.time()))
            db.execute('UPDATE photo_credit_tasks SET state=?,settled=?,charged=?,bill=? WHERE job=?',
                (state,bill['settled'],charged,json.dumps(bill),job))

    def reconcile(self):
        with self.db() as db:
            jobs=db.execute("SELECT job,kind FROM photo_credit_tasks WHERE state='pending'").fetchall()
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            states=[]
            for job in jobs:
                table='photo_scout_jobs' if job['kind']=='search' else 'photo_portraits'
                if table not in tables:continue
                row=db.execute(f'SELECT state FROM {table} WHERE id=?',(job['job'],)).fetchone()
                # Never release a running task on a guessed timeout. Durable workers
                # own recovery. A pruned task cannot run and releases its reservation.
                if not row or row['state'] in ('complete','failed'):
                    states.append((job['job'],row['state'] if row else 'failed'))
        for job,state in states:self.settle(job,state)

    def wallet(self, user, before=None):
        self.reconcile()
        with self.db() as db:
            total,held=self.balance_in(db,user)
            where='user_id=? AND bill IS NOT NULL';args=[user]
            if before:
                cursor=db.execute('SELECT created,job FROM photo_credit_tasks WHERE user_id=? AND job=?',(user,before)).fetchone()
                if not cursor:raise HTTPException(422,'Invalid bill cursor')
                where+=' AND (created<? OR (created=? AND job<?))';args.extend([cursor['created'],cursor['created'],cursor['job']])
            rows=db.execute('SELECT bill,job FROM photo_credit_tasks WHERE '+where+' ORDER BY created DESC,job DESC LIMIT 21',args).fetchall()
            ledger=db.execute('SELECT milli_points,reason,reference,created FROM photo_credit_ledger WHERE user_id=? ORDER BY created DESC LIMIT 30',(user,)).fetchall()
            subscriptions=db.execute("SELECT product,state,subscription FROM photo_credit_orders WHERE user_id=? AND subscription IS NOT NULL ORDER BY created DESC",(user,)).fetchall()
            pending=db.execute("SELECT job,kind,held,created FROM photo_credit_tasks WHERE user_id=? AND state='pending' ORDER BY created DESC",(user,)).fetchall()
        return {'balancePoints':total/1000,'heldPoints':held/1000,'availablePoints':(total-held)/1000,
            'bills':[json.loads(r['bill']) for r in rows[:20]],'nextCursor':rows[19]['job'] if len(rows)>20 else None,
            'ledger':[{'points':r['milli_points']/1000,'reason':r['reason'],'reference':r['reference'],'created':r['created']} for r in ledger],
            'pending':[dict(r)|{'limitPoints':r['held']/1000} for r in pending],
            'subscriptions':[{'product':r['product'],'state':r['state']} for r in subscriptions]}


def track_run(path, job):
    if enabled():
        from .costs import current
        if current.get():BillingStore(path).start_run(job,current.get().id)


def settle_task(path, job):
    if enabled():BillingStore(path).reconcile()


class CheckoutRequest(BaseModel):
    product: str = Field(max_length=80)
    requestToken: str = Field(min_length=32,max_length=80,pattern=r'^[A-Za-z0-9_-]+$')


class ConfirmRequest(BaseModel):
    sessionId: str = Field(pattern=r'^cs_(?:test|live)_[A-Za-z0-9]+$',max_length=150)


def create_billing_router(settings, require_api):
    router=APIRouter(tags=['Photo Scout wallet']);store=BillingStore(settings.database_path)

    def account(request,write=False):
        require_api(request.headers.get('authorization'))
        token=request.cookies.get(ACCOUNT_COOKIE,'')
        with store.db() as db:
            row=db.execute('SELECT * FROM photo_sessions WHERE hash=? AND expires>?',(digest(token),time.time())).fetchone()
        if not row:raise HTTPException(401,'Sign in to view your points and bills')
        if write and (not allowed_web_origin(request.headers.get('origin')) or not hmac.compare_digest(row['csrf'],request.headers.get('x-csrf-token',''))):
            raise HTTPException(403,'Invalid account request')
        return row

    @router.get('/photo-scout/v1/billing')
    def wallet(request:Request,before:str|None=None):
        require_api(request.headers.get('authorization'))
        info={'enabled':enabled(),'purchaseReady':payments_ready(),'pointsPerUsd':POINTS_PER_USD,'costMultiplier':float(MULTIPLIER),
            'applePurchaseReady':apple_ready(),'catalog':[{'id':key,**value} for key,value in CATALOG.items()],'pricingVersion':VERSION}
        _,user=TaskStore(settings.database_path).identity(request)
        if user:info.update(store.wallet(account(request)['user_id'],before))
        else:info['signInRequired']=True
        return JSONResponse(info,headers={'Cache-Control':'private, no-store'})

    def verify_session(session,order):
        if (session.get('id')!=order['session'] or session.get('mode')!=order['mode']
            or session.get('client_reference_id')!=order['id']
            or session.get('metadata',{}).get('serviceId')!='photo-scout-credits'
            or session.get('metadata',{}).get('orderId')!=order['id']
            or session.get('livemode')!=bool(order['live'])
            or session.get('currency')!='usd' or session.get('amount_total')!=order['cents']):
            raise HTTPException(422,'Payment does not match this point purchase')

    async def fund_session(session_id,user=None):
        with store.db() as db:order=db.execute('SELECT * FROM photo_credit_orders WHERE session=?',(session_id,)).fetchone()
        if not order or (user and order['user_id']!=user):raise HTTPException(404,'Point purchase not found')
        session=await stripe('GET','checkout/sessions/'+session_id)
        verify_session(session,order)
        if session.get('payment_status')!='paid':return {'state':'pending'}
        customer=session.get('customer');subscription=session.get('subscription')
        if order['mode']=='payment' and not isinstance(session.get('payment_intent'),str):raise HTTPException(422,'Missing payment reference')
        if not isinstance(customer,str) or not customer.startswith('cus_'):raise HTTPException(422,'Missing payment customer')
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE photo_credit_orders SET customer=?,subscription=?,state=?,payment_intent=? WHERE id=?',
                (customer,subscription,'active' if order['mode']=='subscription' else 'paid',session.get('payment_intent'),order['id']))
            if order['mode']=='payment':
                reference='stripe-checkout:'+session_id
                store.grant_in(db,order['user_id'],order['points'],'Point purchase',reference)
                db.execute('INSERT OR IGNORE INTO photo_credit_paid_units VALUES(?,?,?,?,?,?)',(reference,order['user_id'],order['id'],session.get('payment_intent'),order['cents'],order['points']))
        if order['mode']=='payment':await sync_refunds(session.get('payment_intent'))
        if subscription:
            sub=await stripe('GET','subscriptions/'+subscription)
            invoice=sub.get('latest_invoice')
            if isinstance(invoice,str):await fund_invoice(await stripe('GET','invoices/'+invoice))
        return {'state':'paid'}

    async def fund_invoice(invoice):
        subscription=(invoice.get('subscription') or (invoice.get('parent') or {}).get('subscription_details',{}).get('subscription'))
        if not isinstance(subscription,str):return
        sub=await stripe('GET','subscriptions/'+subscription)
        order_id=sub.get('metadata',{}).get('orderId')
        with store.db() as db:order=db.execute('SELECT * FROM photo_credit_orders WHERE id=?',(order_id,)).fetchone()
        if not order or order['mode']!='subscription':return
        # A renewal cannot change plan, account, environment, amount or currency.
        price=((sub.get('items') or {}).get('data') or [{}])[0].get('price',{})
        if (invoice.get('status')!='paid' or invoice.get('currency')!='usd'
            or invoice.get('livemode')!=bool(order['live']) or sub.get('livemode')!=bool(order['live'])
            or sub.get('metadata',{}).get('serviceId')!='photo-scout-credits'
            or invoice.get('amount_paid')!=order['cents']
            or invoice.get('billing_reason') not in ('subscription_create','subscription_cycle')
            or price.get('unit_amount')!=order['cents'] or price.get('currency')!='usd'
            or (price.get('recurring') or {}).get('interval')!='month'
            or (price.get('recurring') or {}).get('interval_count')!=1
            or (order['customer'] and order['customer']!=invoice.get('customer'))
            or sub.get('customer')!=invoice.get('customer')
            or (order['subscription'] and order['subscription']!=subscription)):
            raise HTTPException(422,'Invoice does not match this monthly point plan')
        invoice_id=invoice.get('id')
        if not isinstance(invoice_id,str) or not invoice_id.startswith('in_'):raise HTTPException(422,'Invalid invoice')
        intent=invoice.get('payment_intent')
        if not intent:
            payments=await stripe('GET','invoice_payments?invoice='+invoice_id+'&status=paid&limit=100')
            paid=(payments.get('data') or [])
            matching=[p for p in paid if p.get('currency')=='usd' and p.get('amount_paid')==order['cents'] and p.get('livemode')==bool(order['live']) and p.get('invoice')==invoice_id and p.get('payment',{}).get('type')=='payment_intent']
            if len(matching)!=1:raise HTTPException(422,'Invoice payment cannot be verified')
            intent=matching[0]['payment']['payment_intent']
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            prior=db.execute('SELECT reference FROM photo_credit_paid_units WHERE intent=?',(intent,)).fetchone()
            if prior and prior['reference']!='stripe-invoice:'+invoice_id:raise HTTPException(422,'Invoice payment was already attributed')
            db.execute('INSERT OR IGNORE INTO photo_credit_paid_units VALUES(?,?,?,?,?,?)',('stripe-invoice:'+invoice_id,order['user_id'],order['id'],intent,order['cents'],order['points']))
            store.grant_in(db,order['user_id'],order['points'],'Monthly subscription points','stripe-invoice:'+invoice_id)
            db.execute('UPDATE photo_credit_orders SET customer=?,subscription=?,state=? WHERE id=?',(invoice['customer'],subscription,sub.get('status','active'),order['id']))
        await sync_refunds(intent)

    @router.post('/photo-scout/v1/billing/checkout')
    async def checkout(payload:CheckoutRequest,request:Request):
        row=account(request,True)
        if 'PhotoScout-iOS/' in request.headers.get('user-agent',''):raise HTTPException(409,'Use the native in-app purchase option for this iOS app')
        if not payments_ready():raise HTTPException(503,'Point purchases are not configured yet')
        product=CATALOG.get(payload.product)
        if not product:raise HTTPException(422,'Choose a point pack or monthly plan')
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            order=db.execute('SELECT * FROM photo_credit_orders WHERE user_id=? AND token=?',(row['user_id'],payload.requestToken)).fetchone()
            if order and order['product']!=payload.product:raise HTTPException(409,'This checkout request was used for another product')
            if not order:
                if product['mode']=='subscription' and db.execute("SELECT 1 FROM photo_credit_orders WHERE user_id=? AND mode='subscription' AND (state IN ('active','past_due') OR (state='created' AND created>?))",(row['user_id'],time.time()-86400)).fetchone():
                    raise HTTPException(409,'You already have a monthly plan or pending checkout. Manage it in Points & bills.')
                order_id='pc_'+secrets.token_hex(16)
                db.execute('INSERT INTO photo_credit_orders(id,user_id,product,cents,points,mode,token,created,session,customer,subscription,state,live) VALUES(?,?,?,?,?,?,?,?,NULL,NULL,NULL,?,?)',
                    (order_id,row['user_id'],payload.product,product['cents'],product['points'],product['mode'],payload.requestToken,time.time(),'created',int(stripe_key().startswith(('sk_live_','rk_live_')))))
                order=db.execute('SELECT * FROM photo_credit_orders WHERE id=?',(order_id,)).fetchone()
        if order['session']:
            session=await stripe('GET','checkout/sessions/'+order['session'])
        else:
            data={'mode':product['mode'],'client_reference_id':order['id'],
                'metadata[serviceId]':'photo-scout-credits','metadata[orderId]':order['id'],
                'line_items[0][price_data][currency]':'usd','line_items[0][price_data][unit_amount]':str(order['cents']),
                'line_items[0][price_data][product_data][name]':product['name'],'line_items[0][quantity]':'1',
                'success_url':CANONICAL_ORIGIN+'/?credit_session={CHECKOUT_SESSION_ID}',
                'cancel_url':CANONICAL_ORIGIN+'/?credits=cancelled'}
            with store.db() as db:customer=db.execute('SELECT customer FROM photo_credit_orders WHERE user_id=? AND customer IS NOT NULL ORDER BY created DESC LIMIT 1',(row['user_id'],)).fetchone()
            if customer:data['customer']=customer['customer']
            else:data['customer_email']=json.loads(row['user_json']).get('email','')
            if product['mode']=='subscription':
                data.update({'line_items[0][price_data][recurring][interval]':'month',
                    'subscription_data[metadata][serviceId]':'photo-scout-credits','subscription_data[metadata][orderId]':order['id']})
            else:
                if not customer:data['customer_creation']='always'
            session=await stripe('POST','checkout/sessions',data,idempotency='photo-credit:'+order['id'])
            if not isinstance(session.get('id'),str) or not session.get('url','').startswith('https://checkout.stripe.com/'):
                raise HTTPException(503,'Checkout could not be created')
            with store.db() as db:db.execute('UPDATE photo_credit_orders SET session=? WHERE id=?',(session['id'],order['id']))
        return {'checkoutUrl':session.get('url'),'sessionId':session['id']}

    @router.post('/photo-scout/v1/billing/confirm')
    async def confirm(payload:ConfirmRequest,request:Request):
        row=account(request,True)
        result=await fund_session(payload.sessionId,row['user_id'])
        return result

    @router.post('/photo-scout/v1/billing/portal')
    async def portal(request:Request):
        row=account(request,True)
        if 'PhotoScout-iOS/' in request.headers.get('user-agent',''):raise HTTPException(409,'Manage native subscriptions in your Apple Account')
        with store.db() as db:order=db.execute('SELECT customer FROM photo_credit_orders WHERE user_id=? AND customer IS NOT NULL ORDER BY created DESC LIMIT 1',(row['user_id'],)).fetchone()
        if not order:raise HTTPException(404,'No paid point plan yet')
        config=os.getenv('PHOTO_SCOUT_BILLING_PORTAL_CONFIG','')
        if not config:raise HTTPException(503,'Subscription management is not configured yet')
        result=await stripe('POST','billing_portal/sessions',{'configuration':config,'customer':order['customer'],'return_url':CANONICAL_ORIGIN+'/?credits=manage'})
        if not result.get('url','').startswith('https://billing.stripe.com/'):raise HTTPException(503,'Subscription management is unavailable')
        return {'url':result['url']}

    async def sync_refunds(intent):
        if not isinstance(intent,str):return
        data=await stripe('GET','refunds?payment_intent='+intent+'&limit=100')
        for refund in data.get('data',[]):
            if refund.get('status')=='succeeded':await reverse_refund(refund['id'])

    async def reverse_refund(refund_id):
        refund=await stripe('GET','refunds/'+refund_id)
        if refund.get('status')!='succeeded':return
        intent=refund.get('payment_intent')
        with store.db() as db:
            order=db.execute('SELECT * FROM photo_credit_paid_units WHERE intent=?',(intent,)).fetchone()
        if not order:return
        if refund.get('currency')!='usd' or not isinstance(refund.get('amount'),int):raise HTTPException(422,'Invalid point refund')
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM photo_credit_ledger WHERE reference=?',(order['reference'],)).fetchone():return
            previous=db.execute('SELECT coalesce(sum(cents),0) FROM photo_credit_refunds WHERE order_id=?',(order['reference'],)).fetchone()[0]
            added=db.execute('INSERT OR IGNORE INTO photo_credit_refunds VALUES(?,?,?)',(refund_id,order['reference'],refund['amount'])).rowcount
            if not added:return
            target=min(order['cents'],previous+refund['amount'])
            delta=(target*order['points']*1000//order['cents'])-(min(order['cents'],previous)*order['points']*1000//order['cents'])
            if delta:db.execute('INSERT INTO photo_credit_ledger VALUES(?,?,?,?,?,?)',(secrets.token_hex(16),order['user_id'],-delta,'Refunded purchase points removed','stripe-refund:'+refund_id,time.time()))

    @router.post('/photo-scout/v1/billing/webhook',include_in_schema=False)
    async def webhook(request:Request):
        secret=os.getenv('PHOTO_SCOUT_BILLING_WEBHOOK_SECRET','')
        if not secret.startswith('whsec_'):raise HTTPException(503,'Wallet webhook is not configured')
        body=bytearray()
        async for part in request.stream():
            body.extend(part)
            if len(body)>262144:raise HTTPException(413,'Webhook is too large')
        try:event=verify_stripe_event(bytes(body),request.headers.get('stripe-signature'),secret)
        except InvalidStripeSignature as error:raise HTTPException(400,'Invalid payment signature') from error
        obj=event.get('data',{}).get('object',{});kind=event.get('type')
        if kind in ('checkout.session.completed','checkout.session.async_payment_succeeded') and obj.get('metadata',{}).get('serviceId')=='photo-scout-credits':
            await fund_session(obj['id'])
        elif kind in ('refund.created','refund.updated'):
            await reverse_refund(obj['id'])
        elif kind=='invoice.paid':
            # Retrieve an authoritative invoice rather than trusting event ordering.
            await fund_invoice(await stripe('GET','invoices/'+obj['id']))
        elif kind in ('customer.subscription.updated','customer.subscription.deleted') and obj.get('metadata',{}).get('serviceId')=='photo-scout-credits':
            sub=await stripe('GET','subscriptions/'+obj['id'])
            with store.db() as db:db.execute('UPDATE photo_credit_orders SET state=? WHERE subscription=?',(sub['status'],sub['id']))
        return {'status':'ok'}

    from .apple_billing import install_apple_routes
    install_apple_routes(router,store,account,CATALOG)
    router.billing_store=store
    router.fund_session=fund_session
    router.fund_invoice=fund_invoice
    router.reverse_refund=reverse_refund
    return router


def authorize_task(path, request):
    if not enabled():return
    if not allowed_web_origin(request.headers.get('origin')):raise HTTPException(403,'Invalid task origin')
    with sqlite3.connect(path,timeout=15) as db:
        row=db.execute('SELECT csrf FROM photo_sessions WHERE hash=? AND expires>?',(digest(request.cookies.get(ACCOUNT_COOKIE,'')),time.time())).fetchone()
    if not row:raise HTTPException(401,'Sign in to use your points')
    if not hmac.compare_digest(row[0],request.headers.get('x-csrf-token','')):raise HTTPException(403,'Invalid account request')
