from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
from datetime import UTC, datetime
from typing import Callable

import httpx
from fastapi import APIRouter, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field

from .contractor_check import LicenseNotFoundError, SourceUnavailableError, fetch_license_report, validate_license_number
from .storage import VerificationStore


SERVICE_ID = "contractor-check"
HUMAN_PRICE_CENTS = 1900
AGENT_PRICE_MICROUSD = 1_000_000


class LicenseRequest(BaseModel):
    license_number: str = Field(alias="licenseNumber", min_length=1, max_length=8)


def create_contractor_router(
    store: VerificationStore,
    require_api_key: Callable[[str | None], None],
    sign_receipt: Callable[[dict[str, object]], str],
    base_url: str,
) -> APIRouter:
    router = APIRouter(tags=["contractor check"])
    base_url = base_url.rstrip("/")

    async def lookup(number: str) -> dict[str, object]:
        try:
            return await fetch_license_report(number)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except LicenseNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except SourceUnavailableError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    def stripe_key() -> str:
        key = os.getenv("STRIPE_SECRET_KEY", "")
        if not key.startswith(("sk_live_", "sk_test_")):
            raise HTTPException(status_code=503, detail="Stripe Checkout is not configured")
        return key

    async def stripe_request(method: str, path: str, *, data: dict[str, str] | None = None) -> dict[str, object]:
        key = stripe_key()
        async with httpx.AsyncClient(timeout=15) as client:
            try:
                result = await client.request(
                    method, f"https://api.stripe.com/v1/{path}",
                    auth=(key, ""), data=data,
                )
            except httpx.RequestError as error:
                raise HTTPException(status_code=503, detail="Stripe is temporarily unavailable") from error
        if result.status_code >= 400:
            raise HTTPException(status_code=503, detail="Stripe could not complete this request")
        return result.json()

    def record_order(order_id: str, report: dict[str, object], amount_microusd: int, protocol: str) -> dict[str, object]:
        existing = store.get_order(order_id)
        if existing and existing["status"] == "completed":
            return report
        report_bytes = json.dumps(report, separators=(",", ":"), sort_keys=True).encode()
        report_hash = hashlib.sha256(report_bytes).hexdigest()
        if not existing:
            try:
                store.create_order(
                    order_id=order_id, service_id=SERVICE_ID, tier="license-preflight",
                    price_microusd=amount_microusd, payment_protocol=protocol,
                    order_token_hash=None, request_hash=hashlib.sha256(str(report["licenseNumber"]).encode()).hexdigest(),
                    customer_key=None, customer_reference=None,
                )
            except sqlite3.IntegrityError:
                existing = store.get_order(order_id)
                if not existing:
                    raise
        receipt = {
            "receiptId": f"rcpt_{secrets.token_hex(16)}", "orderId": order_id,
            "serviceId": SERVICE_ID, "amountMicrousd": amount_microusd,
            "currency": "USD", "paymentProtocol": protocol,
            "resultSha256": report_hash, "issuedAt": datetime.now(UTC).isoformat(),
            "signatureAlgorithm": "hmac-sha256",
        }
        store.complete_order(
            order_id=order_id,
            values={
                "verification_id": None, "provider_response_id": None, "model": None,
                "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0,
                "web_search_calls": 0, "model_cost_microusd": 0,
                "search_cost_microusd": 0, "total_cost_microusd": 0,
            },
            receipt=receipt, signature=sign_receipt(receipt),
        )
        return report

    @router.post("/contractor-check/v1/checkout")
    async def create_checkout(payload: LicenseRequest, authorization: str | None = Header(default=None)) -> dict[str, str]:
        require_api_key(authorization)
        stripe_key()
        report = await lookup(payload.license_number)
        number = validate_license_number(payload.license_number)
        intent_id = store.create_contractor_intent(number, report, HUMAN_PRICE_CENTS)
        success_url = f"{base_url}/contractor-check/report/?session_id={{CHECKOUT_SESSION_ID}}"
        session = await stripe_request("POST", "checkout/sessions", data={
            "mode": "payment",
            "payment_method_types[0]": "card",
            "line_items[0][price_data][currency]": "usd",
            "line_items[0][price_data][unit_amount]": str(HUMAN_PRICE_CENTS),
            "line_items[0][price_data][product_data][name]": "California C-10 License Preflight Report",
            "line_items[0][quantity]": "1",
            "client_reference_id": intent_id,
            "metadata[serviceId]": SERVICE_ID,
            "metadata[licenseNumber]": number,
            "success_url": success_url,
            "cancel_url": f"{base_url}/contractor-check/",
        })
        session_id, url = session.get("id"), session.get("url")
        if not isinstance(session_id, str) or not isinstance(url, str) or not url.startswith("https://checkout.stripe.com/"):
            raise HTTPException(status_code=503, detail="Stripe did not return a valid Checkout session")
        store.bind_contractor_session(intent_id, session_id)
        return {"checkoutUrl": url, "priceUsd": "19.00"}

    @router.get("/contractor-check/v1/report")
    async def get_paid_report(response: Response, session_id: str = Query(min_length=20, max_length=255)) -> dict[str, object]:
        if not re.fullmatch(r"cs_(?:test|live)_[A-Za-z0-9]+", session_id):
            raise HTTPException(status_code=422, detail="Invalid Checkout session")
        session = await stripe_request("GET", f"checkout/sessions/{session_id}")
        intent_id = session.get("client_reference_id")
        if not isinstance(intent_id, str):
            raise HTTPException(status_code=404, detail="Unknown report")
        intent = store.get_contractor_intent(intent_id)
        if not intent or intent["stripe_session_id"] != session_id:
            raise HTTPException(status_code=404, detail="Unknown report")
        if (session.get("payment_status") != "paid"
                or session.get("currency") != "usd"
                or session.get("amount_total") != intent["price_cents"]
                or session.get("mode") != "payment"
                or session.get("livemode") != stripe_key().startswith("sk_live_")):
            raise HTTPException(status_code=402, detail="Payment is not complete")
        report = json.loads(intent["report_json"])
        order_id = f"ord_{intent_id[4:]}"
        record_order(order_id, report, int(intent["price_cents"]) * 10_000, "stripe-checkout")
        store.set_contractor_order(intent_id, order_id)
        response.headers["Cache-Control"] = "private, no-store"
        return {"orderId": order_id, "report": report}

    @router.post("/contractor-check/v1/check")
    async def agent_check(
        payload: LicenseRequest, response: Response,
        authorization: str | None = Header(default=None),
        x_agentic_order_id: str | None = Header(default=None),
        x_agentic_order_amount_microusd: str | None = Header(default=None),
        x_agentic_payment_protocol: str | None = Header(default=None),
    ) -> dict[str, object]:
        require_api_key(authorization)
        if not x_agentic_order_id or not re.fullmatch(r"ord_[a-f0-9]{32}", x_agentic_order_id):
            raise HTTPException(status_code=403, detail="Paid gateway order required")
        if x_agentic_order_amount_microusd != str(AGENT_PRICE_MICROUSD):
            raise HTTPException(status_code=403, detail="Incorrect paid amount")
        report = await lookup(payload.license_number)
        record_order(x_agentic_order_id, report, AGENT_PRICE_MICROUSD, x_agentic_payment_protocol or "unknown")
        response.headers["X-Agentic-Receipt-Id"] = str(store.get_order(x_agentic_order_id)["receiptId"])
        return report

    return router
