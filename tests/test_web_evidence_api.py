from __future__ import annotations

from pathlib import Path
import json
import hashlib
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from agentic_services.config import Settings
from agentic_services.main import create_app
from agentic_services.models import EvidenceSnapshot, ProviderAnalysis, ProviderSource
from agentic_services.provider import ProviderResult, extract_provider_sources
from agentic_services.snapshot import SnapshotCapture
from agentic_services.service import ClaimVerificationService
from agentic_services.storage import VerificationStore


class FakeProvider:
    model = "fake-evidence-model"

    def __init__(self, *, consulted: bool = True) -> None:
        self.calls = 0
        self.consulted = consulted
        self.budgets: list[tuple[int | None, int | None]] = []

    def analyze(self, request, *, max_tool_calls=None, max_output_tokens=None):
        self.calls += 1
        self.budgets.append((max_tool_calls, max_output_tokens))
        analysis = ProviderAnalysis.model_validate(
            {
                "status": "confirmed",
                "conclusion": "The official source supports the claim.",
                "atomicFacts": [
                    {
                        "statement": request.claim,
                        "status": "confirmed",
                        "confidence": 0.98,
                        "explanation": "The current official documentation states this directly.",
                        "evidenceIds": ["ev1"],
                    }
                ],
                "evidence": [
                    {
                        "id": "ev1",
                        "url": "https://example.com/docs",
                        "title": "Official documentation",
                        "publisher": "Example",
                        "publishedAt": None,
                        "excerpt": "The feature is supported.",
                        "relationship": "supports",
                        "sourceType": "official",
                        "qualityReason": "First-party product documentation.",
                    }
                ],
                "conflicts": [],
                "limitations": [],
            }
        )
        sources = []
        if self.consulted:
            sources = [
                ProviderSource(
                    source_id="src_docs",
                    url="https://example.com/docs",
                    title="Official documentation",
                    search_call_ids=["ws_1"],
                    actions=["search"],
                    queries=[request.claim],
                ),
                ProviderSource(
                    source_id="src_uncited",
                    url="https://example.net/background",
                    title="Background result",
                    search_call_ids=["ws_1"],
                    actions=["search"],
                    queries=[request.claim],
                ),
            ]
        return ProviderResult(
            analysis=analysis,
            provider_response_id="resp_test",
            model=self.model,
            provider_sources=sources,
            input_tokens=1000,
            output_tokens=200,
            web_search_call_count=1,
        )


class FakeSnapshotter:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def capture_many(self, urls: list[str]) -> list[SnapshotCapture]:
        captures = []
        for index, url in enumerate(urls):
            self.urls.append(url)
            content = f"snapshot:{url}".encode()
            captures.append(
                SnapshotCapture(
                    metadata=EvidenceSnapshot(
                        snapshot_id=f"snap_{len(self.urls)}_{index}",
                        requested_url=url,
                        final_url=url,
                        retrieved_at=datetime.now(UTC),
                        status="captured",
                        http_status=200,
                        content_type="text/plain",
                        content_length=len(content),
                        raw_sha256=hashlib.sha256(content).hexdigest(),
                        normalized_sha256=hashlib.sha256(content).hexdigest(),
                    ),
                    content=content,
                )
            )
        return captures


