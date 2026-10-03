from __future__ import annotations

import hashlib
import hmac
import json
import time


class InvalidStripeSignature(ValueError):
    pass


def verify_stripe_event(body: bytes, signature: str | None, secret: str, *, now: int | None = None) -> dict:
    """Verify Stripe's signature over the original request bytes before parsing JSON."""
    if not secret.startswith("whsec_") or not signature:
        raise InvalidStripeSignature("Missing Stripe webhook signature")
    parts = [part.strip().split("=", 1) for part in signature.split(",") if "=" in part]
    timestamps = [value for key, value in parts if key == "t"]
    signatures = [value for key, value in parts if key == "v1"]
    if len(timestamps) != 1 or not signatures or not timestamps[0].isdigit():
        raise InvalidStripeSignature("Malformed Stripe webhook signature")
    timestamp = int(timestamps[0])
    if abs((now if now is not None else int(time.time())) - timestamp) > 300:
        raise InvalidStripeSignature("Expired Stripe webhook signature")
    expected = hmac.new(secret.encode(), str(timestamp).encode() + b"." + body, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, value) for value in signatures):
        raise InvalidStripeSignature("Invalid Stripe webhook signature")
    try:
        event = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidStripeSignature("Invalid Stripe event JSON") from error
    if not isinstance(event, dict):
        raise InvalidStripeSignature("Invalid Stripe event")
    return event
