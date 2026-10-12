"""Apple-signed, account-bound StoreKit transactions for the shared point wallet."""
import asyncio
import os
import secrets
import time
import uuid
from functools import lru_cache
from pathlib import Path
from fastapi import HTTPException, Request
from pydantic import BaseModel, Field
from appstoreserverlibrary.models.Environment import Environment
from appstoreserverlibrary.signed_data_verifier import SignedDataVerifier

BUNDLE_ID='net.aisoup.photoscout'
PRODUCTS={BUNDLE_ID+'.'+key.replace('-','_'):key for key in (
    'points-500','points-1000','points-2500','monthly-1200','monthly-3250','monthly-7000')}


def ready():
    return os.getenv('PHOTO_SCOUT_APPLE_IAP_ENABLED','0')=='1' and os.getenv('PHOTO_SCOUT_APPLE_APP_ID','').isdigit()


@lru_cache(maxsize=1)
def verifier():
    if not ready():raise HTTPException(503,'Apple point purchases are not configured yet')
    roots=[p.read_bytes() for p in Path(__file__).with_name('apple_certs').glob('*.cer')]
    return SignedDataVerifier(roots,True,Environment.PRODUCTION,BUNDLE_ID,int(os.environ['PHOTO_SCOUT_APPLE_APP_ID']))


class AppleTransactionRequest(BaseModel):
    signedTransaction: str = Field(min_length=100,max_length=30000)


class AppleNotification(BaseModel):
    signedPayload: str = Field(min_length=100,max_length=50000)


def install_apple_routes(router,store,account,catalog):
    with store.db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS photo_apple_accounts(user_id TEXT PRIMARY KEY,token TEXT UNIQUE NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS photo_apple_transactions(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,product TEXT NOT NULL,points INTEGER NOT NULL,revoked INTEGER NOT NULL DEFAULT 0)')

    def apply(transaction,user=None):
        product=PRODUCTS.get(transaction.productId)
        if product not in catalog:raise HTTPException(422,'Unknown Apple point product')
        if transaction.environment!=Environment.PRODUCTION or transaction.bundleId!=BUNDLE_ID:
            raise HTTPException(422,'Invalid Apple app or payment environment')
        if not transaction.transactionId or not transaction.appAccountToken:
            raise HTTPException(422,'Apple transaction is missing account ownership')
        expected_type='Consumable' if catalog[product]['mode']=='payment' else 'Auto-Renewable Subscription'
        if transaction.type.value!=expected_type or transaction.inAppOwnershipType.value!='PURCHASED':
            raise HTTPException(422,'Invalid Apple product type or purchase ownership')
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            owner=db.execute('SELECT user_id FROM photo_apple_accounts WHERE token=?',(str(transaction.appAccountToken).lower(),)).fetchone()
            if not owner or (user and owner['user_id']!=user):raise HTTPException(403,'Apple purchase belongs to another Photo Scout account')
            user=owner['user_id'];points=catalog[product]['points'];tid=transaction.transactionId
            previous=db.execute('SELECT * FROM photo_apple_transactions WHERE id=?',(tid,)).fetchone()
            if previous and (previous['user_id']!=user or previous['product']!=product):raise HTTPException(409,'Apple transaction ownership mismatch')
            revoked=bool(transaction.revocationDate) or bool(previous and previous['revoked'])
            if not previous:db.execute('INSERT INTO photo_apple_transactions VALUES(?,?,?,?,?)',(tid,user,product,points,int(revoked)))
            if revoked:
                if db.execute('SELECT 1 FROM photo_credit_ledger WHERE reference=?',('apple:'+tid,)).fetchone():
                    db.execute('INSERT OR IGNORE INTO photo_credit_ledger VALUES(?,?,?,?,?,?)',(secrets.token_hex(16),user,-points*1000,'Apple refunded points removed','apple-refund:'+tid,time.time()))
                db.execute('UPDATE photo_apple_transactions SET revoked=1 WHERE id=?',(tid,))
            elif not previous or not previous['revoked']:
                store.grant_in(db,user,points,'Apple purchase points' if catalog[product]['mode']=='payment' else 'Apple monthly points','apple:'+tid)
        return {'state':'revoked' if revoked else 'paid'}

    @router.post('/photo-scout/v1/billing/apple/account')
    def apple_account(request:Request):
        row=account(request,True)
        if not ready():raise HTTPException(503,'Apple point purchases are not configured yet')
        with store.db() as db:
            db.execute('INSERT OR IGNORE INTO photo_apple_accounts VALUES(?,?)',(row['user_id'],str(uuid.uuid4())))
            token=db.execute('SELECT token FROM photo_apple_accounts WHERE user_id=?',(row['user_id'],)).fetchone()[0]
        return {'appAccountToken':token,'products':PRODUCTS}

    @router.post('/photo-scout/v1/billing/apple/transaction')
    async def transaction(payload:AppleTransactionRequest,request:Request):
        row=account(request,True)
        try:decoded=await asyncio.to_thread(verifier().verify_and_decode_signed_transaction,payload.signedTransaction)
        except HTTPException:raise
        except Exception as error:raise HTTPException(400,'Apple transaction could not be verified') from error
        return apply(decoded,row['user_id'])

    @router.post('/photo-scout/v1/billing/apple/notifications',include_in_schema=False)
    async def notification(payload:AppleNotification):
        try:
            verified=verifier()
            decoded=await asyncio.to_thread(verified.verify_and_decode_notification,payload.signedPayload)
            if decoded.data and decoded.data.signedTransactionInfo:
                transaction=await asyncio.to_thread(verified.verify_and_decode_signed_transaction,decoded.data.signedTransactionInfo)
                apply(transaction)
        except HTTPException:raise
        except Exception as error:raise HTTPException(400,'Apple notification could not be verified') from error
        return {'status':'ok'}
    router.apply_apple_transaction=apply
