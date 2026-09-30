from __future__ import annotations

from pathlib import Path
import json

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from agentic_services.config import Settings
from agentic_services.main import create_app
from agentic_services.models import ProviderAnalysis
from agentic_services.provider import ProviderResult
from agentic_services.service import ClaimVerificationService
from agentic_services.storage import VerificationStore


class FakeProvider:
    model = "fake-evidence-model"

    def __init__(self, *, consulted: bool = True) -> None:
        self.calls = 0
        self.consulted = consulted

    def analyze(self, request):
        self.calls += 1
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
        urls = {"https://example.com/docs"} if self.consulted else set()
        return ProviderResult(
            analysis=analysis,
            provider_response_id="resp_test",
            model=self.model,
            consulted_urls=urls,
        )


def build_client(tmp_path: Path, provider: FakeProvider) -> TestClient:
    settings = Settings(
        openai_api_key="test-only",
        openai_model=provider.model,
        database_path=tmp_path / "evidence.db",
        base_url="https://testserver",
    )
    service = ClaimVerificationService(
        provider=provider,
        store=VerificationStore(settings.database_path),
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
    assert result["evidence"][0]["snapshotted"] is False
    assert result["provenance"]["citedSourceCount"] == 1

    retrieved = client.get(f"/v1/claims/verifications/{result['verificationId']}")
    assert retrieved.status_code == 200
    assert retrieved.json() == result


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
