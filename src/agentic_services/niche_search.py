"""Search-index discovery leads, deliberately separate from publishable evidence."""
from __future__ import annotations

import hashlib
import ipaddress
import os
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from .niche_discovery import NicheStore, now

QUERIES = (
    'site:reddit.com "looking for" "alternative"',
    'site:reddit.com "wish there was"',
    '"customer complaints" "shortage"',
    '"product reviews" "missing feature"',
    '"small business" "manual workaround"',
    '"would pay for" "tool"',
    '"服务" "太贵" "替代"',
    '"产品" "差评" "希望"',
)


class SearchUnavailable(Exception):
    pass


def setup(store: NicheStore) -> None:
    with store.connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS niche_search_leads (
                id TEXT PRIMARY KEY, url TEXT UNIQUE NOT NULL, domain TEXT NOT NULL,
                provider TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'discovery_only'
            );
            CREATE TABLE IF NOT EXISTS niche_search_matches (
                lead_id TEXT NOT NULL, query TEXT NOT NULL, last_seen TEXT NOT NULL,
                PRIMARY KEY(lead_id,query)
            );
            CREATE TABLE IF NOT EXISTS niche_search_budget (
                day TEXT PRIMARY KEY, requests INTEGER NOT NULL
            );
        """)


def normalized_url(raw: str) -> str | None:
    try:
        parts = urlsplit(raw)
        host = (parts.hostname or "").lower()
        if parts.scheme not in {"http", "https"} or parts.username or parts.password or not host or parts.port not in {None, 80, 443}:
            return None
        if host == "localhost" or "." not in host or host.endswith((".local", ".internal")):
            return None
        try:
            ipaddress.ip_address(host)
            return None  # Results must point to named public sites; never request them here.
        except ValueError:
            pass
        params = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_") and k.lower() not in {"gclid", "fbclid"}]
        return urlunsplit((parts.scheme, host, parts.path or "/", urlencode(sorted(params)), ""))
    except ValueError:
        return None


def leads(store: NicheStore, limit: int = 100) -> list[dict]:
    setup(store)
    with store.connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM niche_search_leads ORDER BY last_seen DESC LIMIT ?", (limit,))]


def record(store: NicheStore, url: str, query: str) -> bool:
    lead_id = "lead_" + hashlib.sha256(url.encode()).hexdigest()
    stamp = now()
    with store.connect() as db:
        added = db.execute("INSERT OR IGNORE INTO niche_search_leads(id,url,domain,provider,first_seen,last_seen) VALUES(?,?,?,?,?,?)",
            (lead_id, url, urlsplit(url).hostname, "brave", stamp, stamp)).rowcount
        db.execute("UPDATE niche_search_leads SET last_seen=? WHERE id=?", (stamp, lead_id))
        db.execute("INSERT INTO niche_search_matches VALUES(?,?,?) ON CONFLICT(lead_id,query) DO UPDATE SET last_seen=excluded.last_seen", (lead_id, query, stamp))
    return bool(added)


def reserve(store: NicheStore, maximum: int) -> bool:
    day = datetime.now(UTC).date().isoformat()
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        count = db.execute("SELECT requests FROM niche_search_budget WHERE day=?", (day,)).fetchone()
        if count and count[0] >= maximum:
            return False
        db.execute("INSERT INTO niche_search_budget VALUES(?,1) ON CONFLICT(day) DO UPDATE SET requests=requests+1", (day,))
    return True


async def collect_search(store: NicheStore, queries: tuple[str, ...] | None = None) -> dict:
    queries = QUERIES if queries is None else queries
    if not queries or len(queries) > 8 or any(not 3 <= len(q) <= 500 for q in queries):
        raise ValueError("Invalid discovery queries")
    setup(store)
    cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    with store.connect() as db:
        db.execute("DELETE FROM niche_search_matches WHERE lead_id IN (SELECT id FROM niche_search_leads WHERE last_seen<?)", (cutoff,))
        db.execute("DELETE FROM niche_search_leads WHERE last_seen<?", (cutoff,))
    if os.getenv("NICHE_COLLECT_SEARCH") != "1":
        return {"status": "disabled", "leadsAdded": 0}
    key = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
    if not key:
        return {"status": "not_configured", "leadsAdded": 0}
    # Operator records an actual plan/agreement permitting persistent search-result storage.
    if not os.getenv("NICHE_SEARCH_STORAGE_REFERENCE", "").strip():
        return {"status": "storage_permission_needed", "leadsAdded": 0}
    try:
        maximum = int(os.getenv("NICHE_SEARCH_DAILY_REQUEST_LIMIT", "8"))
        if not 1 <= maximum <= 32:
            raise ValueError
    except ValueError:
        return {"status": "invalid_budget", "leadsAdded": 0}
    requests = added = 0
    async with httpx.AsyncClient(timeout=20, follow_redirects=False,
        headers={"X-Subscription-Token": key, "Accept": "application/json"}) as client:
        # Rotate a full query cycle per day at the default 8-request budget.
        day = datetime.now(UTC).date().isoformat()
        with store.connect() as db:
            row = db.execute("SELECT requests FROM niche_search_budget WHERE day=?", (day,)).fetchone()
        start = row[0] if row else 0
        for index in range(len(queries)):
            if not reserve(store, maximum):
                break
            query = queries[(start + index) % len(queries)]
            try:
                response = await client.get("https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": 10, "freshness": "pm", "safesearch": "strict"})
            except httpx.HTTPError:
                raise SearchUnavailable("Search transport failure") from None
            if response.status_code != 200:
                raise SearchUnavailable(f"Search status {response.status_code}") from None
            requests += 1
            for result in response.json().get("web", {}).get("results", [])[:10]:
                url = normalized_url(str(result.get("url", "")))
                if url:
                    added += record(store, url, query)
    return {"status": "discovery_only", "requestsMade": requests, "leadsAdded": added,
            "dailyRequestLimit": maximum, "coverage": "configured_queries_recent_month_top_10",
            "retentionDays": 30, "pageContentFetched": False}
