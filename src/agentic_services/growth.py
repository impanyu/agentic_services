"""Anonymous session funnel; browser observations never prove payment or identity."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
import logging
from functools import wraps
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from typing import Literal

SERVICES = {"web-evidence", "contractor-check", "niche-discovery"}
SOURCES = {"direct", "search", "social", "community", "email", "partner", "other"}
CAMPAIGNS = {"none", "writer-pilot", "developer-pilot", "contractor-pilot"}
STAGES = ("page_view", "sample_view", "checkout_attempt", "checkout_created", "checkout_failed", "payment_confirmed", "report_ready", "report_delivered", "report_view")
CLIENT_STAGES = {"page_view", "sample_view", "checkout_attempt", "report_view"}


def nonblocking_measurement(method):
    @wraps(method)
    def safe(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except sqlite3.Error:
            logging.getLogger(__name__).warning("Funnel database unavailable; commerce continues")
    return safe


class UsageEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: Literal["web-evidence", "contractor-check", "niche-discovery"]
    stage: Literal["page_view", "sample_view", "checkout_attempt", "report_view"]


class GrowthStore:
    def __init__(self, path: Path, secret: str):
        self.path, self.secret = path, secret
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS growth_sessions (
                    id TEXT NOT NULL, service TEXT NOT NULL, source TEXT NOT NULL,
                    campaign TEXT NOT NULL, cohort TEXT NOT NULL, created TEXT NOT NULL,
                    PRIMARY KEY(id,service)
                );
                CREATE TABLE IF NOT EXISTS growth_events (
                    service TEXT NOT NULL, stage TEXT NOT NULL, reference TEXT NOT NULL,
                    session_id TEXT, authority TEXT NOT NULL, created TEXT NOT NULL,
                    PRIMARY KEY(service,stage,reference)
                );
                CREATE INDEX IF NOT EXISTS growth_events_created ON growth_events(created);
                CREATE TABLE IF NOT EXISTS growth_checkouts (
                    intent_id TEXT PRIMARY KEY, service TEXT NOT NULL, session_id TEXT,
                    created TEXT NOT NULL
                );
            """)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def test_token(self) -> str:
        payload = f"{int(time.time()) + 3600}.{secrets.token_hex(12)}"
        signature = hmac.new(self.secret.encode(), ("growth-test:" + payload).encode(), hashlib.sha256).hexdigest()
        return payload + "." + signature

    def is_test(self, token: str) -> bool:
        try:
            expires, nonce, signature = token.split(".")
            payload = expires + "." + nonce
            expected = hmac.new(self.secret.encode(), ("growth-test:" + payload).encode(), hashlib.sha256).hexdigest()
            return bool(self.secret) and int(expires) > time.time() and hmac.compare_digest(signature, expected)
        except (ValueError, TypeError):
            return False

    def session(self, service: str, headers) -> str | None:
        identifier = headers.get("x-usage-session", "")
        if not re.fullmatch(r"[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}", identifier):
            return None
        # Raw random browser IDs are not retained. No IP, claim or URL query is stored.
        identifier = hmac.new(self.secret.encode(), ("growth-session:" + identifier).encode(), hashlib.sha256).hexdigest()
        source, campaign = headers.get("x-usage-source", "direct"), headers.get("x-usage-campaign", "none")
        cohort = "internal" if self.is_test(headers.get("x-usage-test", "")) else "unmarked"
        now = datetime.now(UTC).isoformat()
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO growth_sessions VALUES(?,?,?,?,?,?)", (identifier, service, source if source in SOURCES else "other", campaign if campaign in CAMPAIGNS else "none", cohort, now))
            # A validated operator marker also excludes earlier events in this session.
            if cohort == "internal":
                db.execute("UPDATE growth_sessions SET cohort='internal' WHERE id=? AND service=?", (identifier, service))
        return identifier

    def record(self, service: str, stage: str, reference: str, session_id: str | None, authority: str = "server"):
        if service not in SERVICES or stage not in STAGES:
            raise ValueError("Invalid funnel event")
        now = datetime.now(UTC)
        cutoff = (now - timedelta(days=30)).isoformat()
        with self.connect() as db:
            db.execute("DELETE FROM growth_events WHERE created<?", (cutoff,))
            db.execute("DELETE FROM growth_checkouts WHERE created<?", (cutoff,))
            db.execute("DELETE FROM growth_sessions WHERE created<?", (cutoff,))
            db.execute("INSERT OR IGNORE INTO growth_events VALUES(?,?,?,?,?,?)", (service, stage, reference, session_id, authority, now.isoformat()))

    @nonblocking_measurement
    def checkout(self, service: str, intent_id: str, headers):
        session = self.session(service, headers)
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO growth_checkouts VALUES(?,?,?,?)", (intent_id, service, session, datetime.now(UTC).isoformat()))
        self.record(service, "checkout_created", intent_id, session)

    @nonblocking_measurement
    def outcome(self, service: str, intent_id: str, stage: str):
        with self.connect() as db:
            row = db.execute("SELECT session_id FROM growth_checkouts WHERE intent_id=? AND service=?", (intent_id, service)).fetchone()
        self.record(service, stage, intent_id, row[0] if row else None)

    def summary(self, days: int, service: str | None) -> dict:
        since = (datetime.now(UTC) - timedelta(days=min(days or 30, 30))).isoformat()
        with self.connect() as db:
            rows = db.execute("""SELECT e.service,e.stage,e.authority,
                COALESCE(s.cohort,'unattributed') cohort,COALESCE(s.source,'unknown') source,
                COALESCE(s.campaign,'none') campaign,COUNT(*) events,
                COUNT(DISTINCT e.session_id) sessions
                FROM growth_events e LEFT JOIN growth_sessions s ON s.id=e.session_id AND s.service=e.service
                WHERE e.created>=? AND (? IS NULL OR e.service=?)
                GROUP BY e.service,e.stage,e.authority,cohort,source,campaign""", (since, service, service)).fetchall()
            first = db.execute("SELECT MIN(created) FROM growth_events").fetchone()[0]
        return {"since": since, "firstRecordedAt": first, "retentionDays": 30,
                "rows": [dict(r) for r in rows],
                "interpretation": "Sessions are approximate browser visits, not people. Unmarked does not mean external or human. Browser observations cannot prove payment; server-confirmed events do. report_ready means generated; report_delivered means served by the report endpoint; report_view is a browser rendering observation. Historical orders are not backfilled."}


