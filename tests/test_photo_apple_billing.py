from types import SimpleNamespace
import time
import pytest
from fastapi import HTTPException
from appstoreserverlibrary.models.Environment import Environment
from appstoreserverlibrary.models.Type import Type
from appstoreserverlibrary.models.InAppOwnershipType import InAppOwnershipType
from agentic_services.photo_scout import billing, apple_billing


def setup(tmp_path):
    from fastapi import APIRouter
    store=billing.BillingStore(tmp_path/'db');router=APIRouter()
    apple_billing.install_apple_routes(router,store,lambda *_:None,billing.CATALOG)
    with store.db() as db:db.execute("INSERT INTO photo_apple_accounts VALUES('u','test-token')")
    return store,router


def transaction(**changes):
    return SimpleNamespace(productId='net.aisoup.photoscout.points_500',environment=Environment.PRODUCTION,bundleId='net.aisoup.photoscout',transactionId='100001',appAccountToken='test-token',type=Type.CONSUMABLE,inAppOwnershipType=InAppOwnershipType.PURCHASED,revocationDate=None,**changes)


def test_apple_verified_payment_is_bound_to_account_and_idempotent(tmp_path):
    store,router=setup(tmp_path);value=transaction()
    router.apply_apple_transaction(value,'u');router.apply_apple_transaction(value,'u')
    assert store.wallet('u')['balancePoints']==500
    with pytest.raises(HTTPException):router.apply_apple_transaction(value,'other')
    value.environment=Environment.SANDBOX
    with pytest.raises(HTTPException):router.apply_apple_transaction(value,'u')


def test_refund_before_or_after_purchase_cannot_be_replayed_for_points(tmp_path):
    store,router=setup(tmp_path);value=transaction();router.apply_apple_transaction(value,'u');value.revocationDate=int(time.time()*1000)
    router.apply_apple_transaction(value);router.apply_apple_transaction(value);assert store.wallet('u')['balancePoints']==0
    value.revocationDate=None;router.apply_apple_transaction(value);assert store.wallet('u')['balancePoints']==0
    value.transactionId='100002';value.revocationDate=1;router.apply_apple_transaction(value);value.revocationDate=None;router.apply_apple_transaction(value);assert store.wallet('u')['balancePoints']==0


def test_apple_unconfigured_verifier_fails_closed(monkeypatch):
    monkeypatch.delenv('PHOTO_SCOUT_APPLE_IAP_ENABLED',raising=False);apple_billing.verifier.cache_clear()
    assert not apple_billing.ready()
    with pytest.raises(HTTPException):apple_billing.verifier()
