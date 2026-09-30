from __future__ import annotations

import asyncio
import hmac
from dataclasses import dataclass
from functools import lru_cache

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, Response, status

from . import __version__
from .config import Settings
from .models import (
    CapabilitiesResponse,
    Capability,
    ClaimVerificationRequest,
    ClaimVerificationResult,
    EvidenceSnapshot,
)
from .provider import OpenAIEvidenceProvider
from .service import ClaimVerificationService, IdempotencyConflictError
from .storage import VerificationStore


@dataclass(frozen=True)
class VerificationTier:
    id: str
    path: str
    summary: str
    price_usd: str
    max_tool_calls: int
    max_output_tokens: int
    max_sources: int
    snapshot_mode: str
    max_snapshots: int


def verification_tiers(settings: Settings) -> tuple[VerificationTier, ...]:
    return (
        VerificationTier(
            id="quick",
            path="/v1/claims/verify/quick",
            summary="Quick verification for a narrow claim using up to 3 cited sources.",
            price_usd=settings.quick_price_usd,
            max_tool_calls=1,
            max_output_tokens=1500,
            max_sources=3,
            snapshot_mode="none",
            max_snapshots=0,
        ),
        VerificationTier(
            id="standard",
            path="/v1/claims/verify",
            summary="Standard verification with balanced evidence coverage.",
            price_usd=settings.price_usd,
            max_tool_calls=settings.max_tool_calls,
            max_output_tokens=settings.max_output_tokens,
            max_sources=8,
            snapshot_mode="cited",
            max_snapshots=3,
        ),
        VerificationTier(
            id="deep",
            path="/v1/claims/verify/deep",
            summary="Deep verification for compound or contested claims using up to 15 cited sources.",
            price_usd=settings.deep_price_usd,
            max_tool_calls=7,
            max_output_tokens=6000,
            max_sources=15,
            snapshot_mode="cited",
            max_snapshots=8,
        ),
        VerificationTier(
            id="research",
            path="/v1/claims/verify/research",
            summary="Research-grade verification with the largest search and evidence budget.",
            price_usd=settings.research_price_usd,
            max_tool_calls=15,
            max_output_tokens=12000,
            max_sources=20,
            snapshot_mode="all_sources",
            max_snapshots=20,
        ),
    )


def build_service(settings: Settings) -> ClaimVerificationService | None:
    if not settings.openai_api_key:
        return None
    return ClaimVerificationService(
        provider=OpenAIEvidenceProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            max_tool_calls=settings.max_tool_calls,
            max_output_tokens=settings.max_output_tokens,
        ),
        store=VerificationStore(settings.database_path, settings.snapshot_directory),
    )


