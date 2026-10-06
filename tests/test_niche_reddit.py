import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from agentic_services.niche_discovery import NicheStore
from agentic_services.niche_reddit import RedditUnavailable, collect_reddit, ingest


def item(name="t3_abc", **changes):
    return {"name": name, "author": "someone", "subreddit": "smallbusiness",
            "title": "I need an alternative to manual invoice reconciliation",
            "selftext": "Contact private@example.org or u/somebody https://example.org/me",
            "permalink": "/r/smallbusiness/comments/abc/example/",
            "created_utc": datetime.now(UTC).timestamp(), **changes}


def configure(monkeypatch):
    for name, value in {"NICHE_COLLECT_REDDIT": "1", "NICHE_REDDIT_APPROVAL_REFERENCE": "test-agreement",
                        "NICHE_REDDIT_CLIENT_ID": "test-client", "NICHE_REDDIT_CLIENT_SECRET": "test-secret",
                        "NICHE_REDDIT_REFRESH_TOKEN": "test-refresh", "NICHE_REDDIT_USER_AGENT": "test-agent",
                        "NICHE_REDDIT_SUBREDDITS": "smallbusiness"}.items():
        monkeypatch.setenv(name, value)


def test_reddit_gate_makes_no_requests(tmp_path, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("network should not be called")
    monkeypatch.setattr(httpx.AsyncClient, "post", forbidden)
    monkeypatch.setenv("NICHE_COLLECT_REDDIT", "0")
    store = NicheStore(tmp_path / "db")
    assert asyncio.run(collect_reddit(store))["status"] == "disabled"
    monkeypatch.setenv("NICHE_COLLECT_REDDIT", "1")
    monkeypatch.delenv("NICHE_REDDIT_APPROVAL_REFERENCE", raising=False)
    assert asyncio.run(collect_reddit(store))["status"] == "permission_needed"


def test_edit_remove_and_scope(tmp_path):
    store = NicheStore(tmp_path / "db")
    communities = {"smallbusiness"}
    assert ingest(store, item(), communities) == (1, 0)
    signal = store.signals()[0]
    assert signal["moderation"] == "pending"
    assert "private@example.org" not in signal["text"]
    assert "u/somebody" not in signal["text"]
    assert ingest(store, item(), communities) == (0, 0)
    assert ingest(store, item(title="I need a different paid invoice workflow"), communities) == (1, 0)
    assert "different" in store.signals()[0]["text"]
    assert ingest(store, item(author="[deleted]"), communities) == (0, 1)
    assert store.signals() == []
    assert ingest(store, item(subreddit="unapproved"), communities) == (0, 0)
    assert ingest(store, item(over_18=True), communities) == (0, 0)
    assert ingest(store, item(permalink="//evil.example/x"), communities) == (0, 0)


def test_listing_comments_dedupe_refresh_and_rate_limit(tmp_path, monkeypatch):
    configure(monkeypatch)
    store = NicheStore(tmp_path / "db")
    calls = []
    deleted = False
    throttled = False

    async def fake_post(self, url, **kwargs):
        assert url == "https://www.reddit.com/api/v1/access_token"
        assert kwargs["data"]["grant_type"] == "refresh_token"
        return httpx.Response(200, json={"access_token": "test-access"})

    async def fake_get(self, url, **kwargs):
        calls.append((url, kwargs["params"]))
        assert self.headers["Authorization"] == "Bearer test-access"
        if throttled:
            return httpx.Response(429)
        entries = [] if deleted else [item() if url.endswith("/new") or url.endswith("/api/info") else
                                     item("t1_def", title="", body="I cannot find a tool for this manual reconciliation.")]
        if url.endswith("/api/info") and not deleted:
            entries = [item(), item("t1_def", title="", body="I cannot find a tool for this manual reconciliation.")]
        return httpx.Response(200, json={"data": {"children": [{"data": x} for x in entries], "after": None}})

    async def no_sleep(*args):
        pass
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    assert asyncio.run(collect_reddit(store))["signalsAdded"] == 2
    assert asyncio.run(collect_reddit(store))["signalsAdded"] == 0
    assert any(url.endswith("/api/info") for url, _ in calls)
    deleted = True
    assert asyncio.run(collect_reddit(store))["signalsRemoved"] == 2
    assert store.signals() == []
    throttled = True
    with pytest.raises(RedditUnavailable, match="429"):
        asyncio.run(collect_reddit(store))


def test_removed_evidence_deletes_derived_reports(tmp_path):
    from agentic_services.niche_discovery import NicheDraft
    store = NicheStore(tmp_path / "db")
    ingest(store, item(), {"smallbusiness"})
    for index in range(2):
        store.ingest_external_signal(external_id=f"other:{index}", origin="other", kind="complaint",
            text="Manual invoice reconciliation is a recurring problem for small shops.",
            source_url=f"https://example{index}.org/issue", observed_at=datetime.now(UTC).isoformat())
    draft = NicheDraft(title="Recurring small shop invoicing gap", problem="Small shops reconcile invoices manually across providers.",
        buyer="Small shops", category="commerce", solution_hypothesis="A reconciliation tool for cross-provider invoices.",
        pain=3, frequency=3, willingness_to_pay=2, reachable_buyers=2, feasibility=3, competition_gap=2,
        evaluation_notes="These signals suggest a workflow gap that requires buyer validation.",
        signal_ids=[signal["id"] for signal in store.signals()], publish=True)
    report = store.save_niche(draft)
    assert report["signalCount"] == 3
    ingest(store, item(selftext="[removed]"), {"smallbusiness"})
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM niches").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM niche_links").fetchone()[0] == 0
    assert len(store.signals()) == 2
