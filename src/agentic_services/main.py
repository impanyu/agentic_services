from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
from typing import Annotated

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from . import __version__
from .admin_dashboard import admin_dashboard_html
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


class CustomerCreateRequest(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    email: Annotated[str | None, Field(max_length=320)] = None


class QuoteRequest(BaseModel):
    tier: str = "standard"


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
    store = (
        app.state.verification_service.store
        if app.state.verification_service is not None
        else VerificationStore(resolved_settings.database_path, resolved_settings.snapshot_directory)
    )
    app.state.store = store

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

    def require_admin_key(admin_key: str | None) -> None:
        expected = resolved_settings.admin_api_key
        if not expected:
            raise HTTPException(status_code=503, detail="ADMIN_API_KEY is not configured")
        if not admin_key or not hmac.compare_digest(admin_key, expected):
            raise HTTPException(status_code=401, detail="A valid X-Admin-Key is required")

    def require_customer(customer_key: str | None) -> dict[str, object]:
        customer = store.customer_for_key(customer_key)
        if customer is None:
            raise HTTPException(status_code=401, detail="A valid X-Agentic-Customer-Key is required")
        return customer

    def price_microusd(value: str) -> int:
        return int((Decimal(value) * Decimal(1_000_000)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    def order_cost(result: ClaimVerificationResult) -> dict[str, int]:
        provenance = result.provenance
        if provenance.cache_hit:
            return {"model": 0, "search": 0, "total": 0}
        uncached = max(0, provenance.input_tokens - provenance.cached_input_tokens)
        model_usd = (
            Decimal(uncached) * Decimal(resolved_settings.openai_input_usd_per_million)
            + Decimal(provenance.cached_input_tokens) * Decimal(resolved_settings.openai_cached_input_usd_per_million)
            + Decimal(provenance.output_tokens) * Decimal(resolved_settings.openai_output_usd_per_million)
        ) / Decimal(1_000_000)
        search_usd = (
            Decimal(provenance.web_search_call_count)
            * Decimal(resolved_settings.openai_web_search_usd_per_thousand)
            / Decimal(1000)
        )
        model = int((model_usd * Decimal(1_000_000)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        search = int((search_usd * Decimal(1_000_000)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        return {"model": model, "search": search, "total": model + search}

    def sign_receipt(receipt: dict[str, object]) -> str:
        secret = resolved_settings.receipt_signing_secret or resolved_settings.service_api_key
        if not secret:
            raise RuntimeError("RECEIPT_SIGNING_SECRET is not configured")
        canonical = json.dumps(receipt, separators=(",", ":"), sort_keys=True).encode()
        return hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()

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

    @app.get("/v1/services", tags=["discovery"])
    def service_catalog() -> dict[str, object]:
        """List platform services without requiring payment or an API key."""
        return {"services": store.list_services()}

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
                "commerce": {
                    "quote": f"{base_url}/v1/quotes",
                    "orderByAccessToken": f"{base_url}/v1/orders/{{order_id}}",
                    "customerOrders": f"{base_url}/v1/customer/orders",
                    "receiptVerification": f"{base_url}/v1/receipts/{{order_id}}/verify",
                },
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

    @app.post("/v1/quotes", tags=["commerce"])
    def create_quote(payload: QuoteRequest) -> dict[str, object]:
        tier = next((item for item in tiers if item.id == payload.tier), None)
        if tier is None:
            raise HTTPException(status_code=422, detail="Unknown verification tier")
        expires_at = datetime.now(UTC) + timedelta(minutes=15)
        quote = store.create_quote(
            tier=tier.id,
            amount_microusd=price_microusd(tier.price_usd),
            expires_at=expires_at.isoformat(),
        )
        quote["operation"] = tier.path
        quote["paymentMethods"] = ["x402", "mpp"]
        return quote

    async def run_verification(
        tier: VerificationTier,
        payload: ClaimVerificationRequest,
        request: Request,
        idempotency_key: str | None,
        authorization: str | None,
        response: Response,
        order_id: str | None,
        order_token_hash: str | None,
        payment_protocol: str | None,
        customer_key: str | None,
        customer_reference: str | None,
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
        request_json = effective_payload.model_dump_json(by_alias=True, exclude_none=True)
        request_hash = hashlib.sha256(request_json.encode()).hexdigest()
        if order_id:
            try:
                store.create_order(
                    order_id=order_id,
                    service_id="web-evidence",
                    tier=tier.id,
                    price_microusd=price_microusd(tier.price_usd),
                    payment_protocol=payment_protocol or "unknown",
                    order_token_hash=order_token_hash,
                    request_hash=request_hash,
                    customer_key=customer_key,
                    customer_reference=customer_reference,
                )
            except Exception as error:
                raise HTTPException(status_code=409, detail="Order identifier already exists") from error
        verification_service: ClaimVerificationService | None = request.app.state.verification_service
        if verification_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Web Evidence is not configured",
            )
        try:
            result = await asyncio.to_thread(
                verification_service.verify,
                effective_payload,
                idempotency_key=idempotency_key,
                idempotency_namespace=tier.id,
                max_tool_calls=tier.max_tool_calls,
                max_output_tokens=tier.max_output_tokens,
                snapshot_mode=tier.snapshot_mode,
                max_snapshots=tier.max_snapshots,
            )
            if order_id:
                costs = order_cost(result)
                issued_at = datetime.now(UTC).isoformat()
                receipt = {
                    "receiptId": f"rcpt_{secrets.token_hex(16)}",
                    "orderId": order_id,
                    "serviceId": "web-evidence",
                    "verificationId": result.verification_id,
                    "tier": tier.id,
                    "amountMicrousd": price_microusd(tier.price_usd),
                    "currency": "USD",
                    "paymentProtocol": payment_protocol or "unknown",
                    "resultSha256": hashlib.sha256(result.model_dump_json(by_alias=True).encode()).hexdigest(),
                    "issuedAt": issued_at,
                    "signatureAlgorithm": "hmac-sha256",
                }
                signature = sign_receipt(receipt)
                store.complete_order(
                    order_id=order_id,
                    values={
                        "verification_id": result.verification_id,
                        "provider_response_id": result.provenance.provider_response_id,
                        "model": result.provenance.model,
                        "input_tokens": result.provenance.input_tokens,
                        "cached_input_tokens": result.provenance.cached_input_tokens,
                        "output_tokens": result.provenance.output_tokens,
                        "web_search_calls": result.provenance.web_search_call_count,
                        "model_cost_microusd": costs["model"],
                        "search_cost_microusd": costs["search"],
                        "total_cost_microusd": costs["total"],
                    },
                    receipt=receipt,
                    signature=signature,
                )
                response.headers["X-Agentic-Order-Id"] = order_id
                response.headers["X-Agentic-Receipt-Id"] = str(receipt["receiptId"])
            return result
        except IdempotencyConflictError as error:
            if order_id:
                store.fail_order(order_id, "idempotency_conflict")
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except Exception as error:
            if order_id:
                store.fail_order(order_id, type(error).__name__)
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
        response: Response,
        order_id: str | None,
        order_token_hash: str | None,
        payment_protocol: str | None,
        customer_key: str | None,
        customer_reference: str | None,
    ) -> ClaimVerificationResult:
        tier = next(item for item in tiers if item.id == tier_id)
        return await run_verification(
            tier,
            payload,
            request,
            idempotency_key,
            authorization,
            response,
            order_id,
            order_token_hash,
            payment_protocol,
            customer_key,
            customer_reference,
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
        response: Response,
        idempotency_key: str | None = Header(default=None, max_length=255),
        authorization: str | None = Header(default=None),
        x_agentic_order_id: str | None = Header(default=None),
        x_agentic_order_token_hash: str | None = Header(default=None),
        x_agentic_payment_protocol: str | None = Header(default=None),
        x_agentic_customer_key: str | None = Header(default=None),
        x_agentic_customer_reference: str | None = Header(default=None, max_length=255),
    ) -> ClaimVerificationResult:
        return await verification_endpoint(
            "quick", payload, request, idempotency_key, authorization, response,
            x_agentic_order_id, x_agentic_order_token_hash, x_agentic_payment_protocol,
            x_agentic_customer_key, x_agentic_customer_reference,
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
        response: Response,
        idempotency_key: str | None = Header(default=None, max_length=255),
        authorization: str | None = Header(default=None),
        x_agentic_order_id: str | None = Header(default=None),
        x_agentic_order_token_hash: str | None = Header(default=None),
        x_agentic_payment_protocol: str | None = Header(default=None),
        x_agentic_customer_key: str | None = Header(default=None),
        x_agentic_customer_reference: str | None = Header(default=None, max_length=255),
    ) -> ClaimVerificationResult:
        return await verification_endpoint(
            "standard", payload, request, idempotency_key, authorization, response,
            x_agentic_order_id, x_agentic_order_token_hash, x_agentic_payment_protocol,
            x_agentic_customer_key, x_agentic_customer_reference,
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
        response: Response,
        idempotency_key: str | None = Header(default=None, max_length=255),
        authorization: str | None = Header(default=None),
        x_agentic_order_id: str | None = Header(default=None),
        x_agentic_order_token_hash: str | None = Header(default=None),
        x_agentic_payment_protocol: str | None = Header(default=None),
        x_agentic_customer_key: str | None = Header(default=None),
        x_agentic_customer_reference: str | None = Header(default=None, max_length=255),
    ) -> ClaimVerificationResult:
        return await verification_endpoint(
            "deep", payload, request, idempotency_key, authorization, response,
            x_agentic_order_id, x_agentic_order_token_hash, x_agentic_payment_protocol,
            x_agentic_customer_key, x_agentic_customer_reference,
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
        response: Response,
        idempotency_key: str | None = Header(default=None, max_length=255),
        authorization: str | None = Header(default=None),
        x_agentic_order_id: str | None = Header(default=None),
        x_agentic_order_token_hash: str | None = Header(default=None),
        x_agentic_payment_protocol: str | None = Header(default=None),
        x_agentic_customer_key: str | None = Header(default=None),
        x_agentic_customer_reference: str | None = Header(default=None, max_length=255),
    ) -> ClaimVerificationResult:
        return await verification_endpoint(
            "research", payload, request, idempotency_key, authorization, response,
            x_agentic_order_id, x_agentic_order_token_hash, x_agentic_payment_protocol,
            x_agentic_customer_key, x_agentic_customer_reference,
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

    @app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
    def admin_dashboard() -> str:
        return admin_dashboard_html()

    @app.get("/v1/admin/summary", tags=["admin"])
    def admin_summary(
        days: Annotated[int, Query(ge=0, le=3650)] = 30,
        service_id: Annotated[str | None, Query(alias="serviceId")] = None,
        x_admin_key: str | None = Header(default=None),
    ) -> dict[str, object]:
        require_admin_key(x_admin_key)
        since = (datetime.now(UTC) - timedelta(days=days)).isoformat() if days else None
        return {
            "periodDays": days or None,
            "since": since,
            "serviceId": service_id,
            **store.admin_summary(since, service_id),
        }

    @app.get("/v1/admin/services", tags=["admin"])
    def admin_services(
        x_admin_key: str | None = Header(default=None),
    ) -> dict[str, object]:
        require_admin_key(x_admin_key)
        return {"services": store.list_services()}

    @app.get("/v1/admin/orders", tags=["admin"])
    def admin_orders(
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
        service_id: Annotated[str | None, Query(alias="serviceId")] = None,
        x_admin_key: str | None = Header(default=None),
    ) -> dict[str, object]:
        require_admin_key(x_admin_key)
        orders = store.list_orders(service_id=service_id, limit=limit, offset=offset)
        return {
            "orders": orders,
            "limit": limit,
            "offset": offset,
            "serviceId": service_id,
            "total": store.count_orders(service_id=service_id),
        }

    @app.get("/v1/admin/orders/{order_id}", tags=["admin"])
    def admin_order(order_id: str, x_admin_key: str | None = Header(default=None)) -> dict[str, object]:
        require_admin_key(x_admin_key)
        order = store.get_order(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")
        return order

    @app.get("/v1/admin/customers", tags=["admin"])
    def admin_customers(x_admin_key: str | None = Header(default=None)) -> dict[str, object]:
        require_admin_key(x_admin_key)
        return {"customers": store.list_customers()}

    @app.post("/v1/admin/customers", tags=["admin"], status_code=201)
    def admin_create_customer(
        payload: CustomerCreateRequest,
        x_admin_key: str | None = Header(default=None),
    ) -> dict[str, object]:
        require_admin_key(x_admin_key)
        customer, api_key = store.create_customer(name=payload.name, email=payload.email)
        return {**customer, "apiKey": api_key, "apiKeyShownOnce": True}

    @app.get("/v1/customer/orders", tags=["customer commerce"])
    def customer_orders(
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        x_agentic_customer_key: str | None = Header(default=None),
    ) -> dict[str, object]:
        customer = require_customer(x_agentic_customer_key)
        orders = store.list_orders(
            customer_id=str(customer["customer_id"]), limit=limit, offset=offset
        )
        return {"customerId": customer["customer_id"], "orders": orders, "limit": limit, "offset": offset}

    @app.get("/v1/customer/orders/{order_id}", tags=["customer commerce"])
    def customer_order(
        order_id: str,
        x_agentic_customer_key: str | None = Header(default=None),
    ) -> dict[str, object]:
        customer = require_customer(x_agentic_customer_key)
        order = store.get_order_for_customer(order_id, str(customer["customer_id"]))
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")
        return order

    @app.get("/v1/orders/{order_id}", tags=["customer commerce"])
    def order_by_access_token(
        order_id: str,
        x_agentic_order_token: str | None = Header(default=None),
    ) -> dict[str, object]:
        if not x_agentic_order_token:
            raise HTTPException(status_code=401, detail="X-Agentic-Order-Token is required")
        order = store.get_order_with_token(order_id, x_agentic_order_token)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")
        return order

    @app.post("/v1/receipts/{order_id}/verify", tags=["commerce"])
    def verify_receipt(order_id: str) -> dict[str, object]:
        order = store.get_order(order_id)
        receipt = order.get("receipt") if order else None
        if not isinstance(receipt, dict):
            raise HTTPException(status_code=404, detail="Receipt not found")
        supplied_signature = str(receipt.pop("signature"))
        expected = sign_receipt(receipt)
        return {
            "valid": hmac.compare_digest(supplied_signature, expected),
            "orderId": order_id,
            "receiptId": receipt["receiptId"],
            "signatureAlgorithm": receipt["signatureAlgorithm"],
        }

    return app


@lru_cache
def get_app() -> FastAPI:
    return create_app()


app = get_app()


def run() -> None:
    uvicorn.run("agentic_services.main:app", host="0.0.0.0", port=8000, reload=False)