def test_provider_sources_come_only_from_web_search_metadata() -> None:
    sources = extract_provider_sources(
        {
            "output": [
                {
                    "type": "web_search_call",
                    "id": "ws_1",
                    "action": {
                        "type": "search",
                        "query": "example claim",
                        "sources": [
                            {"url": "https://example.com/a", "title": "A"},
                            {"url": "https://example.com/b", "title": "B"},
                        ],
                    },
                },
                {"type": "message", "content": [{"url": "https://invented.example/"}]},
            ]
        }
    )

    assert [source.url for source in sources] == [
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert sources[0].search_call_ids == ["ws_1"]
    assert sources[0].queries == ["example claim"]


def build_client(
    tmp_path: Path,
    provider: FakeProvider,
    *,
    service_api_key: str | None = None,
) -> TestClient:
    settings = Settings(
        openai_api_key="test-only",
        openai_model=provider.model,
        database_path=tmp_path / "evidence.db",
        base_url="https://testserver",
        service_api_key=service_api_key,
    )
    service = ClaimVerificationService(
        provider=provider,
        store=VerificationStore(settings.database_path, tmp_path / "snapshots"),
        snapshotter=FakeSnapshotter(),
    )
    return TestClient(create_app(settings=settings, service=service))


def test_verify_claim_and_retrieve_result(tmp_path: Path) -> None:
    provider = FakeProvider()
    client = build_client(tmp_path, provider)

    response = client.post(
        "/v1/claims/verify",
        headers={"Idempotency-Key": "claim-1"},
        json={
            "claim": "The feature is supported.",
            "minimumSources": 1,
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "confirmed"
    assert result["evidence"][0]["consulted"] is True
    assert result["evidence"][0]["providerSourceMatched"] is True
    assert result["evidence"][0]["cited"] is True
    assert result["evidence"][0]["snapshotted"] is True
    assert len(result["providerSources"]) == 2
    assert result["providerSources"][0]["cited"] is True
    assert result["providerSources"][1]["cited"] is False
    assert len(result["snapshots"]) == 1
    assert result["provenance"]["citedSourceCount"] == 1
    assert result["provenance"]["providerSourceCount"] == 2
    assert result["provenance"]["snapshottedSourceCount"] == 1

    retrieved = client.get(f"/v1/claims/verifications/{result['verificationId']}")
    assert retrieved.status_code == 200
    assert retrieved.json() == result

    snapshot_id = result["snapshots"][0]["snapshotId"]
    metadata = client.get(f"/v1/url-snapshots/{snapshot_id}")
    content = client.get(f"/v1/url-snapshots/{snapshot_id}/content")
    assert metadata.status_code == 200
    assert metadata.json() == result["snapshots"][0]
    assert content.status_code == 200
    assert hashlib.sha256(content.content).hexdigest() == content.headers["X-Content-SHA256"]


def test_idempotency_prevents_duplicate_provider_calls(tmp_path: Path) -> None:
    provider = FakeProvider()
    client = build_client(tmp_path, provider)
    request = {"claim": "The feature is supported.", "minimumSources": 1}

    first = client.post("/v1/claims/verify", headers={"Idempotency-Key": "same"}, json=request)
    second = client.post("/v1/claims/verify", headers={"Idempotency-Key": "same"}, json=request)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["verificationId"] == second.json()["verificationId"]
    assert provider.calls == 1


def test_reusing_idempotency_key_for_different_claim_returns_conflict(tmp_path: Path) -> None:
    client = build_client(tmp_path, FakeProvider())
    headers = {"Idempotency-Key": "same"}

    assert client.post(
        "/v1/claims/verify",
        headers=headers,
        json={"claim": "The first claim.", "minimumSources": 1},
    ).status_code == 200
    response = client.post(
        "/v1/claims/verify",
        headers=headers,
        json={"claim": "A different claim.", "minimumSources": 1},
    )

    assert response.status_code == 409


def test_unmatched_source_cannot_confirm_claim(tmp_path: Path) -> None:
    client = build_client(tmp_path, FakeProvider(consulted=False))

    response = client.post(
        "/v1/claims/verify",
        json={"claim": "The feature is supported.", "minimumSources": 1},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "insufficient_evidence"
    assert result["atomicFacts"][0]["status"] == "insufficient_evidence"
    assert result["evidence"][0]["consulted"] is False


def test_official_only_requires_allowed_domains(tmp_path: Path) -> None:
    client = build_client(tmp_path, FakeProvider())

    response = client.post(
        "/v1/claims/verify",
        json={
            "claim": "The feature is supported.",
            "sourcePolicy": "official_only",
        },
    )

    assert response.status_code == 422


def test_blocked_domain_cannot_be_used_as_evidence(tmp_path: Path) -> None:
    client = build_client(tmp_path, FakeProvider())

    response = client.post(
        "/v1/claims/verify",
        json={
            "claim": "The feature is supported.",
            "minimumSources": 1,
            "blockedDomains": ["example.com"],
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "insufficient_evidence"
    assert result["evidence"] == []


def test_discovery_document_matches_manifest_schema(tmp_path: Path) -> None:
    client = build_client(tmp_path, FakeProvider())
    manifest = client.get("/.well-known/agent-service.json")

    assert manifest.status_code == 200
    schema_path = Path(__file__).parents[1] / "schemas" / "service-manifest.schema.json"
    schema = json.loads(schema_path.read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest.json())


def test_paid_discovery_advertises_x402_and_mpp(tmp_path: Path) -> None:
    settings = Settings(
        openai_api_key="test-only",
        openai_model="fake-evidence-model",
        database_path=tmp_path / "evidence.db",
        base_url="https://api.example.com",
        service_api_key="internal-secret",
        payment_recipient="0x1111111111111111111111111111111111111111",
        price_usd="0.05",
    )
    provider = FakeProvider()
    service = ClaimVerificationService(
        provider=provider,
        store=VerificationStore(settings.database_path),
        snapshotter=FakeSnapshotter(),
    )
    manifest = TestClient(create_app(settings=settings, service=service)).get(
        "/.well-known/agent-service.json"
    ).json()

    offers = {offer["operation"]: offer for offer in manifest["offers"]}
    assert offers["verify-claim-quick"]["amount"] == "0.02"
    assert offers["verify-claim-standard"]["amount"] == "0.05"
    assert offers["verify-claim-deep"]["amount"] == "0.12"
    assert offers["verify-claim-research"]["amount"] == "0.25"
    for offer in offers.values():
        assert offer["currency"] == "USD"
        assert {method["protocol"] for method in offer["paymentMethods"]} == {"x402", "mpp"}
        assert all(method["network"] == "eip155:8453" for method in offer["paymentMethods"])


def test_verification_tiers_apply_distinct_provider_budgets(tmp_path: Path) -> None:
    provider = FakeProvider()
    client = build_client(tmp_path, provider)

    expected = {
        "/v1/claims/verify/quick": (1, 1500),
        "/v1/claims/verify": (3, 3000),
        "/v1/claims/verify/deep": (7, 6000),
        "/v1/claims/verify/research": (15, 12000),
    }
    for path, budget in expected.items():
        response = client.post(path, json={"claim": "The feature is supported.", "minimumSources": 1})
        assert response.status_code == 200
        assert provider.budgets[-1] == budget


def test_snapshot_policy_varies_by_tier(tmp_path: Path) -> None:
    provider = FakeProvider()
    client = build_client(tmp_path, provider)

    quick = client.post(
        "/v1/claims/verify/quick",
        json={"claim": "The feature is supported.", "minimumSources": 1},
    ).json()
    research = client.post(
        "/v1/claims/verify/research",
        json={"claim": "The feature is supported.", "minimumSources": 1},
    ).json()

    assert quick["snapshots"] == []
    assert len(research["snapshots"]) == 2


def test_paid_order_is_recorded_with_cost_receipt_and_customer_access(tmp_path: Path) -> None:
    provider = FakeProvider()
    settings = Settings(
        openai_api_key="test-only",
        openai_model=provider.model,
        database_path=tmp_path / "evidence.db",
        base_url="https://testserver",
        service_api_key="internal-secret",
        admin_api_key="admin-secret",
        receipt_signing_secret="receipt-secret",
    )
    service = ClaimVerificationService(
        provider=provider,
        store=VerificationStore(settings.database_path, tmp_path / "snapshots"),
        snapshotter=FakeSnapshotter(),
    )
    client = TestClient(create_app(settings=settings, service=service))

    created = client.post(
        "/v1/admin/customers",
        headers={"X-Admin-Key": "admin-secret"},
        json={"name": "Test Agent", "email": "agent@example.com"},
    )
    assert created.status_code == 201
    customer_key = created.json()["apiKey"]
    order_token = "ort_test_secret"
    order_id = "ord_test"
    paid = client.post(
        "/v1/claims/verify",
        headers={
            "Authorization": "Bearer internal-secret",
            "X-Agentic-Order-Id": order_id,
            "X-Agentic-Order-Token-Hash": hashlib.sha256(order_token.encode()).hexdigest(),
            "X-Agentic-Payment-Protocol": "x402",
            "X-Agentic-Customer-Key": customer_key,
        },
        json={"claim": "The feature is supported.", "minimumSources": 1},
    )
    assert paid.status_code == 200
    assert paid.headers["X-Agentic-Order-Id"] == order_id
    assert paid.headers["X-Agentic-Receipt-Id"].startswith("rcpt_")

    summary = client.get(
        "/v1/admin/summary", headers={"X-Admin-Key": "admin-secret"}
    ).json()
    assert summary["revenueMicrousd"] == 50_000
    assert summary["costMicrousd"] == 10_200
    assert summary["grossProfitMicrousd"] == 39_800
    assert summary["webSearchCalls"] == 1

    customer_orders = client.get(
        "/v1/customer/orders", headers={"X-Agentic-Customer-Key": customer_key}
    )
    assert customer_orders.status_code == 200
    assert customer_orders.json()["orders"][0]["orderId"] == order_id
    assert "totalCostMicrousd" not in customer_orders.json()["orders"][0]

    token_order = client.get(
        f"/v1/orders/{order_id}", headers={"X-Agentic-Order-Token": order_token}
    )
    assert token_order.status_code == 200
    assert token_order.json()["receipt"]["signatureAlgorithm"] == "hmac-sha256"
    assert client.post(f"/v1/receipts/{order_id}/verify").json()["valid"] is True


def test_quote_lists_price_and_payment_methods(tmp_path: Path) -> None:
    client = build_client(tmp_path, FakeProvider())
    quote = client.post("/v1/quotes", json={"tier": "deep"})
    assert quote.status_code == 200
    assert quote.json()["amountMicrousd"] == 120_000
    assert quote.json()["paymentMethods"] == ["x402", "mpp"]


def test_quick_tier_rejects_explicit_excess_source_budget(tmp_path: Path) -> None:
    client = build_client(tmp_path, FakeProvider())
    response = client.post(
        "/v1/claims/verify/quick",
        json={"claim": "The feature is supported.", "maxSources": 4},
    )
    assert response.status_code == 422


def test_protected_service_requires_valid_bearer_key(tmp_path: Path) -> None:
    client = build_client(tmp_path, FakeProvider(), service_api_key="preview-secret")
    payload = {"claim": "The feature is supported.", "minimumSources": 1}

    missing = client.post("/v1/claims/verify", json=payload)
    invalid = client.post(
        "/v1/claims/verify",
        headers={"Authorization": "Bearer wrong"},
        json=payload,
    )
    valid = client.post(
        "/v1/claims/verify",
        headers={"Authorization": "Bearer preview-secret"},
        json=payload,
    )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert valid.status_code == 200
    manifest = client.get("/.well-known/agent-service.json").json()
    assert manifest["transports"][0]["authorization"] == "bearer"
