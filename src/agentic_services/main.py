from __future__ import annotations

import asyncio
import hmac
from functools import lru_cache

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, status

from . import __version__
from .config import Settings
from .models import (
    CapabilitiesResponse,
    Capability,
    ClaimVerificationRequest,
    ClaimVerificationResult,
)
from .provider import OpenAIEvidenceProvider
from .service import ClaimVerificationService, IdempotencyConflictError
from .storage import VerificationStore


def build_service(settings: Settings) -> ClaimVerificationService | None:
    if not settings.openai_api_key:
        return None
    return ClaimVerificationService(
        provider=OpenAIEvidenceProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
        ),
        store=VerificationStore(settings.database_path),
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
                    "id": "verify-claim",
                    "capability": "web.evidence.verify-claim",
                    "transport": "public-http",
                    "summary": "Verify one claim and persist the time-stamped result.",
                    "method": "POST",
                    "path": "/v1/claims/verify",
                    "inputSchema": ClaimVerificationRequest.model_json_schema(by_alias=True),
                    "outputSchema": ClaimVerificationResult.model_json_schema(by_alias=True),
                    "timeoutSeconds": 120,
                    "idempotent": True,
                }
            ],
            "offers": [
                {
                    "id": "verify-claim-call",
                    "operation": "verify-claim",
                    "model": "per_call",
                    "amount": resolved_settings.price_usd if is_paid else "0",
                    "currency": "USD",
                    "unit": "request",
                    "paymentMethods": payment_methods,
                }
            ],
            "provenance": {
                "summary": "Results identify consulted and cited web sources and the model used for analysis.",
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

    @app.post(
        "/v1/claims/verify",
        response_model=ClaimVerificationResult,
        tags=["claim verification"],
    )
    async def verify_claim(
        payload: ClaimVerificationRequest,
        request: Request,
        idempotency_key: str | None = Header(default=None, max_length=255),
        authorization: str | None = Header(default=None),
    ) -> ClaimVerificationResult:
        require_service_api_key(authorization)
        verification_service: ClaimVerificationService | None = request.app.state.verification_service
        if verification_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Web Evidence is not configured",
            )
        try:
            return await asyncio.to_thread(
                verification_service.verify,
                payload,
                idempotency_key=idempotency_key,
            )
        except IdempotencyConflictError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Evidence provider failed: {type(error).__name__}",
            ) from error

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

    return app


@lru_cache
def get_app() -> FastAPI:
    return create_app()


app = get_app()


def run() -> None:
    uvicorn.run("agentic_services.main:app", host="0.0.0.0", port=8000, reload=False)
