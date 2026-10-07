from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import time
from contextlib import asynccontextmanager
from urllib.parse import urlparse
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
from typing import Annotated

import uvicorn
import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import __version__
from .admin_dashboard import admin_dashboard_html
from .config import Settings
from .contractor_routes import create_contractor_router
from .contact import send_contact_email, send_email
from .models import (
    CapabilitiesResponse,
    Capability,
    ClaimVerificationRequest,
    ClaimVerificationResult,
    EvidenceSnapshot,
)
from .niche_agent.routes import create_manager_router
from .niche_agent.research import create_research_router
from .niche_agent.websub import create_websub_router
from .niche_agent.query_service import create_query_router
from .niche_agent.store import ManagerStore
from .niche_search import collect_search
from .niche_reddit import collect_reddit
from .niche_discovery import (NicheStore, collect_configured_github,
                              collect_gdelt_news, collect_hacker_news,
                              create_niche_router)
from .provider import OpenAIEvidenceProvider
from .service import ClaimVerificationService, IdempotencyConflictError
from .storage import VerificationStore
from .status_page import (
    notify_status_subscribers,
    send_subscription_verification,
    status_page_html,
    status_rss,
)
from .stripe_webhook import InvalidStripeSignature, verify_stripe_event


class CustomerCreateRequest(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    email: Annotated[str | None, Field(max_length=320)] = None


class QuoteRequest(BaseModel):
    tier: str = "standard"


class ContactMessageRequest(BaseModel):
    name: Annotated[str, Field(min_length=2, max_length=120)]
    email: Annotated[str, Field(min_length=3, max_length=320)]
    subject: Annotated[str, Field(min_length=3, max_length=160)] = "General inquiry"
    message: Annotated[str, Field(min_length=20, max_length=4000)]
    service_id: Annotated[str | None, Field(alias="serviceId", max_length=100)] = None
    company: Annotated[str, Field(max_length=200)] = ""


class SupportTicketCreateRequest(BaseModel):
    subject: Annotated[str, Field(min_length=3, max_length=160)]
    message: Annotated[str, Field(min_length=10, max_length=8000)]
    service_id: Annotated[str | None, Field(alias="serviceId", max_length=100)] = None
    order_id: Annotated[str | None, Field(alias="orderId", max_length=100)] = None
    priority: str = "normal"


class SupportMessageRequest(BaseModel):
    message: Annotated[str, Field(min_length=2, max_length=8000)]


class SupportTicketUpdateRequest(BaseModel):
    status: str
    message: Annotated[str | None, Field(max_length=8000)] = None


class StatusSubscriptionRequest(BaseModel):
    channel: str
    target: Annotated[str, Field(min_length=3, max_length=2000)]
    verification_email: Annotated[str | None, Field(alias="verificationEmail", max_length=320)] = None


class StatusIncidentCreateRequest(BaseModel):
    title: Annotated[str, Field(min_length=3, max_length=200)]
    message: Annotated[str, Field(min_length=3, max_length=8000)]
    severity: str
    affected_components: Annotated[list[str], Field(alias="affectedComponents", min_length=1, max_length=20)]


class StatusIncidentUpdateRequest(BaseModel):
    status: str
    message: Annotated[str, Field(min_length=3, max_length=8000)]


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

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async def worker() -> None:
            while True:
                if not await app.state.process_stripe_fulfillment():
                    await asyncio.sleep(5)

        task = asyncio.create_task(worker())
        async def niche_collector() -> None:
            store = ManagerStore(resolved_settings.database_path)
            while True:
                collectors = []
                if os.getenv("NICHE_COLLECT_HACKER_NEWS", "") == "1":
                    collectors.append(("Hacker News", collect_hacker_news))
                if os.getenv("NICHE_GDELT_QUERY", "").strip():
                    collectors.append(("GDELT", collect_gdelt_news))
                if os.getenv("NICHE_GITHUB_REPOSITORIES", "").strip():
                    collectors.append(("GitHub", collect_configured_github))
                if os.getenv("NICHE_COLLECT_REDDIT", "") == "1":
                    collectors.append(("Reddit", collect_reddit))
                if os.getenv("NICHE_COLLECT_SEARCH", "") == "1":
                    collectors.append(("Web search discovery", collect_search))
                from .niche_agent.sources import source_registry, collect_registered
                for identifier, definition in source_registry().items():
                    if definition['enabled']:
                        async def extra_collect(s, source=identifier):
                            return await collect_registered(s, source)
                        collectors.append((identifier, extra_collect))
                for name, collect in collectors:
                    try:
                        result = await collect(store)
                        if result.get('signalsAdded', 0) > 0:
                            store.enqueue('source.batch.ready', {'source': name, 'signalsAdded': result['signalsAdded']},
                                          f'collection:{name}:{time.time_ns()}')
                        store.record_collection_run(name, "ok" if result["status"] == "pending_review" else result["status"], result)
                    except Exception as error:
                        store.record_collection_run(name, "error", {"errorType": type(error).__name__})
                        logging.getLogger(__name__).exception("Niche Discovery %s collection failed", name)
                await asyncio.sleep(6 * 60 * 60)

        niche_task = asyncio.create_task(niche_collector())
        try:
            yield
        finally:
            task.cancel()
            niche_task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            try:
                await niche_task
            except asyncio.CancelledError:
                pass

    app = FastAPI(
        title="Agentic Services — Web Evidence",
        version=__version__,
        description="Verify factual claims against current web evidence.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://aisoup.net", "https://www.aisoup.net"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
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

    def settled_amount_microusd(tier: VerificationTier, value: str | None) -> int:
        if value is None:
            return price_microusd(tier.price_usd)
        try:
            amount = int(value)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="Invalid settled order amount") from error
        if amount <= 0 or amount > 100_000_000:
            raise HTTPException(status_code=400, detail="Invalid settled order amount")
        return amount

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

    checkout_locks: dict[str, asyncio.Lock] = {}

    def checkout_lock(session_id: str) -> asyncio.Lock:
        return checkout_locks.setdefault(session_id, asyncio.Lock())

    contractor_router, fulfill_contractor_checkout, retrieve_contractor_checkout = create_contractor_router(
        store, require_service_api_key, sign_receipt, resolved_settings.base_url, checkout_lock,
    )
    app.include_router(contractor_router)
    app.include_router(create_niche_router(resolved_settings))
    app.include_router(create_manager_router(resolved_settings))
    app.include_router(create_research_router(resolved_settings))
    app.include_router(create_query_router(resolved_settings))
    app.include_router(create_websub_router(resolved_settings))

    @app.get("/niche-discovery/openapi.json", include_in_schema=False)
    def niche_openapi() -> dict[str, object]:
        document = app.openapi()
        paths = {path: value for path, value in document["paths"].items()
                 if path.startswith("/niche-discovery/v1/") and "/admin/" not in path}
        paths["/niche-discovery/v1/niches/{niche_id}/pay-per-call"] = {
            "get": {
                "operationId": "get_niche_pay_per_call",
                "summary": "Pay USD 0.25 in Base USDC for one full niche evaluation",
                "parameters": [{"name": "niche_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": {"description": "Full evaluation with gateway payment receipt"},
                              "402": {"description": "x402/MPP payment challenge"}},
            },
        }
        return {
            **document,
            "info": {"title": "Niche Discovery API", "version": "0.1.0",
                     "description": "Submit market needs and inspect evidence-linked niche hypotheses."},
            "paths": paths,
        }

    def contact_ip_hash(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for", "")
        address = forwarded.split(",", 1)[0].strip() or (
            request.client.host if request.client else "unknown"
        )
        secret = (
            resolved_settings.contact_ip_hash_secret
            or resolved_settings.receipt_signing_secret
            or "local-contact-rate-limit"
        )
        return hmac.new(secret.encode(), address.encode(), hashlib.sha256).hexdigest()

    def support_identity(
        *,
        customer_key: str | None,
        order_id: str | None,
        order_token: str | None,
    ) -> tuple[str | None, str | None]:
        customer = store.customer_for_key(customer_key)
        if customer is not None:
            return str(customer["customer_id"]), order_id
        if order_id and order_token:
            order = store.get_order_with_token(order_id, order_token)
            if order is not None:
                customer_id = order.get("customerId")
                return str(customer_id) if customer_id else None, order_id
        raise HTTPException(
            status_code=401,
            detail="Use X-Agentic-Customer-Key or a valid orderId with X-Agentic-Order-Token",
        )

    def require_ticket_access(
        ticket_id: str, support_token: str | None, customer_key: str | None
    ) -> dict[str, object]:
        ticket = store.support_ticket_for_token(ticket_id, support_token)
        if ticket is not None:
            return ticket
        customer = store.customer_for_key(customer_key)
        ticket = store.get_support_ticket(ticket_id)
        if (
            customer is not None
            and ticket is not None
            and ticket.get("customerId") == customer.get("customer_id")
        ):
            return ticket
        raise HTTPException(status_code=404, detail="Support ticket not found")

    def validate_public_webhook(value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise HTTPException(status_code=422, detail="Webhook target must be a public HTTPS URL")
        try:
            addresses = {
                item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443)
            }
        except socket.gaierror as error:
            raise HTTPException(status_code=422, detail="Webhook hostname could not be resolved") from error
        if not addresses or any(
            not ipaddress.ip_address(address).is_global for address in addresses
        ):
            raise HTTPException(status_code=422, detail="Webhook target must resolve to public addresses")
        return value

    @app.get("/healthz", tags=["operations"])
    def health() -> dict[str, str]:
        store.purge_contractor_intents()
        return {"status": "ok"}

    @app.get("/.well-known/glama.json", tags=["discovery"])
    def glama_domain_claim() -> dict[str, str]:
        """Publish Glama's public domain-ownership proof for this connector."""
        return {
            "$schema": "https://glama.ai/mcp/schemas/connector.json",
            "claim": "glama_claim_UOohXzLBOuUu_N1G6EmoqEJWd388-E7c",
        }

    @app.get("/readyz", tags=["operations"])
    def readiness(request: Request) -> dict[str, str]:
        if request.app.state.verification_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="OPENAI_API_KEY is not configured",
            )
        return {"status": "ready"}

    @app.post("/v1/contact/messages", status_code=201, tags=["contact"])
    async def create_contact_message(
        payload: ContactMessageRequest, request: Request, response: Response
    ) -> dict[str, str]:
        if payload.company:
            return {"messageId": f"msg_{secrets.token_hex(16)}", "status": "received"}
        email = payload.email.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise HTTPException(status_code=422, detail="A valid email address is required")

        ip_hash = contact_ip_hash(request)
        since = datetime.now(UTC) - timedelta(hours=1)
        if store.count_recent_contact_messages(ip_hash=ip_hash, since=since) >= max(
            1, resolved_settings.contact_rate_limit_per_hour
        ):
            raise HTTPException(
                status_code=429,
                detail="Too many messages. Please try again later.",
                headers={"Retry-After": "3600"},
            )

        record = store.create_contact_message(
            sender_name=payload.name.strip(),
            sender_email=email,
            subject=payload.subject.strip(),
            message_text=payload.message.strip(),
            ip_hash=ip_hash,
            user_agent=request.headers.get("user-agent"),
            service_id=payload.service_id,
        )
        message_id = str(record["messageId"])
        try:
            await asyncio.to_thread(
                send_contact_email,
                resolved_settings,
                message_id=message_id,
                sender_name=payload.name.strip(),
                sender_email=email,
                subject=payload.subject.strip(),
                message_text=payload.message.strip(),
                service_id=payload.service_id,
            )
        except Exception as error:
            store.update_contact_delivery(
                message_id=message_id, status="failed", error=str(error)[:500]
            )
            response.status_code = 202
            return {"messageId": message_id, "status": "saved"}
        store.update_contact_delivery(message_id=message_id, status="sent")
        return {"messageId": message_id, "status": "sent"}

    @app.post("/support/v1/tickets", status_code=201, tags=["support"])
    async def create_support_ticket(
        payload: SupportTicketCreateRequest,
        x_agentic_customer_key: str | None = Header(default=None),
        x_agentic_order_token: str | None = Header(default=None),
    ) -> dict[str, object]:
        if payload.priority not in {"low", "normal", "high", "urgent"}:
            raise HTTPException(status_code=422, detail="Unsupported priority")
        customer_id, order_id = support_identity(
            customer_key=x_agentic_customer_key,
            order_id=payload.order_id,
            order_token=x_agentic_order_token,
        )
        ticket, access_token = store.create_support_ticket(
            subject=payload.subject.strip(), message_text=payload.message.strip(),
            service_id=payload.service_id, customer_id=customer_id, order_id=order_id,
            priority=payload.priority,
        )
        try:
            await asyncio.to_thread(
                send_email, resolved_settings,
                recipient=resolved_settings.contact_recipient_email,
                subject=f"[Agent support] {payload.subject.strip()}",
                body=(f"Ticket: {ticket['ticketId']}\nService: {payload.service_id or 'Platform'}\n"
                      f"Order: {order_id or 'n/a'}\nPriority: {payload.priority}\n\n{payload.message.strip()}"),
            )
        except Exception:
            pass
        return {**ticket, "accessToken": access_token, "accessTokenShownOnce": True}

    @app.get("/support/v1/tickets/{ticket_id}", tags=["support"])
    def get_support_ticket(
        ticket_id: str,
        x_support_token: str | None = Header(default=None),
        x_agentic_customer_key: str | None = Header(default=None),
    ) -> dict[str, object]:
        return require_ticket_access(ticket_id, x_support_token, x_agentic_customer_key)

    @app.post("/support/v1/tickets/{ticket_id}/messages", status_code=201, tags=["support"])
    async def add_support_ticket_message(
        ticket_id: str,
        payload: SupportMessageRequest,
        x_support_token: str | None = Header(default=None),
        x_agentic_customer_key: str | None = Header(default=None),
    ) -> dict[str, object]:
        ticket = require_ticket_access(ticket_id, x_support_token, x_agentic_customer_key)
        message = store.add_support_message(
            ticket_id=ticket_id, author_type="customer", message_text=payload.message.strip()
        )
        try:
            await asyncio.to_thread(
                send_email, resolved_settings,
                recipient=resolved_settings.contact_recipient_email,
                subject=f"[Agent support update] {ticket['subject']}",
                body=f"Ticket: {ticket_id}\n\n{payload.message.strip()}",
            )
        except Exception:
            pass
        return message

    @app.get("/status/", response_class=HTMLResponse, include_in_schema=False)
    def public_status_page() -> str:
        return status_page_html()

    @app.get("/status/index.json", tags=["status"])
    def public_status_json() -> dict[str, object]:
        return store.status_document()

    @app.get("/status/feed.rss", tags=["status"])
    def public_status_feed() -> Response:
        return Response(content=status_rss(store.status_document()), media_type="application/rss+xml")

    @app.post("/status/v1/subscriptions", status_code=202, tags=["status"])
    async def create_status_subscription(payload: StatusSubscriptionRequest) -> dict[str, object]:
        if payload.channel not in {"email", "webhook"}:
            raise HTTPException(status_code=422, detail="channel must be email or webhook")
        verification_email = (payload.verification_email or payload.target).strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", verification_email):
            raise HTTPException(status_code=422, detail="A valid verification email is required")
        target = payload.target.strip()
        if payload.channel == "email":
            target = target.lower()
            if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", target):
                raise HTTPException(status_code=422, detail="A valid email target is required")
        else:
            target = validate_public_webhook(target)
        subscriber, token = store.create_status_subscriber(
            channel=payload.channel, target=target, verification_email=verification_email
        )
        try:
            await asyncio.to_thread(
                send_subscription_verification, resolved_settings,
                verification_email=verification_email, channel=payload.channel, token=token,
            )
        except Exception as error:
            raise HTTPException(
                status_code=503,
                detail="Subscription was saved, but confirmation email delivery is not configured yet.",
            ) from error
        return subscriber

    @app.get("/status/v1/subscriptions/verify", response_class=HTMLResponse, include_in_schema=False)
    def verify_status_subscription(token: Annotated[str, Query(min_length=20, max_length=200)]) -> str:
        if not store.verify_status_subscriber(token):
            raise HTTPException(status_code=404, detail="Confirmation link is invalid or already used")
        return "<h1>Status subscription confirmed</h1><p>You will receive Dream Workshop service updates.</p>"

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
                "homepage": "https://aisoup.net/web-evidence/",
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
        order_amount_microusd: str | None,
        customer_key: str | None,
        customer_reference: str | None,
    ) -> ClaimVerificationResult:
        require_service_api_key(authorization)
        settled_amount = settled_amount_microusd(tier, order_amount_microusd)
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
                    price_microusd=settled_amount,
                    payment_protocol=payment_protocol or "unknown",
                    order_token_hash=order_token_hash,
                    request_hash=request_hash,
                    customer_key=customer_key,
                    customer_reference=customer_reference,
                )
            except Exception as error:
                if payment_protocol != "stripe-checkout" or not store.reset_failed_order(order_id):
                    raise HTTPException(status_code=409, detail="Order identifier already exists") from error
                store.create_order(
                    order_id=order_id, service_id="web-evidence", tier=tier.id,
                    price_microusd=settled_amount, payment_protocol=payment_protocol,
                    order_token_hash=order_token_hash, request_hash=request_hash,
                    customer_key=customer_key, customer_reference=customer_reference,
                )
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
                    "amountMicrousd": settled_amount,
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
        order_amount_microusd: str | None,
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
            order_amount_microusd,
            customer_key,
            customer_reference,
        )

    def human_stripe_key() -> str:
        key = os.getenv("WEB_EVIDENCE_STRIPE_SECRET_KEY") or os.getenv("CONTRACTOR_STRIPE_SECRET_KEY", "")
        if not key.startswith(("rk_live_", "rk_test_", "sk_live_", "sk_test_")):
            raise HTTPException(status_code=503, detail="Stripe Checkout is not configured")
        return key

    async def human_stripe_request(method: str, path: str, data: dict[str, str] | None = None) -> dict[str, object]:
        async with httpx.AsyncClient(timeout=15) as client:
            try:
                result = await client.request(
                    method, f"https://api.stripe.com/v1/{path}", auth=(human_stripe_key(), ""), data=data,
                )
            except httpx.RequestError as error:
                raise HTTPException(status_code=503, detail="Stripe is temporarily unavailable") from error
        if result.status_code >= 400:
            raise HTTPException(status_code=503, detail="Stripe could not complete this request")
        return result.json()

    @app.post("/web-evidence/v1/checkout", tags=["web evidence human checkout"])
    async def web_evidence_checkout(payload: ClaimVerificationRequest) -> dict[str, str]:
        human_stripe_key()
        if payload.minimum_sources > 8 or payload.max_sources > 8:
            raise HTTPException(status_code=422, detail="The Standard report supports at most 8 cited sources")
        intent_id = store.create_web_evidence_intent(payload.model_dump_json(by_alias=True), 200)
        session = await human_stripe_request("POST", "checkout/sessions", {
            "mode": "payment",
            "payment_method_types[0]": "card",
            "line_items[0][price_data][currency]": "usd",
            "line_items[0][price_data][unit_amount]": "200",
            "line_items[0][price_data][product_data][name]": "Web Evidence Standard Report",
            "line_items[0][quantity]": "1",
            "client_reference_id": intent_id,
            "metadata[serviceId]": "web-evidence",
            "success_url": "https://aisoup.net/web-evidence/report/?session_id={CHECKOUT_SESSION_ID}",
            "cancel_url": "https://aisoup.net/web-evidence/",
        })
        session_id, url = session.get("id"), session.get("url")
        if not isinstance(session_id, str) or not isinstance(url, str) or not url.startswith("https://checkout.stripe.com/"):
            raise HTTPException(status_code=503, detail="Stripe did not return a valid Checkout session")
        store.bind_web_evidence_session(intent_id, session_id)
        return {"checkoutUrl": url, "priceUsd": "2.00"}

    async def fulfill_web_evidence_checkout(
        session_id: str, session: dict[str, object], request: Request, response: Response,
    ) -> dict[str, object]:
        intent_id = session.get("client_reference_id")
        if not isinstance(intent_id, str):
            raise HTTPException(status_code=404, detail="Unknown report")
        intent = store.get_web_evidence_intent(intent_id)
        if not intent or intent["stripe_session_id"] != session_id:
            raise HTTPException(status_code=404, detail="Unknown report")
        if (session.get("payment_status") != "paid"
                or session.get("currency") != "usd"
                or session.get("amount_total") != intent["price_cents"]
                or session.get("mode") != "payment"
                or session.get("livemode") != human_stripe_key().startswith(("rk_live_", "sk_live_"))):
            raise HTTPException(status_code=402, detail="Payment is not complete")
        order_id = f"ord_{intent_id[4:]}"
        existing = store.get_order(order_id)
        if existing and existing["status"] == "completed":
            result = store.get(str(existing["verificationId"]))
        else:
            payload = ClaimVerificationRequest.model_validate_json(intent["request_json"])
            result = await run_verification(
                next(item for item in tiers if item.id == "standard"), payload, request,
                intent_id, f"Bearer {resolved_settings.service_api_key}" if resolved_settings.service_api_key else None,
                response, order_id, None, "stripe-checkout", "2000000", None, None,
            )
            store.set_web_evidence_order(intent_id, order_id)
        if result is None:
            raise HTTPException(status_code=503, detail="Report is temporarily unavailable")
        return {"orderId": order_id, "report": result.model_dump(by_alias=True, mode="json")}

    @app.get("/web-evidence/v1/report", tags=["web evidence human checkout"])
    async def web_evidence_paid_report(
        request: Request, response: Response, session_id: str = Query(min_length=20, max_length=255),
    ) -> dict[str, object]:
        if not re.fullmatch(r"cs_(?:test|live)_[A-Za-z0-9]+", session_id):
            raise HTTPException(status_code=422, detail="Invalid Checkout session")
        async with checkout_lock(session_id):
            session = await human_stripe_request("GET", f"checkout/sessions/{session_id}")
            result = await fulfill_web_evidence_checkout(session_id, session, request, response)
        response.headers["Cache-Control"] = "private, no-store"
        return result

    async def retrieve_paid_checkout(service_id: str, session_id: str) -> dict[str, object]:
        if service_id == "contractor-check":
            session = await retrieve_contractor_checkout(session_id)
        else:
            session = await human_stripe_request("GET", f"checkout/sessions/{session_id}")
            session["id"] = session_id
        intent_id = session.get("client_reference_id")
        intent = (store.get_contractor_intent(intent_id) if service_id == "contractor-check"
                  else store.get_web_evidence_intent(intent_id)) if isinstance(intent_id, str) else None
        key = (os.getenv("CONTRACTOR_STRIPE_SECRET_KEY", "") if service_id == "contractor-check"
               else human_stripe_key())
        if (not intent or intent["stripe_session_id"] != session_id
                or session.get("metadata", {}).get("serviceId") != service_id
                or session.get("payment_status") != "paid"
                or session.get("currency") != "usd"
                or session.get("amount_total") != intent["price_cents"]
                or session.get("mode") != "payment"
                or session.get("livemode") != key.startswith(("rk_live_", "sk_live_"))):
            raise HTTPException(status_code=422, detail="Checkout session does not match a paid order")
        return session

    @app.post("/v1/stripe/checkout-webhook", include_in_schema=False)
    async def stripe_checkout_webhook(request: Request) -> dict[str, str]:
        secret = os.getenv("HUMAN_STRIPE_WEBHOOK_SECRET", "")
        if not secret.startswith("whsec_"):
            raise HTTPException(status_code=503, detail="Stripe webhook is not configured")
        body = await request.body()
        if len(body) > 262_144:
            raise HTTPException(status_code=413, detail="Stripe event is too large")
        try:
            event = verify_stripe_event(body, request.headers.get("stripe-signature"), secret)
        except InvalidStripeSignature as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if event.get("type") not in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}:
            return {"status": "ignored"}
        session_data = event.get("data", {}).get("object", {})
        session_id = session_data.get("id") if isinstance(session_data, dict) else None
        service_id = session_data.get("metadata", {}).get("serviceId") if isinstance(session_data, dict) else None
        if service_id not in {"web-evidence", "contractor-check"}:
            return {"status": "ignored"}
        if not isinstance(session_id, str) or not re.fullmatch(r"cs_(?:test|live)_[A-Za-z0-9]+", session_id):
            raise HTTPException(status_code=422, detail="Invalid Checkout session")
        session = await retrieve_paid_checkout(service_id, session_id)
        store.enqueue_stripe_fulfillment(session_id, service_id, str(session["client_reference_id"]))
        return {"status": "queued"}

    async def process_stripe_fulfillment() -> bool:
        job = store.claim_stripe_fulfillment()
        if job is None:
            return False
        session_id, service_id = job["stripe_session_id"], job["service_id"]
        try:
            async with checkout_lock(session_id):
                session = await retrieve_paid_checkout(service_id, session_id)
                if service_id == "contractor-check":
                    fulfill_contractor_checkout(session)
                else:
                    worker_request = Request({"type": "http", "app": app, "method": "POST", "path": "/v1/stripe/checkout-webhook", "headers": []})
                    await fulfill_web_evidence_checkout(session_id, session, worker_request, Response())
                store.finish_stripe_fulfillment(session_id)
        except Exception as error:
            logging.getLogger(__name__).exception("Stripe Checkout fulfillment failed for %s", session_id)
            store.retry_stripe_fulfillment(session_id, job["attempts"] + 1, type(error).__name__)
        return True

    app.state.process_stripe_fulfillment = process_stripe_fulfillment


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
        x_agentic_order_amount_microusd: str | None = Header(default=None),
        x_agentic_customer_key: str | None = Header(default=None),
        x_agentic_customer_reference: str | None = Header(default=None, max_length=255),
    ) -> ClaimVerificationResult:
        return await verification_endpoint(
            "quick", payload, request, idempotency_key, authorization, response,
            x_agentic_order_id, x_agentic_order_token_hash, x_agentic_payment_protocol,
            x_agentic_order_amount_microusd,
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
        x_agentic_order_amount_microusd: str | None = Header(default=None),
        x_agentic_customer_key: str | None = Header(default=None),
        x_agentic_customer_reference: str | None = Header(default=None, max_length=255),
    ) -> ClaimVerificationResult:
        return await verification_endpoint(
            "standard", payload, request, idempotency_key, authorization, response,
            x_agentic_order_id, x_agentic_order_token_hash, x_agentic_payment_protocol,
            x_agentic_order_amount_microusd,
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
        x_agentic_order_amount_microusd: str | None = Header(default=None),
        x_agentic_customer_key: str | None = Header(default=None),
        x_agentic_customer_reference: str | None = Header(default=None, max_length=255),
    ) -> ClaimVerificationResult:
        return await verification_endpoint(
            "deep", payload, request, idempotency_key, authorization, response,
            x_agentic_order_id, x_agentic_order_token_hash, x_agentic_payment_protocol,
            x_agentic_order_amount_microusd,
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
        x_agentic_order_amount_microusd: str | None = Header(default=None),
        x_agentic_customer_key: str | None = Header(default=None),
        x_agentic_customer_reference: str | None = Header(default=None, max_length=255),
    ) -> ClaimVerificationResult:
        return await verification_endpoint(
            "research", payload, request, idempotency_key, authorization, response,
            x_agentic_order_id, x_agentic_order_token_hash, x_agentic_payment_protocol,
            x_agentic_order_amount_microusd,
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

    @app.get("/v1/admin/contact-messages", tags=["admin"])
    def admin_contact_messages(
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        service_id: Annotated[str | None, Query(alias="serviceId")] = None,
        x_admin_key: str | None = Header(default=None),
    ) -> dict[str, object]:
        require_admin_key(x_admin_key)
        return {
            "messages": store.list_contact_messages(
                limit=limit, offset=offset, service_id=service_id
            ),
            "limit": limit,
            "offset": offset,
            "serviceId": service_id,
            "total": store.count_contact_messages(service_id=service_id),
        }

    @app.get("/v1/admin/support-tickets", tags=["admin"])
    def admin_support_tickets(
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        ticket_status: Annotated[str | None, Query(alias="status")] = None,
        x_admin_key: str | None = Header(default=None),
    ) -> dict[str, object]:
        require_admin_key(x_admin_key)
        return {
            "tickets": store.list_support_tickets(limit=limit, offset=offset, status=ticket_status),
            "limit": limit, "offset": offset, "status": ticket_status,
            "total": store.count_support_tickets(status=ticket_status),
        }

    @app.patch("/v1/admin/support-tickets/{ticket_id}", tags=["admin"])
    def admin_update_support_ticket(
        ticket_id: str,
        payload: SupportTicketUpdateRequest,
        x_admin_key: str | None = Header(default=None),
    ) -> dict[str, object]:
        require_admin_key(x_admin_key)
        if payload.status not in {"open", "pending", "resolved"}:
            raise HTTPException(status_code=422, detail="Unsupported ticket status")
        ticket = store.update_support_ticket(
            ticket_id=ticket_id, status=payload.status,
            message_text=payload.message.strip() if payload.message else None,
        )
        if ticket is None:
            raise HTTPException(status_code=404, detail="Support ticket not found")
        return ticket

    @app.post("/v1/admin/status/incidents", status_code=201, tags=["admin status"])
    async def admin_create_status_incident(
        payload: StatusIncidentCreateRequest,
        x_admin_key: str | None = Header(default=None),
    ) -> dict[str, object]:
        require_admin_key(x_admin_key)
        if payload.severity not in {"minor", "major", "maintenance"}:
            raise HTTPException(status_code=422, detail="Unsupported severity")
        incident = store.create_status_incident(
            title=payload.title.strip(), message_text=payload.message.strip(),
            severity=payload.severity, component_ids=payload.affected_components,
        )
        failures = await asyncio.to_thread(
            notify_status_subscribers, resolved_settings,
            subscribers=store.active_status_subscribers(), incident=incident,
        )
        return {**incident, "notificationFailures": failures}

    @app.patch("/v1/admin/status/incidents/{incident_id}", tags=["admin status"])
    async def admin_update_status_incident(
        incident_id: str,
        payload: StatusIncidentUpdateRequest,
        x_admin_key: str | None = Header(default=None),
    ) -> dict[str, object]:
        require_admin_key(x_admin_key)
        if payload.status not in {"investigating", "identified", "monitoring", "resolved"}:
            raise HTTPException(status_code=422, detail="Unsupported incident status")
        incident = store.update_status_incident(
            incident_id=incident_id, status=payload.status, message_text=payload.message.strip()
        )
        if incident is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        failures = await asyncio.to_thread(
            notify_status_subscribers, resolved_settings,
            subscribers=store.active_status_subscribers(), incident=incident,
        )
        return {**incident, "notificationFailures": failures}

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
