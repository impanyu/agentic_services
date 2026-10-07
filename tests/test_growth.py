from pathlib import Path
from uuid import uuid4
import sqlite3

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from agentic_services.growth import GrowthStore, create_growth_router


def setup(tmp_path):
    store = GrowthStore(tmp_path / 'growth.db', 'test-secret')
    app = FastAPI()
    def admin(key):
        if key != 'admin':
            raise HTTPException(401)
    def service(key):
        if key != 'Bearer private':
            raise HTTPException(401)
    app.include_router(create_growth_router(store, admin, service))
    return store, TestClient(app)


def headers():
    return {'Authorization': 'Bearer private', 'Origin': 'https://aisoup.net', 'X-Usage-Session': str(uuid4()), 'X-Usage-Source': 'community', 'X-Usage-Campaign': 'writer-pilot'}


def test_browser_cannot_forge_payment_or_internal_cohort(tmp_path):
    store, client = setup(tmp_path)
    h = {**headers(), 'X-Usage-Test': 'internal'}
    assert client.post('/v1/usage/events', headers=h, json={'service': 'web-evidence', 'stage': 'payment_confirmed'}).status_code == 422
    assert client.post('/v1/usage/events', headers=h, json={'service': 'web-evidence', 'stage': 'page_view', 'claim': 'private'}).status_code == 422
    event = {'service': 'web-evidence', 'stage': 'page_view'}
    for _ in range(2):
        assert client.post('/v1/usage/events', headers=h, json=event).status_code == 202
    rows = store.summary(30, None)['rows']
    assert len(rows) == 1 and rows[0]['events'] == 1 and rows[0]['cohort'] == 'unmarked'
    with store.connect() as db:
        assert h['X-Usage-Session'] not in str([tuple(r) for r in db.execute('SELECT * FROM growth_sessions')])


def test_signed_internal_marker_and_webhook_outcomes_stay_correlated(tmp_path):
    store, client = setup(tmp_path)
    assert client.post('/v1/admin/funnel/test-token').status_code == 401
    token = client.post('/v1/admin/funnel/test-token', headers={'X-Admin-Key': 'admin'}).json()['token']
    h = {**headers(), 'X-Usage-Test': token}
    client.post('/v1/usage/events', headers=h, json={'service': 'web-evidence', 'stage': 'page_view'})
    store.checkout('web-evidence', 'intent', {k.lower(): v for k, v in h.items()})
    # Browser-return and webhook replay count one paid order, not two.
    for _ in range(2):
        store.outcome('web-evidence', 'intent', 'payment_confirmed')
        store.outcome('web-evidence', 'intent', 'report_delivered')
    rows = store.summary(30, 'web-evidence')['rows']
    assert len(rows) == 4 and all(r['cohort'] == 'internal' and r['events'] == 1 for r in rows)
    assert all(r['authority'] == 'server' for r in rows if r['stage'] != 'page_view')
    assert client.get('/v1/admin/funnel').status_code == 401


def test_unattributed_payment_does_not_become_an_external_customer(tmp_path):
    store, _ = setup(tmp_path)
    store.outcome('web-evidence', 'historical', 'payment_confirmed')
    row = store.summary(30, None)['rows'][0]
    assert row['cohort'] == 'unattributed' and row['sessions'] == 0


def test_commerce_continues_when_measurement_database_fails(tmp_path, monkeypatch):
    store, _ = setup(tmp_path)
    monkeypatch.setattr(store, 'connect', lambda: (_ for _ in ()).throw(sqlite3.OperationalError('busy')))
    assert store.outcome('web-evidence', 'intent', 'report_delivered') is None


def test_rejects_untrusted_ingress_and_expires_old_attribution(tmp_path):
    store, client = setup(tmp_path)
    h = headers()
    event = {'service': 'web-evidence', 'stage': 'page_view'}
    assert client.post('/v1/usage/events', headers={**h, 'Origin': 'https://example.com'}, json=event).status_code == 403
    assert client.post('/v1/usage/events', headers={**h, 'Authorization': 'Bearer wrong'}, json=event).status_code == 401
    assert client.post('/v1/usage/events', headers=h, content=b'x' * 1025).status_code == 413
    client.post('/v1/usage/events', headers=h, json=event)
    with store.connect() as db:
        db.execute("UPDATE growth_events SET created='2020-01-01T00:00:00+00:00'")
        db.execute("UPDATE growth_sessions SET created='2020-01-01T00:00:00+00:00'")
    assert store.summary(0, None)['rows'] == []
    store.record('web-evidence', 'report_ready', 'new', None)
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM growth_sessions').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM growth_events').fetchone()[0] == 1
