"""Evidence-first niche discovery MVP.

Collected candidates are private until editorial review.
External collection uses explicitly configured source adapters.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
import uuid
import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .config import Settings
from .contact import send_email
from .stripe_webhook import InvalidStripeSignature, verify_stripe_event


def now() -> str:
    return datetime.now(UTC).isoformat()


class NicheDraft(BaseModel):
    title: Annotated[str, Field(min_length=8, max_length=180)]
    problem: Annotated[str, Field(min_length=30, max_length=2000)]
    buyer: Annotated[str, Field(min_length=3, max_length=180)]
    category: Annotated[str, Field(min_length=2, max_length=80)]
    solution_hypothesis: Annotated[str, Field(min_length=20, max_length=2000)]
    pain: Annotated[int, Field(ge=0, le=5)]
    frequency: Annotated[int, Field(ge=0, le=5)]
    willingness_to_pay: Annotated[int, Field(ge=0, le=5)]
    reachable_buyers: Annotated[int, Field(ge=0, le=5)]
    feasibility: Annotated[int, Field(ge=0, le=5)]
    competition_gap: Annotated[int, Field(ge=0, le=5)]
    evaluation_notes: Annotated[str, Field(min_length=30, max_length=4000)]
    signal_ids: Annotated[list[str], Field(min_length=1, max_length=100)]
    publish: bool = False


WEIGHTS = {
    "pain": 25, "frequency": 20, "willingness_to_pay": 20,
    "reachable_buyers": 15, "feasibility": 10, "competition_gap": 10,
}


class NicheStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS niche_signals (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, text TEXT NOT NULL,
                    audience TEXT, source_url TEXT, source_domain TEXT NOT NULL,
                    external_id TEXT UNIQUE, origin TEXT NOT NULL,
                    observed_at TEXT NOT NULL, created_at TEXT NOT NULL,
                    moderation TEXT NOT NULL DEFAULT 'pending', reporter_hash TEXT
                );
                CREATE INDEX IF NOT EXISTS niche_signals_created ON niche_signals(created_at);
                CREATE TABLE IF NOT EXISTS niches (
                    id TEXT PRIMARY KEY, draft_json TEXT NOT NULL, score INTEGER NOT NULL,
                    published INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS niche_links (
                    niche_id TEXT NOT NULL, signal_id TEXT NOT NULL,
                    PRIMARY KEY(niche_id, signal_id)
                );
                CREATE TABLE IF NOT EXISTS niche_subscriptions (
                    stripe_subscription_id TEXT PRIMARY KEY,
                    stripe_customer_id TEXT NOT NULL,
                    email TEXT NOT NULL,
                    status TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    notified INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS niche_collection_runs (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, status TEXT NOT NULL,
                    result_json TEXT NOT NULL, finished_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS niche_collection_runs_source_time
                    ON niche_collection_runs(source, finished_at DESC);
            """)

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def ingest_github_issue(self, item: dict, repository: str) -> bool:
        external_id = f"github:{item['id']}"
        title = str(item.get("title") or "")[:300]
        body = str(item.get("body") or "")[:1400]
        body = re.sub(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", "[email removed]", body, flags=re.IGNORECASE)
        text = f"{title}\n\n{body}".strip()[:1800]
        if len(text) < 25:
            return False
        with self.connect() as db:
            result = db.execute(
                "INSERT OR IGNORE INTO niche_signals(id,kind,text,audience,source_url,source_domain,external_id,origin,observed_at,created_at,moderation) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (f"sig_{uuid.uuid4().hex}", "complaint", text, repository,
                 item["html_url"], "github.com", external_id, "github_issue",
                 item.get("created_at") or now(), now(), "pending"),
            )
            return result.rowcount == 1

    def ingest_external_signal(self, *, external_id: str, origin: str, kind: str,
                               text: str, source_url: str, observed_at: str,
                               audience: str | None = None) -> bool:
        """Queue a link-backed candidate; source text is never published verbatim."""
        parsed = urlparse(source_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        clean = re.sub(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", "[email removed]", text, flags=re.IGNORECASE)
        clean = re.sub(r"\s+", " ", clean).strip()[:500]
        if len(clean) < 25:
            return False
        with self.connect() as db:
            result = db.execute(
                "INSERT OR IGNORE INTO niche_signals(id,kind,text,audience,source_url,source_domain,external_id,origin,observed_at,created_at,moderation) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (f"sig_{uuid.uuid4().hex}", kind, clean, audience, source_url,
                 parsed.hostname.lower(), external_id, origin, observed_at, now(), "pending"),
            )
            return result.rowcount == 1

    def signals(self, limit: int = 100) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT id,kind,text,audience,source_url,source_domain,origin,observed_at,moderation FROM niche_signals ORDER BY created_at DESC LIMIT ?", (limit,))]

    def record_collection_run(self, source: str, status: str, result: dict) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO niche_collection_runs VALUES(?,?,?,?,?)",
                       (f"run_{uuid.uuid4().hex}", source, status, json.dumps(result), now()))
            db.execute("""DELETE FROM niche_collection_runs WHERE id IN (
                SELECT id FROM niche_collection_runs WHERE source=?
                ORDER BY finished_at DESC LIMIT -1 OFFSET 100
            )""", (source,))

    def collection_status(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("""SELECT source,status,result_json,finished_at FROM niche_collection_runs r
                WHERE finished_at=(SELECT MAX(finished_at) FROM niche_collection_runs WHERE source=r.source)
                ORDER BY source""").fetchall()
        return [{"source": row["source"], "status": row["status"],
                 "result": json.loads(row["result_json"]), "finishedAt": row["finished_at"]}
                for row in rows]

    def save_niche(self, draft: NicheDraft) -> dict:
        ids = list(dict.fromkeys(draft.signal_ids))
        with self.connect() as db:
            existing = db.execute(f"SELECT id,source_domain FROM niche_signals WHERE id IN ({','.join('?' for _ in ids)})", ids).fetchall()
            if len(existing) != len(ids):
                raise HTTPException(status_code=422, detail="Unknown signal id")
            domains = {row["source_domain"] for row in existing}
            if draft.publish and (len(ids) < 3 or len(domains) < 2):
                raise HTTPException(status_code=422, detail="Publishing requires at least 3 signals from 2 source domains")
            score = round(sum(getattr(draft, name) * weight / 5 for name, weight in WEIGHTS.items()))
            niche_id = f"niche_{uuid.uuid4().hex}"
            db.execute("INSERT INTO niches VALUES(?,?,?,?,?,?)", (niche_id, draft.model_dump_json(), score, int(draft.publish), now(), now()))
            db.executemany("INSERT INTO niche_links VALUES(?,?)", [(niche_id, signal_id) for signal_id in ids])
            db.execute(f"UPDATE niche_signals SET moderation='accepted' WHERE id IN ({','.join('?' for _ in ids)})", ids)
        return self.niche(niche_id, include_private=True)

    def niche(self, niche_id: str, *, include_private: bool = False) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM niches WHERE id=?", (niche_id,)).fetchone()
            if not row or (not include_private and not row["published"]):
                raise HTTPException(status_code=404, detail="Niche not found")
            draft = json.loads(row["draft_json"])
            signals = [dict(s) for s in db.execute("""SELECT s.id,s.kind,s.source_url,s.source_domain,s.origin,s.observed_at
                FROM niche_signals s JOIN niche_links l ON l.signal_id=s.id WHERE l.niche_id=? ORDER BY s.observed_at DESC""", (niche_id,))]
            for signal in signals:
                signal["sourceAttribution"] = (
                    "GDELT Project — https://www.gdeltproject.org/"
                    if signal["origin"] == "gdelt_news" else None
                )
            manager_metadata = {}
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='manager_revision_log'").fetchone():
                revision = db.execute('SELECT revision,metadata FROM manager_revision_log WHERE niche_id=? ORDER BY revision DESC LIMIT 1', (niche_id,)).fetchone()
                if revision:
                    metadata = json.loads(revision['metadata'])
                    manager_metadata = {'revision': revision['revision'], 'confidence': metadata['confidence'],
                                        'counterevidence': metadata['counterevidence'], 'assessmentRationale': metadata['rationale']}
            dates = [datetime.fromisoformat(s["observed_at"].replace("Z", "+00:00")) for s in signals]
            today = datetime.now(UTC)
            recent = sum(d >= today - timedelta(days=30) for d in dates)
            prior = sum(today - timedelta(days=60) <= d < today - timedelta(days=30) for d in dates)
            return {
                "id": niche_id, "title": draft["title"], "problem": draft["problem"],
                "buyer": draft["buyer"], "category": draft["category"],
                "solutionHypothesis": draft["solution_hypothesis"],
                "evaluationNotes": draft["evaluation_notes"], "score": row["score"],
                "scoreType": "editorial_hypothesis", "scoreWeights": WEIGHTS,
                "dimensions": {name: draft[name] for name in WEIGHTS},
                "signalCount": len(signals), "sourceDomainCount": len({s["source_domain"] for s in signals}),
                "recent30Days": recent, "previous30Days": prior,
                "trend": "insufficient_history" if recent + prior < 10 else ("rising" if recent > prior * 1.5 else "falling" if prior > recent * 1.5 else "stable"),
                "forecast": None, "forecastStatus": "not_available_without_longitudinal_history",
                "evidence": signals, "updatedAt": row["updated_at"], **manager_metadata,
            }

    def list_niches(self, query: str, category: str | None, sort: str, limit: int) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT id,draft_json,score FROM niches WHERE published=1 ORDER BY updated_at DESC").fetchall()
        matches = []
        for row in rows:
            draft = json.loads(row["draft_json"])
            searchable = " ".join(str(draft[k]) for k in ("title", "problem", "buyer", "category", "solution_hypothesis")).lower()
            if query.lower() not in searchable or (category and draft["category"].lower() != category.lower()):
                continue
            matches.append({"id": row["id"], "title": draft["title"], "buyer": draft["buyer"], "category": draft["category"], "score": row["score"], "scoreType": "editorial_hypothesis"})
        if sort == "score":
            matches.sort(key=lambda item: item["score"], reverse=True)
        return matches[:limit]

    def published_count(self) -> int:
        with self.connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM niches WHERE published=1").fetchone()[0])

    def preview(self, niche_id: str) -> dict:
        niche = self.niche(niche_id)
        return {key: niche[key] for key in ("id", "title", "buyer", "category", "score", "scoreType", "signalCount")}

    def upsert_subscription(self, subscription_id: str, customer_id: str, email: str,
                            status: str, token_hash: str) -> bool:
        with self.connect() as db:
            row = db.execute("SELECT notified FROM niche_subscriptions WHERE stripe_subscription_id=?", (subscription_id,)).fetchone()
            db.execute("""INSERT INTO niche_subscriptions
                (stripe_subscription_id,stripe_customer_id,email,status,token_hash,updated_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(stripe_subscription_id) DO UPDATE SET
                stripe_customer_id=excluded.stripe_customer_id,email=excluded.email,
                status=excluded.status,token_hash=excluded.token_hash,updated_at=excluded.updated_at""",
                (subscription_id, customer_id, email, status, token_hash, now()))
            return not row or not row["notified"]

    def mark_notified(self, subscription_id: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE niche_subscriptions SET notified=1 WHERE stripe_subscription_id=?", (subscription_id,))

    def update_subscription_status(self, subscription_id: str, status: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE niche_subscriptions SET status=?,updated_at=? WHERE stripe_subscription_id=?", (status, now(), subscription_id))

    def active_for_token(self, token: str) -> bool:
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.connect() as db:
            row = db.execute("SELECT status FROM niche_subscriptions WHERE token_hash=?", (digest,)).fetchone()
            return bool(row and row["status"] == "active")

    def customer_for_token(self, token: str) -> str | None:
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.connect() as db:
            row = db.execute("SELECT stripe_customer_id FROM niche_subscriptions WHERE token_hash=?", (digest,)).fetchone()
            return str(row["stripe_customer_id"]) if row else None


async def collect_configured_github(store: NicheStore) -> dict:
    """Collect a bounded set of issue candidates from explicitly approved repos."""
    repositories = [s.strip() for s in os.getenv("NICHE_GITHUB_REPOSITORIES", "").split(",") if s.strip()]
    if not repositories:
        return {"repositories": 0, "signalsAdded": 0, "status": "not_configured"}
    if len(repositories) > 20 or any(not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) for repo in repositories):
        raise ValueError("Invalid GitHub repository configuration")
    added = 0
    async with httpx.AsyncClient(timeout=15, headers={"Accept": "application/vnd.github+json", "User-Agent": "DreamWorkshop-NicheDiscovery/0.1"}) as client:
        for repo in repositories:
            response = await client.get(f"https://api.github.com/repos/{repo}/issues", params={"state": "open", "per_page": 50, "sort": "created", "direction": "desc"})
            response.raise_for_status()
            for item in response.json():
                if "pull_request" not in item:
                    added += store.ingest_github_issue(item, repo)
    return {"repositories": len(repositories), "signalsAdded": added, "status": "pending_review"}


async def collect_hacker_news(store: NicheStore) -> dict:
    """Sample recent Ask HN questions through the official public API."""
    added = 0
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "DreamWorkshop-NicheDiscovery/0.1"}) as client:
        response = await client.get("https://hacker-news.firebaseio.com/v0/askstories.json")
        response.raise_for_status()
        item_ids = response.json()[:30]
        for item_id in item_ids:
            if not isinstance(item_id, int):
                continue
            item_response = await client.get(f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json")
            item_response.raise_for_status()
            item = item_response.json()
            if not isinstance(item, dict) or item.get("deleted") or item.get("dead") or item.get("type") != "story":
                continue
            title = str(item.get("title") or "")
            if not re.search(r"\b(recommend\w*|alternative\w*|tool\w*|software|service\w*|problem\w*|struggl\w*|need\w*|looking for|how do you|wish\w*|no longer|price increase|broken|gets me|what did)\b", title, re.IGNORECASE):
                continue
            added += store.ingest_external_signal(
                external_id=f"hackernews:{item_id}", origin="hacker_news",
                kind="question", text=title,
                source_url=f"https://news.ycombinator.com/item?id={item_id}",
                observed_at=datetime.fromtimestamp(item.get("time", 0), UTC).isoformat(),
            )
    return {"itemsChecked": len(item_ids), "signalsAdded": added, "status": "pending_review"}


async def collect_gdelt_news(store: NicheStore) -> dict:
    """Queue news headlines from GDELT's public article index, not article bodies."""
    query = os.getenv("NICHE_GDELT_QUERY", "").strip()
    if not query:
        return {"status": "not_configured", "signalsAdded": 0}
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "DreamWorkshop-NicheDiscovery/0.1"}) as client:
        response = await client.get("https://api.gdeltproject.org/api/v2/doc/doc", params={
            "query": query, "mode": "artlist", "format": "json", "maxrecords": 25,
            "timespan": "1week", "sort": "datedesc",
        })
        response.raise_for_status()
        data = response.json()
    added = 0
    for article in data.get("articles", []):
        url = str(article.get("url") or "")
        title = str(article.get("title") or "")
        seen = str(article.get("seendate") or "")
        try:
            observed_at = datetime.strptime(seen, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).isoformat()
        except ValueError:
            observed_at = now()
        added += store.ingest_external_signal(
            external_id=f"gdelt:{hashlib.sha256(url.encode()).hexdigest()}",
            origin="gdelt_news", kind="news_report", text=title,
            source_url=url, observed_at=observed_at,
        )
    return {"articlesChecked": len(data.get("articles", [])), "signalsAdded": added, "status": "pending_review"}


