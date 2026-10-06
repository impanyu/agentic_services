"""Bounded, approval-gated Reddit Data API collection. No HTML scraping."""
from __future__ import annotations

import asyncio
import os
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import httpx

from .niche_discovery import NicheStore

# Discovery heuristic, not proof of demand. Keep ambiguous candidates private.
PAIN = re.compile(r"\b(complaint|frustrat\w*|wish|need|looking for|alternative|too expensive|"
                  r"manual|workaround|doesn['’]t work|cannot|can['’]t|missing|problem|"
                  r"pay for|recommend|suggestion|hate|broken)\b", re.I)


class RedditUnavailable(Exception):
    """Safe error without credential-bearing request/response objects."""


def configuration() -> tuple[dict[str, str] | None, str]:
    if os.getenv("NICHE_COLLECT_REDDIT") != "1":
        return None, "disabled"
    names = ("NICHE_REDDIT_APPROVAL_REFERENCE", "NICHE_REDDIT_CLIENT_ID",
             "NICHE_REDDIT_CLIENT_SECRET", "NICHE_REDDIT_REFRESH_TOKEN",
             "NICHE_REDDIT_USER_AGENT", "NICHE_REDDIT_SUBREDDITS")
    values = {name: os.getenv(name, "").strip() for name in names}
    if not values[names[0]]:
        return None, "permission_needed"
    if not all(values.values()):
        return None, "not_configured"
    communities = values[names[-1]].split(",")
    if len(communities) > 20 or any(not re.fullmatch(r"[A-Za-z0-9_]{2,21}", c.strip()) for c in communities):
        return None, "invalid_community_scope"
    return values, "configured"


def purge(store: NicheStore, external_ids: list[str]) -> int:
    """Delete evidence and dependent reports; derived claims must be rebuilt."""
    if not external_ids:
        return 0
    placeholders = ",".join("?" for _ in external_ids)
    with store.connect() as db:
        rows = db.execute(f"SELECT id FROM niche_signals WHERE external_id IN ({placeholders}) AND origin='reddit'", external_ids).fetchall()
        for row in rows:
            dependent = [r[0] for r in db.execute("SELECT niche_id FROM niche_links WHERE signal_id=?", (row["id"],))]
            for niche_id in dependent:
                db.execute("DELETE FROM niche_links WHERE niche_id=?", (niche_id,))
                db.execute("DELETE FROM niches WHERE id=?", (niche_id,))
            db.execute("DELETE FROM niche_links WHERE signal_id=?", (row["id"],))
            db.execute("DELETE FROM niche_signals WHERE id=?", (row["id"],))
        return len(rows)