def create_growth_router(store: GrowthStore, require_admin, require_service):
    router = APIRouter()
    windows: dict[str, tuple[int, int]] = {}

    @router.post("/v1/usage/events", status_code=202, include_in_schema=False)
    async def record_event(request: Request, authorization: str | None = Header(default=None)):
        require_service(authorization)
        if request.headers.get("origin") not in {"https://aisoup.net", "https://www.aisoup.net"}:
            raise HTTPException(403, "Unsupported origin")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 1024:
                raise HTTPException(413, "Event too large")
        try:
            event = UsageEvent.model_validate_json(body)
        except ValueError:
            raise HTTPException(422, "Invalid usage event") from None
        # Short-lived in-memory rate limit, without retaining the address.
        address = request.headers.get("x-forwarded-for", "unknown")
        key = hmac.new(store.secret.encode(), address.encode(), hashlib.sha256).hexdigest()
        minute = int(time.time() // 60)
        if len(windows) >= 5000:
            for old in [k for k, v in windows.items() if v[0] != minute]:
                windows.pop(old, None)
        if len(windows) >= 5000 and key not in windows:
            raise HTTPException(429, "Usage collection busy")
        previous, count = windows.get(key, (minute, 0))
        count = count + 1 if previous == minute else 1
        windows[key] = (minute, count)
        if count > 60:
            raise HTTPException(429, "Too many events")
        session = store.session(event.service, request.headers)
        if session is None:
            raise HTTPException(422, "Missing anonymous session")
        store.record(event.service, event.stage, session, session, "browser")
        return {"status": "recorded"}

    @router.get("/v1/admin/funnel", include_in_schema=False)
    def summary(days: int = Query(default=30, ge=0, le=3650), serviceId: str | None = None, x_admin_key: str | None = Header(default=None)):
        require_admin(x_admin_key)
        return store.summary(days, serviceId)

    @router.post("/v1/admin/funnel/test-token", include_in_schema=False)
    def test_token(x_admin_key: str | None = Header(default=None)):
        require_admin(x_admin_key)
        return {"token": store.test_token(), "expiresInSeconds": 3600}

    return router
