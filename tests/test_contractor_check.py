from __future__ import annotations

import json
import hashlib
import sqlite3
import hmac
import time
import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from agentic_services.config import Settings
from agentic_services.contractor_check import LicenseNotFoundError, parse_license_page
from agentic_services.main import create_app
from agentic_services.storage import VerificationStore


SAMPLE_HTML = """
<span id="MainContent_Header2Detail">1234567</span>
<span id="MainContent_extractDate">Data current as of 10/3/2026 2:00 PM</span>
<td id="MainContent_BusInfo">EXAMPLE ELECTRIC LLC<br>San Diego, CA</td>
<td id="MainContent_Status">This license is current and active.</td>
<td id="MainContent_ClassCellTable"><a>C-10 - ELECTRICAL</a></td>
<td id="MainContent_ExpDt">10/31/2027</td>
<td id="MainContent_BondingCellTable">This license filed a Contractor's Bond with EXAMPLE SURETY.</td>
<td id="MainContent_WCStatus">This license has workers compensation insurance with EXAMPLE INSURER.</td>
"""


def test_c10_report_uses_explicit_cslb_fields() -> None:
    report = parse_license_page(SAMPLE_HTML, "1234567")
    assert report["businessName"] == "EXAMPLE ELECTRIC LLC"
    assert report["assessment"] == "selected_checks_present"
    assert all(report["checks"].values())
    assert report["source"]["url"] == "https://www.cslb.ca.gov/1234567"
    with pytest.raises(LicenseNotFoundError):
        parse_license_page(SAMPLE_HTML, "7654321")


def test_manifest_valid() -> None:
    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / "schemas/service-manifest.schema.json").read_text())
    manifest = json.loads((root / "contractor-check-site/.well-known/agent-service.json").read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)


def test_report_retention_purges_expired_intents(tmp_path: Path) -> None:
    store = VerificationStore(tmp_path / "retention.sqlite")
    report = parse_license_page(SAMPLE_HTML, "1234567")
    expired = store.create_contractor_intent("1234567", report, 1900)
    current = store.create_contractor_intent("1234567", report, 1900)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE contractor_check_intents SET created_at=? WHERE intent_id=?",
            ("2026-01-01T00:00:00+00:00", expired),
        )
    store.purge_contractor_intents()
    assert store.get_contractor_intent(expired) is None
    assert store.get_contractor_intent(current) is not None


def test_checkout_releases_only_matching_paid_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import agentic_services.contractor_routes as routes

    report = parse_license_page(SAMPLE_HTML, "1234567")

    async def fake_lookup(_number: str):
        return report

    paid = {"value": False, "amount": 1900}
    intent = {"id": None}

    async def fake_stripe(self, method, url, **kwargs):
        request = httpx.Request(method, url)
        if method == "POST":
            intent["id"] = kwargs["data"]["client_reference_id"]
            return httpx.Response(200, request=request, json={
                "id": "cs_test_abc12345678901234567890",
                "url": "https://checkout.stripe.com/c/pay/test-session",
            })
        return httpx.Response(200, request=request, json={
            "client_reference_id": intent["id"],
            "payment_status": "paid" if paid["value"] else "unpaid",
            "currency": "usd", "amount_total": paid["amount"],
            "mode": "payment", "livemode": False,
        })

    monkeypatch.setattr(routes, "fetch_license_report", fake_lookup)
    monkeypatch.setattr(httpx.AsyncClient, "request", fake_stripe)
    monkeypatch.setenv("CONTRACTOR_STRIPE_SECRET_KEY", "rk_test_localdummy")
    app = create_app(settings=Settings(
        openai_api_key=None, openai_model="test", database_path=tmp_path / "db.sqlite",
        base_url="https://api.example.test", service_api_key="internal-key",
        receipt_signing_secret="receipt-test-secret",
    ))
    client = TestClient(app)
    headers = {"Authorization": "Bearer internal-key"}
    created = client.post("/contractor-check/v1/checkout", json={"licenseNumber": "1234567"}, headers=headers)
    assert created.status_code == 200
    session_id = "cs_test_abc12345678901234567890"
    path = f"/contractor-check/v1/report?session_id={session_id}"
    assert client.get(path, headers=headers).status_code == 402
    paid["value"] = True
    paid["amount"] = 1800
    assert client.get(path, headers=headers).status_code == 402
    paid["amount"] = 1900
    delivered = client.get(path, headers=headers)
    assert delivered.status_code == 200
    assert delivered.json()["report"]["businessName"] == "EXAMPLE ELECTRIC LLC"
    assert client.get(path, headers=headers).json()["orderId"] == delivered.json()["orderId"]
    order = app.state.store.get_order(delivered.json()["orderId"])
    assert order["serviceId"] == "contractor-check"
    assert order["status"] == "completed"
    with sqlite3.connect(tmp_path / "db.sqlite") as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM ledger_entries WHERE order_id=? AND kind='revenue'",
            (delivered.json()["orderId"],),
        ).fetchone()[0]
    assert count == 1