def ingest(store: NicheStore, item: dict, communities: set[str]) -> tuple[int, int]:
    name = str(item.get("name", ""))
    if not re.fullmatch(r"t[13]_[a-z0-9]+", name):
        return 0, 0
    external_id = "reddit:" + name
    body = str(item.get("body", item.get("selftext", "")))
    if (item.get("author") in {None, "[deleted]"} or body in {"[removed]", "[deleted]"}
            or item.get("removed_by_category") or item.get("over_18")
            or str(item.get("subreddit", "")).lower() not in communities):
        return 0, purge(store, [external_id])
    text = str(item.get("title", "")) + " " + body
    if not PAIN.search(text):
        return 0, purge(store, [external_id])
    permalink = str(item.get("permalink", ""))
    parsed = urlparse(permalink)
    if parsed.scheme or parsed.netloc or not permalink.startswith("/r/"):
        return 0, purge(store, [external_id])
    text = re.sub(r"https?://\S+", "[link removed]", text)
    text = re.sub(r"/?u/[A-Za-z0-9_-]+", "[user removed]", text)
    text = re.sub(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", "[email removed]", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()[:500]
    if len(text) < 25:
        return 0, purge(store, [external_id])
    timestamp = datetime.fromtimestamp(float(item["created_utc"]), UTC).isoformat()
    with store.connect() as db:
        existing = db.execute("SELECT id,text FROM niche_signals WHERE external_id=?", (external_id,)).fetchone()
    if existing:
        if existing["text"] != text:
            # Changed evidence invalidates a previously curated conclusion.
            purge(store, [external_id])
        else:
            return 0, 0
    added = store.ingest_external_signal(external_id=external_id, origin="reddit", kind="candidate",
        text=text, source_url="https://www.reddit.com" + permalink,
        observed_at=timestamp, audience="r/" + item["subreddit"])
    return int(added), 0


async def collect_reddit(store: NicheStore) -> dict:
    config, status = configuration()
    if not config:
        return {"status": status, "signalsAdded": 0}
    communities = {c.strip().lower() for c in config["NICHE_REDDIT_SUBREDDITS"].split(",")}
    # Pilot retention ceiling, shorten if required by the signed agreement.
    cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    with store.connect() as db:
        expired = [r[0] for r in db.execute("SELECT external_id FROM niche_signals WHERE origin='reddit' AND observed_at<?", (cutoff,))]
    removed = purge(store, expired)
    added = checked = 0
    async with httpx.AsyncClient(timeout=20, follow_redirects=False,
                                 headers={"User-Agent": config["NICHE_REDDIT_USER_AGENT"]}) as client:
        response = await client.post("https://www.reddit.com/api/v1/access_token",
            auth=(config["NICHE_REDDIT_CLIENT_ID"], config["NICHE_REDDIT_CLIENT_SECRET"]),
            data={"grant_type": "refresh_token", "refresh_token": config["NICHE_REDDIT_REFRESH_TOKEN"]})
        if response.status_code != 200:
            raise RedditUnavailable(f"OAuth status {response.status_code}")
        token = response.json().get("access_token")
        if not token:
            raise RedditUnavailable("OAuth token missing")
        client.headers["Authorization"] = "Bearer " + token

        async def get(path: str, params: dict) -> dict:
            nonlocal checked
            response = await client.get("https://oauth.reddit.com" + path, params=params)
            if response.status_code != 200:
                # Stop immediately on throttling; next scheduled run retries. No tight retry loop.
                raise RedditUnavailable(f"Data API status {response.status_code}")
            payload = response.json()["data"]
            checked += len(payload["children"])
            remaining = response.headers.get("x-ratelimit-remaining")
            if remaining is not None and float(remaining) < 1:
                raise RedditUnavailable("API rate budget exhausted")
            await asyncio.sleep(1)
            return payload

        # Refresh ALL retained evidence before acquiring more; each batch <=100.
        with store.connect() as db:
            retained = [r[0].removeprefix("reddit:") for r in db.execute("SELECT external_id FROM niche_signals WHERE origin='reddit'")]
        for offset in range(0, len(retained), 100):
            batch = retained[offset:offset + 100]
            data = await get("/api/info", {"id": ",".join(batch), "raw_json": 1})
            found = {child["data"]["name"] for child in data["children"]}
            removed += purge(store, ["reddit:" + name for name in batch if name not in found])
            for child in data["children"]:
                a, d = ingest(store, child["data"], communities)
                added += a
                removed += d

        # Sliding recent window: two pages per stream per community. Repeated polls dedupe.
        # This is bounded sampling, not an exhaustive historical archive.
        for community in sorted(communities):
            for stream in ("new", "comments"):
                after = None
                for _ in range(2):
                    params = {"limit": 50, "raw_json": 1}
                    if after:
                        params["after"] = after
                    data = await get(f"/r/{community}/{stream}", params)
                    for child in data["children"]:
                        item = child["data"]
                        if datetime.fromtimestamp(float(item["created_utc"]), UTC).isoformat() < cutoff:
                            continue
                        a, d = ingest(store, item, communities)
                        added += a
                        removed += d
                    after = data.get("after")
                    if not after:
                        break
    return {"status": "pending_review", "communities": sorted(communities),
            "itemsChecked": checked, "signalsAdded": added, "signalsRemoved": removed,
            "coverage": "latest_100_posts_and_comments_per_community", "retentionDays": 30}
