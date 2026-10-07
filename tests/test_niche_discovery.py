from pathlib import Path
import asyncio
import hashlib
import hmac
import json
import time

import httpx
import pytest
from jsonschema import Draft202012Validator, FormatChecker

from fastapi.testclient import TestClient

from agentic_services.config import Settings
from agentic_services.main import create_app
from agentic_services.niche_discovery import NicheStore, collect_gdelt_news, collect_hacker_news


def client_for(tmp_path: Path) -> TestClient:
    app = create_app(settings=Settings(
        openai_api_key=None, openai_model="test", database_path=tmp_path / "test.sqlite",
        base_url="https://api.example.test", admin_api_key="editor-secret", service_api_key="internal-secret",
        contact_smtp_username="smtp@example.test", contact_smtp_app_password="local-test",
    ))
    return TestClient(app)


def test_collected_signals_private_until_curated(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    store = NicheStore(tmp_path / "test.sqlite")
    for index in range(3):
        store.ingest_external_signal(external_id=f"test:{index}", origin="test_source", kind="complaint",
            text=f"Small shops cannot reconcile recurring invoices from provider {index} without manual copying.",
            source_url=f"https://example{index}.org/issue", observed_at="2026-10-01T00:00:00Z")
    assert client.get("/niche-discovery/v1/niches").json()["niches"] == []
    assert client.get("/niche-discovery/v1/admin/signals").status_code == 401
    signals = client.get("/niche-discovery/v1/admin/signals", headers={"X-Admin-Key": "editor-secret"}).json()["signals"]
    draft = {
        "title": "Recurring invoice reconciliation for small shops",
        "problem": "Small shops spend time copying recurring invoices across providers.",
        "buyer": "Small shop operators", "category": "commerce",
        "solution_hypothesis": "A reconciliation workflow across multiple billing providers.",
        "pain": 4, "frequency": 4, "willingness_to_pay": 2,
        "reachable_buyers": 3, "feasibility": 3, "competition_gap": 2,
        "evaluation_notes": "Evidence shows a repeated problem; willingness to pay remains unvalidated.",
        "signal_ids": [signal["id"] for signal in signals], "publish": True,
    }
    created = client.post("/niche-discovery/v1/admin/niches", json=draft, headers={"X-Admin-Key": "editor-secret"})
    assert created.status_code == 201
    niche = created.json()
    assert niche["scoreType"] == "editorial_hypothesis"
    assert niche["forecast"] is None
    assert niche["signalCount"] == 3
    assert niche["sourceDomainCount"] == 3
    assert client.get(f"/niche-discovery/v1/niches/{niche['id']}").status_code == 401
    assert client.get(f"/niche-discovery/v1/niches/{niche['id']}", headers={"Authorization": "Bearer internal-secret", "X-Niche-Paid-Call": "1"}).status_code == 200
    assert client.get("/niche-discovery/v1/niches?q=invoice&sort=score").status_code == 400
    assert client.get("/niche-discovery/v1/niches?q=unrelated").status_code == 400


def test_no_public_contribution_and_publication_floor(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    assert client.post("/niche-discovery/v1/submissions", json={"text": "a signal"}).status_code == 404
    schema = client.get("/niche-discovery/openapi.json").json()
    assert not any("submission" in path for path in schema["paths"])
    manifest = json.loads(Path("niche-discovery-site/.well-known/agent-service.json").read_text())
    assert not any("submit" in op["id"] or "submission" in op["path"] for op in manifest["operations"])
    NicheStore(tmp_path / "test.sqlite").ingest_external_signal(external_id="test:1", origin="test_source",
        kind="complaint", text="A longer than twenty-five character proposal for a product gap.",
        source_url="https://example.org/issue", observed_at="2026-10-01T00:00:00Z")
    signal = client.get("/niche-discovery/v1/admin/signals", headers={"X-Admin-Key": "editor-secret"}).json()["signals"][0]
    draft = {
        "title": "A narrow product gap", "problem": "A longer than thirty character description of the market gap.",
        "buyer": "Operators", "category": "software", "solution_hypothesis": "A practical workflow to solve this recurring issue.",
        "pain": 3, "frequency": 2, "willingness_to_pay": 1, "reachable_buyers": 2,
        "feasibility": 4, "competition_gap": 2,
        "evaluation_notes": "One signal cannot establish the existence of a market niche.",
        "signal_ids": [signal["id"]], "publish": True,
    }
    assert client.post("/niche-discovery/v1/admin/niches", json=draft, headers={"X-Admin-Key": "editor-secret"}).status_code == 422


def test_github_signal_is_idempotent_and_removes_email(tmp_path: Path) -> None:
    store = NicheStore(tmp_path / "signals.sqlite")
    issue = {"id": 22, "title": "Missing export support", "body": "We cannot export our invoices. Contact me at private@example.org for details.",
             "html_url": "https://github.com/acme/app/issues/22", "created_at": "2026-10-01T00:00:00Z"}
    assert store.ingest_github_issue(issue, "acme/app") is True
    assert store.ingest_github_issue(issue, "acme/app") is False
    assert "private@example.org" not in store.signals()[0]["text"]


def test_public_source_collectors_queue_private_deduplicated_candidates(tmp_path: Path,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    store = NicheStore(tmp_path / "signals.sqlite")
    monkeypatch.setenv("NICHE_GDELT_QUERY", '"customer complaints"')

    async def fake_get(self, url, **_kwargs):
        if url.endswith("askstories.json"):
            data = [101, 102]
        elif url.endswith("/101.json"):
            data = {"id": 101, "type": "story", "title": "Ask HN: What tool replaces manual invoice reconciliation?",
                    "time": 1790812800}
        elif url.endswith("/102.json"):
            data = {"id": 102, "type": "story", "title": "Ask HN: What are you reading for fun?", "time": 1790812800}
        else:
            data = {"articles": [{"url": "https://example.org/report", "title": "Shops report a recurring invoice data gap",
                                  "seendate": "20261001T120000Z"}]}
        return httpx.Response(200, request=httpx.Request("GET", url), json=data)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    assert asyncio.run(collect_hacker_news(store))["signalsAdded"] == 1
    assert asyncio.run(collect_gdelt_news(store))["signalsAdded"] == 1
    assert asyncio.run(collect_hacker_news(store))["signalsAdded"] == 0
    assert asyncio.run(collect_gdelt_news(store))["signalsAdded"] == 0
    signals = store.signals()
    assert {signal["origin"] for signal in signals} == {"hacker_news", "gdelt_news"}
    assert {signal["moderation"] for signal in signals} == {"pending"}
    store.record_collection_run("Hacker News", "ok", {"signalsAdded": 1})
    assert store.collection_status()[0]["result"]["signalsAdded"] == 1


def test_external_signal_rejects_bad_link_and_strips_email(tmp_path: Path) -> None:
    store = NicheStore(tmp_path / "signals.sqlite")
    assert not store.ingest_external_signal(external_id="bad", origin="source", kind="review",
                                            text="A long enough review describing a problem",
                                            source_url="javascript:alert(1)", observed_at="2026-10-01T00:00:00Z")
    assert store.ingest_external_signal(external_id="ok", origin="source", kind="review",
                                        text="The checkout is broken; contact user@example.org for a workaround.",
                                        source_url="https://store.example.org/review/1", observed_at="2026-10-01T00:00:00Z")
    assert "user@example.org" not in store.signals()[0]["text"]


def test_niche_manifest_matches_schema() -> None:
    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / "schemas/service-manifest.schema.json").read_text())
    manifest = json.loads((root / "niche-discovery-site/.well-known/agent-service.json").read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)


def test_subscription_checkout_and_webhook_access(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import agentic_services.niche_discovery as module

    monkeypatch.setenv("NICHE_STRIPE_SECRET_KEY", "sk_test_localdummy")
    monkeypatch.setenv("NICHE_STRIPE_PRICE_ID", "price_test123")
    monkeypatch.setenv("NICHE_TOKEN_SECRET", "a" * 40)
    monkeypatch.setenv("NICHE_STRIPE_WEBHOOK_SECRET", "whsec_localtest")
    monkeypatch.setattr(module.NicheStore, "published_count", lambda self: 1)
    emails: list[str] = []
    subscription_status = {"value": "active"}
    monkeypatch.setattr(module, "send_email", lambda *_args, **kwargs: emails.append(kwargs["body"]))

    async def fake_get(self, url, **_kwargs):
        if "/prices/" in url:
            data = {"unit_amount": 999, "currency": "usd", "recurring": {"interval": "month"}, "active": True, "livemode": False}
        elif "/checkout/sessions/" in url:
            data = {"id": "cs_test_abcdefghijk", "mode": "subscription", "payment_status": "paid", "livemode": False,
                    "subscription": "sub_test123", "customer": "cus_test123", "customer_details": {"email": "buyer@example.org"},
                    "metadata": {"serviceId": "niche-discovery"}}
        else:
            data = {"id": "sub_test123", "status": subscription_status["value"], "customer": "cus_test123",
                    "metadata": {"serviceId": "niche-discovery"}, "items": {"data": [{"price": {"id": "price_test123"}}]}}
        return httpx.Response(200, request=httpx.Request("GET", url), json=data)

    async def fake_post(self, url, **_kwargs):
        return httpx.Response(200, request=httpx.Request("POST", url), json={"url": "https://checkout.stripe.com/test"})

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    client = client_for(tmp_path)
    checkout = client.post("/niche-discovery/v1/subscription/checkout")
    assert checkout.status_code == 200
    assert checkout.json()["priceUsdPerMonth"] == "9.99"

    body = json.dumps({"type": "checkout.session.completed", "data": {"object": {
        "id": "cs_test_abcdefghijk", "metadata": {"serviceId": "niche-discovery"}}}}).encode()
    timestamp = int(time.time())
    sig = hmac.new(b"whsec_localtest", str(timestamp).encode() + b"." + body, hashlib.sha256).hexdigest()
    headers = {"Stripe-Signature": f"t={timestamp},v1={sig}"}
    assert client.post("/niche-discovery/v1/stripe/webhook", content=body, headers=headers).status_code == 200
    assert client.post("/niche-discovery/v1/stripe/webhook", content=body, headers=headers).status_code == 200
    assert len(emails) == 1
    token = emails[0].split("Agent API / human access token: ")[1].splitlines()[0]
    assert token.startswith("nd_")
    assert NicheStore(tmp_path / "test.sqlite").active_for_token(token)
    subscription_status["value"] = "canceled"
    cancelled = json.dumps({"type": "customer.subscription.deleted", "data": {"object": {
        "id": "sub_test123", "metadata": {"serviceId": "niche-discovery"}}}}).encode()
    sig = hmac.new(b"whsec_localtest", str(timestamp).encode() + b"." + cancelled, hashlib.sha256).hexdigest()
    headers = {"Stripe-Signature": f"t={timestamp},v1={sig}"}
    assert client.post("/niche-discovery/v1/stripe/webhook", content=cancelled, headers=headers).status_code == 200
    assert not NicheStore(tmp_path / "test.sqlite").active_for_token(token)