def test_contractor_webhook_fulfills_without_browser_return(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report = parse_license_page(SAMPLE_HTML, "1234567")
    app = create_app(settings=Settings(
        openai_api_key=None, openai_model="test", database_path=tmp_path / "db.sqlite",
        base_url="https://api.example.test", service_api_key="internal-key",
        receipt_signing_secret="receipt-test-secret",
    ))
    client = TestClient(app)
    monkeypatch.setenv("CONTRACTOR_STRIPE_SECRET_KEY", "rk_test_localdummy")
    monkeypatch.setenv("HUMAN_STRIPE_WEBHOOK_SECRET", "whsec_localtest")
    session_id = "cs_test_abc12345678901234567890"
    intent_id = app.state.store.create_contractor_intent("1234567", report, 1900)
    app.state.store.bind_contractor_session(intent_id, session_id)

    async def fake_stripe(self, method, url, **kwargs):
        return httpx.Response(200, request=httpx.Request(method, url), json={
            "id": session_id, "client_reference_id": intent_id,
            "metadata": {"serviceId": "contractor-check"},
            "payment_status": "paid", "currency": "usd", "amount_total": 1900,
            "mode": "payment", "livemode": False,
        })

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_stripe)
    body = json.dumps({
        "id": "evt_test_contractor", "type": "checkout.session.completed",
        "data": {"object": {"id": session_id, "metadata": {"serviceId": "contractor-check"}}},
    }).encode()
    timestamp = int(time.time())
    signature = hmac.new(b"whsec_localtest", str(timestamp).encode() + b"." + body, hashlib.sha256).hexdigest()
    headers = {"Stripe-Signature": f"t={timestamp},v1={signature}"}
    assert client.post("/v1/stripe/checkout-webhook", content=body, headers=headers).status_code == 200
    assert asyncio.run(app.state.process_stripe_fulfillment()) is True
    assert app.state.store.get_order(f"ord_{intent_id[4:]}")["status"] == "completed"
    assert client.get(f"/contractor-check/v1/report?session_id={session_id}").status_code == 200
    with sqlite3.connect(tmp_path / "db.sqlite") as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM ledger_entries WHERE order_id=? AND kind='revenue'",
            (f"ord_{intent_id[4:]}",),
        ).fetchone()[0]
    assert count == 1


def test_agent_paid_order_token_is_retrievable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import agentic_services.contractor_routes as routes

    async def fake_lookup(_number: str):
        return parse_license_page(SAMPLE_HTML, "1234567")

    monkeypatch.setattr(routes, "fetch_license_report", fake_lookup)
    app = create_app(settings=Settings(
        openai_api_key=None, openai_model="test", database_path=tmp_path / "db.sqlite",
        base_url="https://api.example.test", service_api_key="internal-key",
        receipt_signing_secret="receipt-test-secret",
    ))
    client = TestClient(app)
    order_id = "ord_" + "a" * 32
    token = "ort_exampletesttoken"
    headers = {
        "Authorization": "Bearer internal-key",
        "X-Agentic-Order-Id": order_id,
        "X-Agentic-Order-Amount-Microusd": "1000000",
        "X-Agentic-Order-Token-Hash": hashlib.sha256(token.encode()).hexdigest(),
        "X-Agentic-Payment-Protocol": "x402-mcp",
    }
    response = client.post("/contractor-check/v1/check", json={"licenseNumber": "1234567"}, headers=headers)
    assert response.status_code == 200
    assert response.headers["X-Agentic-Receipt-Id"].startswith("rcpt_")
    assert app.state.store.get_order_with_token(order_id, token)["status"] == "completed"
    assert app.state.store.get_order_with_token(order_id, "bad") is None