def create_app(
    *,
    settings: Settings | None = None,
    service: ClaimVerificationService | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_environment()
    app = FastAPI(
        title="Agentic Services — Web Evidence",
        version=__version__,
        description="Verify factual claims against current web evidence.",
    )
    app.state.settings = resolved_settings
    app.state.verification_service = service or build_service(resolved_settings)
    tiers = verification_tiers(resolved_settings)

    def require_service_api_key(authorization: str | None) -> None:
        expected = resolved_settings.service_api_key
        if expected is None:
            return
        scheme, _, credential = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(credential, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="A valid Bearer API key is required",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @app.get("/healthz", tags=["operations"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["operations"])
    def readiness(request: Request) -> dict[str, str]:
        if request.app.state.verification_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="OPENAI_API_KEY is not configured",
            )
        return {"status": "ready"}

    @app.get("/v1/capabilities", response_model=CapabilitiesResponse, tags=["discovery"])
    def capabilities() -> CapabilitiesResponse:
        return CapabilitiesResponse(
            service="web-evidence",
            version=__version__,
            capabilities=[
                Capability(
                    id="web.evidence.verify-claim",
                    summary="Verify a factual claim using current web sources and return structured evidence.",
                    status="preview",
                    method="POST",
                    path="/v1/claims/verify",
                )
            ],
        )

    @app.get("/.well-known/agent-service.json", tags=["discovery"])
    def discovery() -> dict[str, object]:
        base_url = resolved_settings.base_url
        is_paid = resolved_settings.payment_recipient is not None
        payment_methods: list[dict[str, str]] = [{"protocol": "credit"}]
        if is_paid:
            payment_methods = [
                {
                    "protocol": "x402",
                    "network": "eip155:8453",
                    "asset": "USDC",
                    "payTo": resolved_settings.payment_recipient or "",
                },
                {
                    "protocol": "mpp",
                    "network": "eip155:8453",
                    "asset": "USDC",
                    "payTo": resolved_settings.payment_recipient or "",
                },
            ]
        return {
            "manifestVersion": "0.1",
            "service": {
                "id": f"{base_url}/.well-known/agent-service.json",
                "name": "Web Evidence",
                "description": "Time-stamped verification of factual claims against current web evidence.",
                "version": __version__,
                "homepage": base_url,
                "tags": ["web", "evidence", "fact-checking", "research"],
            },
            "provider": {
                "id": f"{base_url}/providers/agentic-services",
                "name": "Agentic Services",
                "contact": resolved_settings.provider_contact,
            },
            "capabilities": [
                {
                    "id": "web.evidence.verify-claim",
                    "summary": "Verify a factual claim using current web sources and return structured evidence.",
                    "domains": ["web", "research", "verification"],
                }
            ],
            "transports": [
                {
                    "id": "public-http",
                    "type": "http",
                    "url": base_url,
                    "specification": f"{base_url}/openapi.json",
                    "authorization": "signed-request" if is_paid else (
                        "bearer" if resolved_settings.service_api_key else "none"
                    ),
                }
            ],
            "operations": [
                {
                    "id": f"verify-claim-{tier.id}",
                    "capability": "web.evidence.verify-claim",
                    "transport": "public-http",
                    "summary": tier.summary,
                    "method": "POST",
                    "path": tier.path,
                    "inputSchema": ClaimVerificationRequest.model_json_schema(by_alias=True),
                    "outputSchema": ClaimVerificationResult.model_json_schema(by_alias=True),
                    "timeoutSeconds": 120,
                    "idempotent": True,
                }
                for tier in tiers
            ],
            "offers": [
                {
                    "id": f"verify-claim-{tier.id}-call",
                    "operation": f"verify-claim-{tier.id}",
                    "model": "per_call",
                    "amount": tier.price_usd if is_paid else "0",
                    "currency": "USD",
                    "unit": "request",
                    "paymentMethods": payment_methods,
                }
                for tier in tiers
            ],
            "provenance": {
                "summary": "Results preserve all provider-reported search sources, identify cited evidence, and include tier-dependent URL snapshots with hashes.",
                "freshness": "Caller-controlled freshness target; retrieval time is returned with every result.",
                "coverage": "Publicly accessible web sources supported by the upstream search provider.",
            },
            "policies": {
                "terms": f"{base_url}/terms",
                "privacy": f"{base_url}/privacy",
                "dataRetentionDays": 30,
                "allowedUse": ["Research and decision support"],
                "prohibitedUse": ["Representing the result as a guarantee of absolute truth"],
            },
            "extensions": {
                "lifecycle": "paid-preview" if is_paid else "preview",
                "paymentDiscovery": f"{base_url}/openapi.json",
                "verificationTiers": {
                    tier.id: {
                        "path": tier.path,
                        "maxToolCalls": tier.max_tool_calls,
                        "maxOutputTokens": tier.max_output_tokens,
                        "maxSources": tier.max_sources,
                        "snapshotMode": tier.snapshot_mode,
                        "maxSnapshots": tier.max_snapshots,
                    }
                    for tier in tiers
                },
            },
        }

    @app.get("/terms", tags=["legal"])
    def terms() -> dict[str, str]:
        return {
            "status": "preview",
            "summary": "Web Evidence is a research aid. Callers must independently assess high-impact decisions.",
        }

    @app.get("/privacy", tags=["legal"])
    def privacy() -> dict[str, str | int]:
        return {
            "status": "preview",
            "storedData": "Requests and verification results",
            "retentionDays": 30,
        }

    async def run_verification(
        tier: VerificationTier,
        payload: ClaimVerificationRequest,
        request: Request,
        idempotency_key: str | None,
        authorization: str | None,
    ) -> ClaimVerificationResult:
        require_service_api_key(authorization)
        if (
            "minimum_sources" in payload.model_fields_set
            and payload.minimum_sources > tier.max_sources
        ) or (
            "max_sources" in payload.model_fields_set
            and payload.max_sources > tier.max_sources
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"The {tier.id} tier supports at most {tier.max_sources} cited sources",
            )
        effective_payload = payload.model_copy(
            update={"max_sources": min(payload.max_sources, tier.max_sources)}
        )
        verification_service: ClaimVerificationService | None = request.app.state.verification_service
        if verification_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Web Evidence is not configured",
            )
        try:
            return await asyncio.to_thread(
                verification_service.verify,
                effective_payload,
                idempotency_key=idempotency_key,
                idempotency_namespace=tier.id,
                max_tool_calls=tier.max_tool_calls,
                max_output_tokens=tier.max_output_tokens,
                snapshot_mode=tier.snapshot_mode,
                max_snapshots=tier.max_snapshots,
            )
        except IdempotencyConflictError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Evidence provider failed: {type(error).__name__}",
            ) from error

    async def verification_endpoint(
        tier_id: str,
        payload: ClaimVerificationRequest,
        request: Request,
        idempotency_key: str | None,
        authorization: str | None,
    ) -> ClaimVerificationResult:
        tier = next(item for item in tiers if item.id == tier_id)
        return await run_verification(
            tier,
            payload,
            request,
            idempotency_key,
            authorization,
        )

    @app.post(
        "/v1/claims/verify/quick",
        response_model=ClaimVerificationResult,
        tags=["claim verification"],
        summary="Quick claim verification",
    )
    async def verify_claim_quick(
        payload: ClaimVerificationRequest,
        request: Request,
        idempotency_key: str | None = Header(default=None, max_length=255),
        authorization: str | None = Header(default=None),
    ) -> ClaimVerificationResult:
        return await verification_endpoint(
            "quick", payload, request, idempotency_key, authorization
        )

    @app.post(
        "/v1/claims/verify",
        response_model=ClaimVerificationResult,
        tags=["claim verification"],
        summary="Standard claim verification",
    )
    async def verify_claim(
        payload: ClaimVerificationRequest,
        request: Request,
        idempotency_key: str | None = Header(default=None, max_length=255),
        authorization: str | None = Header(default=None),
    ) -> ClaimVerificationResult:
        return await verification_endpoint(
            "standard", payload, request, idempotency_key, authorization
        )

    @app.post(
        "/v1/claims/verify/deep",
        response_model=ClaimVerificationResult,
        tags=["claim verification"],
        summary="Deep claim verification",
    )
    async def verify_claim_deep(
        payload: ClaimVerificationRequest,
        request: Request,
        idempotency_key: str | None = Header(default=None, max_length=255),
        authorization: str | None = Header(default=None),
    ) -> ClaimVerificationResult:
        return await verification_endpoint(
            "deep", payload, request, idempotency_key, authorization
        )

    @app.post(
        "/v1/claims/verify/research",
        response_model=ClaimVerificationResult,
        tags=["claim verification"],
        summary="Research-grade claim verification",
    )
    async def verify_claim_research(
        payload: ClaimVerificationRequest,
        request: Request,
        idempotency_key: str | None = Header(default=None, max_length=255),
        authorization: str | None = Header(default=None),
    ) -> ClaimVerificationResult:
        return await verification_endpoint(
            "research", payload, request, idempotency_key, authorization
        )

    @app.get(
        "/v1/claims/verifications/{verification_id}",
        response_model=ClaimVerificationResult,
        tags=["claim verification"],
    )
    def get_verification(
        verification_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> ClaimVerificationResult:
        require_service_api_key(authorization)
        verification_service: ClaimVerificationService | None = request.app.state.verification_service
        if verification_service is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Web Evidence is not configured")
        result = verification_service.store.get(verification_id)
        if result is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Verification not found")
        return result

    @app.get(
        "/v1/url-snapshots/{snapshot_id}",
        response_model=EvidenceSnapshot,
        tags=["evidence snapshots"],
    )
    def get_snapshot(
        snapshot_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> EvidenceSnapshot:
        require_service_api_key(authorization)
        verification_service: ClaimVerificationService | None = request.app.state.verification_service
        if verification_service is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Web Evidence is not configured")
        snapshot = verification_service.store.get_snapshot(snapshot_id)
        if snapshot is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot not found")
        return snapshot

    @app.get(
        "/v1/url-snapshots/{snapshot_id}/content",
        tags=["evidence snapshots"],
        responses={200: {"content": {"application/octet-stream": {}}}},
    )
    def get_snapshot_content(
        snapshot_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> Response:
        require_service_api_key(authorization)
        verification_service: ClaimVerificationService | None = request.app.state.verification_service
        if verification_service is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Web Evidence is not configured")
        metadata = verification_service.store.get_snapshot(snapshot_id)
        stored = verification_service.store.get_snapshot_content(snapshot_id)
        if metadata is None or stored is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot content not found")
        content, content_type = stored
        return Response(
            content=content,
            media_type=content_type,
            headers={
                "Cache-Control": "public, immutable, max-age=31536000",
                "X-Content-SHA256": metadata.raw_sha256 or "",
            },
        )

    return app


@lru_cache
def get_app() -> FastAPI:
    return create_app()


app = get_app()


def run() -> None:
    uvicorn.run("agentic_services.main:app", host="0.0.0.0", port=8000, reload=False)