def create_niche_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/niche-discovery/v1", tags=["niche discovery"])
    store = NicheStore(settings.database_path)
    admin_api_key = settings.admin_api_key

    def stripe_config() -> tuple[str, str, str]:
        key = os.getenv("NICHE_STRIPE_SECRET_KEY", "")
        price = os.getenv("NICHE_STRIPE_PRICE_ID", "")
        token_secret = os.getenv("NICHE_TOKEN_SECRET", "")
        if not key.startswith(("sk_test_", "sk_live_", "rk_test_", "rk_live_")) or not re.fullmatch(r"price_[A-Za-z0-9]+", price) or len(token_secret) < 32:
            raise HTTPException(status_code=503, detail="Niche Discovery subscriptions are not configured")
        return key, price, token_secret

    def billing_ready() -> bool:
        try:
            stripe_config()
        except HTTPException:
            return False
        return (store.published_count() > 0
                and os.getenv("NICHE_STRIPE_WEBHOOK_SECRET", "").startswith("whsec_")
                and bool(settings.contact_smtp_username and settings.contact_smtp_app_password))

    async def stripe_get(path: str, key: str) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(f"https://api.stripe.com/v1/{path}", auth=(key, ""))
        response.raise_for_status()
        return response.json()

    def access_token(subscription_id: str, secret: str) -> str:
        digest = hmac.new(secret.encode(), subscription_id.encode(), hashlib.sha256).digest()
        return "nd_" + base64.urlsafe_b64encode(digest).decode().rstrip("=")

    def require_subscription(authorization: str | None, paid_call: str | None) -> None:
        scheme, _, credential = (authorization or "").partition(" ")
        if paid_call == "1" and settings.service_api_key and scheme.lower() == "bearer" and hmac.compare_digest(credential, settings.service_api_key):
            return
        if scheme.lower() != "bearer" or not credential.startswith("nd_") or not store.active_for_token(credential):
            raise HTTPException(status_code=401, detail="An active Niche Discovery subscription is required")

    def admin(key: str | None) -> None:
        if not admin_api_key:
            raise HTTPException(status_code=503, detail="Admin key is not configured")
        if not key or not hmac.compare_digest(key, admin_api_key):
            raise HTTPException(status_code=401, detail="Admin key required")

    @router.get("/niches")
    def list_niches(q: Annotated[str, Query(max_length=150)] = "", category: Annotated[str | None, Query(max_length=80)] = None,
                    sort: Literal["recent", "score"] = "score", limit: Annotated[int, Query(ge=1, le=100)] = 20) -> dict:
        return {"niches": store.list_niches(q, category, sort, limit), "rankingMethod": "editorial_hypothesis"}

    @router.get("/niches/{niche_id}")
    def niche(niche_id: str, authorization: str | None = Header(default=None), x_niche_paid_call: str | None = Header(default=None)) -> dict:
        require_subscription(authorization, x_niche_paid_call)
        return store.niche(niche_id)

    @router.get("/niches/{niche_id}/preview")
    def niche_preview(niche_id: str) -> dict:
        return store.preview(niche_id)

    @router.get("/subscription/status")
    def subscription_status() -> dict:
        return {"available": billing_ready(), "priceUsdPerMonth": "9.99"}

    @router.post("/subscription/checkout")
    async def subscription_checkout() -> dict:
        if not billing_ready():
            raise HTTPException(status_code=503, detail="Subscriptions are not available until the first reviewed evaluation is published and billing is ready")
        key, price, _ = stripe_config()
        price_data = await stripe_get(f"prices/{price}", key)
        if (price_data.get("unit_amount") != 999 or price_data.get("currency") != "usd"
                or (price_data.get("recurring") or {}).get("interval") != "month"
                or not price_data.get("active")
                or price_data.get("livemode") != key.startswith(("sk_live_", "rk_live_"))):
            raise HTTPException(status_code=503, detail="Configured Stripe price must be an active USD 9.99 monthly price")
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post("https://api.stripe.com/v1/checkout/sessions", auth=(key, ""), data={
                "mode": "subscription", "line_items[0][price]": price,
                "line_items[0][quantity]": "1",
                "success_url": "https://aisoup.net/niche-discovery/?subscription=success",
                "cancel_url": "https://aisoup.net/niche-discovery/?subscription=cancelled",
                "metadata[serviceId]": "niche-discovery",
                "subscription_data[metadata][serviceId]": "niche-discovery",
            })
        response.raise_for_status()
        data = response.json()
        if not isinstance(data.get("url"), str) or not data["url"].startswith("https://checkout.stripe.com/"):
            raise HTTPException(status_code=502, detail="Stripe Checkout did not return a valid URL")
        return {"checkoutUrl": data["url"], "priceUsdPerMonth": "9.99"}

    @router.post("/subscription/portal")
    async def subscription_portal(authorization: str | None = Header(default=None)) -> dict:
        scheme, _, credential = (authorization or "").partition(" ")
        customer = store.customer_for_token(credential) if scheme.lower() == "bearer" and credential.startswith("nd_") else None
        if not customer:
            raise HTTPException(status_code=401, detail="Subscription token required")
        key, _, _ = stripe_config()
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post("https://api.stripe.com/v1/billing_portal/sessions", auth=(key, ""), data={
                "customer": customer, "return_url": "https://aisoup.net/niche-discovery/",
            })
        response.raise_for_status()
        data = response.json()
        if not isinstance(data.get("url"), str) or not data["url"].startswith("https://billing.stripe.com/"):
            raise HTTPException(status_code=502, detail="Stripe Billing Portal did not return a valid URL")
        return {"portalUrl": data["url"]}

    @router.post("/stripe/webhook", include_in_schema=False)
    async def stripe_webhook(request: Request) -> dict:
        secret = os.getenv("NICHE_STRIPE_WEBHOOK_SECRET", "")
        if not secret.startswith("whsec_"):
            raise HTTPException(status_code=503, detail="Niche Discovery Stripe webhook is not configured")
        body = await request.body()
        if len(body) > 262_144:
            raise HTTPException(status_code=413, detail="Stripe event is too large")
        try:
            event = verify_stripe_event(body, request.headers.get("stripe-signature"), secret)
        except InvalidStripeSignature as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        event_type = event.get("type")
        object_data = event.get("data", {}).get("object", {})
        if not isinstance(object_data, dict):
            return {"status": "ignored"}
        key, price, token_secret = stripe_config()
        if event_type in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}:
            if object_data.get("metadata", {}).get("serviceId") != "niche-discovery":
                return {"status": "ignored"}
            session_id = object_data.get("id")
            if not isinstance(session_id, str) or not re.fullmatch(r"cs_(?:test|live)_[A-Za-z0-9]+", session_id):
                raise HTTPException(status_code=422, detail="Invalid Checkout session")
            session = await stripe_get(f"checkout/sessions/{session_id}", key)
            if session.get("metadata", {}).get("serviceId") != "niche-discovery":
                raise HTTPException(status_code=422, detail="Checkout session service mismatch")
            if session.get("mode") != "subscription" or session.get("payment_status") != "paid":
                return {"status": "pending_payment"}
            if session.get("livemode") != key.startswith(("sk_live_", "rk_live_")):
                raise HTTPException(status_code=422, detail="Checkout mode mismatch")
            subscription_id = session.get("subscription")
            customer_id = session.get("customer")
            email = (session.get("customer_details") or {}).get("email")
            if not all(isinstance(v, str) and v for v in (subscription_id, customer_id, email)):
                raise HTTPException(status_code=422, detail="Checkout session is missing subscription identity")
            subscription = await stripe_get(f"subscriptions/{subscription_id}", key)
            actual_price = ((subscription.get("items") or {}).get("data") or [{}])[0].get("price", {}).get("id")
            if subscription.get("metadata", {}).get("serviceId") != "niche-discovery" or actual_price != price or subscription.get("customer") != customer_id:
                raise HTTPException(status_code=422, detail="Subscription does not match Niche Discovery price")
            status_value = subscription.get("status")
            if status_value != "active":
                return {"status": "pending_payment"}
            token = access_token(subscription_id, token_secret)
            notify = store.upsert_subscription(subscription_id, customer_id, email, "active", hashlib.sha256(token.encode()).hexdigest())
            if notify:
                send_email(settings, recipient=email, subject="Your Niche Discovery subscription access",
                           body=f"Your Niche Discovery subscription is active.\n\nAgent API / human access token: {token}\n\nOpen https://aisoup.net/niche-discovery/ and paste this token into the subscription field. Keep it private.\n\nTo manage or cancel your subscription, reply to contact@aisoup.net until self-service billing is available.")
                store.mark_notified(subscription_id)
            return {"status": "active"}
        if event_type in {"customer.subscription.updated", "customer.subscription.deleted"}:
            if object_data.get("metadata", {}).get("serviceId") != "niche-discovery":
                return {"status": "ignored"}
            subscription_id = object_data.get("id")
            if isinstance(subscription_id, str):
                subscription = await stripe_get(f"subscriptions/{subscription_id}", key)
                store.update_subscription_status(subscription_id, str(subscription.get("status", "inactive")))
            return {"status": "updated"}
        if event_type in {"invoice.paid", "invoice.payment_failed"}:
            subscription_id = object_data.get("subscription")
            if isinstance(subscription_id, str):
                subscription = await stripe_get(f"subscriptions/{subscription_id}", key)
                if subscription.get("metadata", {}).get("serviceId") == "niche-discovery":
                    store.update_subscription_status(subscription_id, str(subscription.get("status", "inactive")))
                    return {"status": "updated"}
        return {"status": "ignored"}

    @router.get("/admin/signals")
    def signals(x_admin_key: str | None = Header(default=None)) -> dict:
        admin(x_admin_key)
        return {"signals": store.signals()}

    @router.post("/admin/niches", status_code=201)
    def create_niche(draft: NicheDraft, x_admin_key: str | None = Header(default=None)) -> dict:
        admin(x_admin_key)
        return store.save_niche(draft)

    @router.post("/admin/collect/github")
    async def collect_github(x_admin_key: str | None = Header(default=None)) -> dict:
        admin(x_admin_key)
        result = await collect_configured_github(store)
        if result["status"] == "not_configured":
            raise HTTPException(status_code=503, detail="NICHE_GITHUB_REPOSITORIES is not configured")
        return result

    @router.post("/admin/collect/hacker-news")
    async def collect_hacker_news_admin(x_admin_key: str | None = Header(default=None)) -> dict:
        admin(x_admin_key)
        return await collect_hacker_news(store)

    @router.post("/admin/collect/gdelt")
    async def collect_gdelt_admin(x_admin_key: str | None = Header(default=None)) -> dict:
        admin(x_admin_key)
        result = await collect_gdelt_news(store)
        if result["status"] == "not_configured":
            raise HTTPException(status_code=503, detail="NICHE_GDELT_QUERY is not configured")
        return result

    @router.post("/admin/collect/reddit")
    async def collect_reddit_admin(x_admin_key: str | None = Header(default=None)) -> dict:
        admin(x_admin_key)
        from .niche_reddit import collect_reddit, RedditUnavailable
        try:
            result = await collect_reddit(store)
        except RedditUnavailable as error:
            store.record_collection_run("Reddit", "error", {"errorType": type(error).__name__})
            raise HTTPException(status_code=503, detail=str(error)) from None
        store.record_collection_run("Reddit", result["status"], result)
        if result["status"] != "pending_review":
            raise HTTPException(status_code=503, detail=result["status"])
        return result

    @router.get("/admin/search-leads")
    def search_leads(x_admin_key: str | None = Header(default=None)) -> dict:
        admin(x_admin_key)
        from .niche_search import leads
        return {"leads": leads(store), "evidenceEligible": False}

    @router.post("/admin/collect/search")
    async def search_collect(x_admin_key: str | None = Header(default=None)) -> dict:
        admin(x_admin_key)
        from .niche_search import collect_search, SearchUnavailable
        try:
            result = await collect_search(store)
        except SearchUnavailable as error:
            store.record_collection_run("Web search discovery", "error", {"errorType": type(error).__name__})
            raise HTTPException(status_code=503, detail=str(error)) from None
        store.record_collection_run("Web search discovery", result["status"], result)
        if result["status"] != "discovery_only":
            raise HTTPException(status_code=503, detail=result["status"])
        return result

    @router.get("/admin/collection-status")
    def collection_status(x_admin_key: str | None = Header(default=None)) -> dict:
        admin(x_admin_key)
        return {"sources": store.collection_status()}

    return router
