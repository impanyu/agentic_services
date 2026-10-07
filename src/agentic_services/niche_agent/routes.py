from __future__ import annotations

import hashlib
import hmac
import json
import os
import time

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from ..config import Settings
from .store import ManagerStore


class WakeRequest(BaseModel):
    operation_id: str = Field(min_length=1, max_length=160)
    objective: str = Field(min_length=20, max_length=2000)


class SubscriptionRequest(BaseModel):
    identifier: str
    source: str
    secret_env: str
    interval_seconds: int = 21600
    enabled: bool = True


def create_manager_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix='/niche-discovery/v1', include_in_schema=False)
    store = ManagerStore(settings.database_path)

    def admin(key: str | None):
        expected = settings.admin_api_key
        if not expected:
            raise HTTPException(503, 'Admin access is not configured')
        if not key or not hmac.compare_digest(key, expected):
            raise HTTPException(401, 'Valid X-Admin-Key required')

    @router.get('/admin/manager')
    def status(x_admin_key: str | None = Header(default=None)):
        admin(x_admin_key)
        return {**store.status(), 'enabled': os.getenv('NICHE_AGENT_ENABLED') == '1',
                'model': os.getenv('NICHE_AGENT_MODEL', 'gpt-6-sol')}

    @router.post('/admin/manager/wake', status_code=202)
    def wake(body: WakeRequest, x_admin_key: str | None = Header(default=None)):
        admin(x_admin_key)
        return {'eventId': store.enqueue('operator.message', {'objective': body.objective}, 'operator:' + body.operation_id)}

    @router.post('/admin/manager/subscriptions')
    def subscription(body: SubscriptionRequest, x_admin_key: str | None = Header(default=None)):
        admin(x_admin_key)
        try:
            return store.subscribe(None, body.identifier, body.source, body.secret_env,
                                   body.interval_seconds, body.enabled)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @router.post('/source-events/{identifier}', status_code=202)
    async def callback(identifier: str, request: Request):
        subscription = next((s for s in store.subscriptions() if s['id'] == identifier and s['enabled']), None)
        if not subscription:
            raise HTTPException(404, 'Subscription not found')
        secret = os.getenv(subscription['secret_env'])
        if not secret:
            raise HTTPException(503, 'Callback authentication is not configured')
        raw = bytearray()
        async for part in request.stream():
            raw.extend(part)
            if len(raw) > 65536:
                raise HTTPException(413, 'Event exceeds 64 KiB')
        body = bytes(raw)
        github_signature = request.headers.get('x-hub-signature-256')
        if github_signature and subscription['source'] == 'github':
            expected = 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(github_signature, expected):
                raise HTTPException(401, 'Invalid source signature')
            delivery = request.headers.get('x-github-delivery', '')
            event = request.headers.get('x-github-event', '')
            if not delivery or len(delivery) > 160 or event not in {'issues', 'issue_comment', 'ping'}:
                raise HTTPException(422, 'Unsupported GitHub delivery')
            try:
                payload = json.loads(body)
            except ValueError:
                raise HTTPException(422, 'Invalid event JSON') from None
            allowed = {s.strip() for s in os.getenv('NICHE_GITHUB_REPOSITORIES', '').split(',') if s.strip()}
            if event != 'ping' and (not isinstance(payload, dict) or payload.get('repository', {}).get('full_name') not in allowed):
                raise HTTPException(403, 'Repository outside configured source scope')
        else:
            timestamp = request.headers.get('x-niche-timestamp', '')
            try:
                if abs(time.time() - int(timestamp)) > 300:
                    raise ValueError()
            except ValueError:
                raise HTTPException(401, 'Missing or expired timestamp') from None
            expected = hmac.new(secret.encode(), timestamp.encode() + b'.' + body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(request.headers.get('x-niche-signature', ''), expected):
                raise HTTPException(401, 'Invalid source signature')
            delivery = request.headers.get('x-niche-delivery', '')
            event = 'source.changed'
            if not delivery or len(delivery) > 160:
                raise HTTPException(422, 'Stable delivery ID required')
        # Persist references, not arbitrary callback text. Agent retrieves permitted
        # evidence through the registered adapter; the callback cannot inject evidence.
        event_id = store.enqueue('source.callback', {'source': subscription['source'],
            'subscription': identifier, 'event': event, 'delivery': delivery},
            'callback:' + identifier + ':' + delivery)
        return {'eventId': event_id, 'accepted': True}

    return router
