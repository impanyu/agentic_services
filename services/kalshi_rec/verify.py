"""Daily REST cross-check of the WebSocket recording (public endpoints, no auth).

For every KXBTC15M market that closed in the look-back window and is not yet
verified: pull market metadata + the FULL trade list from REST, compare with the
WS rows trade-by-trade (trade_id), back-fill anything the WS missed (source='rest'),
and store the counts in btc15m_markets. Markets that closed < SETTLE_GRACE_MIN ago
are left for the next run.

Usage:  python verify.py [--days N] [--recheck]
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timedelta, timezone

import httpx

from common import REST_URL, SERIES, cents, connect

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("kverify")

SETTLE_GRACE_MIN = 10


def get(client: httpx.Client, path: str, params: dict) -> dict:
    for i in range(8):
        r = client.get(REST_URL + path, params=params)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(1.5 * (i + 1))
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return {}


def list_markets(client, min_close: datetime, max_close: datetime) -> list[dict]:
    out, cursor = [], None
    while True:
        p = {"series_ticker": SERIES, "limit": 1000,
             "min_close_ts": int(min_close.timestamp()), "max_close_ts": int(max_close.timestamp())}
        if cursor:
            p["cursor"] = cursor
        d = get(client, "/markets", p)
        out += d.get("markets", [])
        cursor = d.get("cursor")
        if not cursor or not d.get("markets"):
            return out


def rest_trades(client, ticker: str) -> list[dict]:
    out, cursor = [], None
    while True:
        p = {"ticker": ticker, "limit": 1000}
        if cursor:
            p["cursor"] = cursor
        d = get(client, "/markets/trades", p)
        out += d.get("trades", [])
        cursor = d.get("cursor")
        if not cursor or not d.get("trades"):
            return out


def norm(t: dict) -> tuple:
    yes = cents(t.get("yes_price_dollars")) if t.get("yes_price_dollars") is not None else float(t.get("yes_price"))
    no = cents(t.get("no_price_dollars")) if t.get("no_price_dollars") is not None else round(100 - yes, 4)
    ts = datetime.fromisoformat(t["created_time"].replace("Z", "+00:00"))
    return (t["trade_id"], t["ticker"], int(ts.timestamp() * 1000), float(yes), float(no),
            float(t.get("count_fp", t.get("count")) or 0), t.get("taker_side"))


def verify_market(db, client, m: dict) -> dict:
    tk = m["ticker"]
    rest = {r[0]: r for r in (norm(t) for t in rest_trades(client, tk))}
    ws = {row[0]: row for row in db.execute(
        "SELECT trade_id, ticker, ts_ms, yes_price, no_price, count, taker_side FROM btc15m_trades "
        "WHERE ticker=? AND source='ws'", (tk,))}
    missing = [rest[k] for k in rest.keys() - ws.keys()]
    extra = ws.keys() - rest.keys()
    mismatch = sum(1 for k in rest.keys() & ws.keys()
                   if abs(rest[k][3] - ws[k][3]) > 1e-6 or abs(rest[k][5] - ws[k][5]) > 1e-6
                   or (rest[k][6] or "") != (ws[k][6] or ""))
    stat = dict(rest_n=len(rest), ws_n=len(rest.keys() & ws.keys()), missing_n=len(missing),
                extra_n=len(extra), rest_ct=sum(r[5] for r in rest.values()),
                ws_ct=sum(w[5] for w in ws.values()), mismatch_n=mismatch)
    with db:
        if missing:
            db.executemany(
                "INSERT OR IGNORE INTO btc15m_trades "
                "(trade_id, ticker, ts_ms, yes_price, no_price, count, taker_side, source, recv_ms) "
                "VALUES (?,?,?,?,?,?,?,'rest',NULL)", missing)
        db.execute(
            "INSERT INTO btc15m_markets (ticker, open_time, close_time, floor_strike, result, expiration_value, "
            "volume, verified_at, rest_n, ws_n, missing_n, extra_n, rest_ct, ws_ct, mismatch_n) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(ticker) DO UPDATE SET "
            "open_time=excluded.open_time, close_time=excluded.close_time, floor_strike=excluded.floor_strike, "
            "result=excluded.result, expiration_value=excluded.expiration_value, volume=excluded.volume, "
            "verified_at=excluded.verified_at, rest_n=excluded.rest_n, ws_n=excluded.ws_n, "
            "missing_n=excluded.missing_n, extra_n=excluded.extra_n, rest_ct=excluded.rest_ct, "
            "ws_ct=excluded.ws_ct, mismatch_n=excluded.mismatch_n",
            (tk, m.get("open_time"), m.get("close_time"), m.get("floor_strike"), m.get("result"),
             float(m["expiration_value"]) if m.get("expiration_value") else None,
             float(m.get("volume_fp") or m.get("volume") or 0),
             datetime.now(timezone.utc).isoformat(timespec="seconds"),
             stat["rest_n"], stat["ws_n"], stat["missing_n"], stat["extra_n"],
             stat["rest_ct"], stat["ws_ct"], stat["mismatch_n"]))
    return stat


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=2.0, help="look-back window (days)")
    ap.add_argument("--recheck", action="store_true", help="re-verify already verified markets")
    a = ap.parse_args()

    db = connect()
    now = datetime.now(timezone.utc)
    lo, hi = now - timedelta(days=a.days), now - timedelta(minutes=SETTLE_GRACE_MIN)
    # only markets the recorder could have seen (avoid flagging pre-deployment markets)
    first_ws = db.execute("SELECT MIN(connected_ms) FROM ws_sessions").fetchone()[0]
    if first_ws:
        lo = max(lo, datetime.fromtimestamp(first_ws / 1000, timezone.utc) + timedelta(minutes=15))
    done = set() if a.recheck else {r[0] for r in db.execute(
        "SELECT ticker FROM btc15m_markets WHERE verified_at IS NOT NULL")}
    with httpx.Client(timeout=30, headers={"User-Agent": "kalshi-rec-verify"}) as client:
        mkts = [m for m in list_markets(client, lo, hi) if m["ticker"] not in done]
        log.info("verifying %d markets closed %s → %s", len(mkts), lo.isoformat(timespec="minutes"),
                 hi.isoformat(timespec="minutes"))
        tot = dict(rest_n=0, missing_n=0, extra_n=0, mismatch_n=0)
        bad = []
        for m in sorted(mkts, key=lambda x: x["close_time"]):
            s = verify_market(db, client, m)
            for k in tot:
                tot[k] += s[k]
            if s["missing_n"] or s["extra_n"] or s["mismatch_n"]:
                bad.append((m["ticker"], s["missing_n"], s["extra_n"], s["mismatch_n"]))
    cov = 100.0 * (1 - tot["missing_n"] / tot["rest_n"]) if tot["rest_n"] else 100.0
    log.info("DONE markets=%d rest_trades=%d ws_coverage=%.4f%% missing(back-filled)=%d extra=%d mismatch=%d",
             len(mkts), tot["rest_n"], cov, tot["missing_n"], tot["extra_n"], tot["mismatch_n"])
    for b in bad[:50]:
        log.warning("diff %s missing=%d extra=%d mismatch=%d", *b)
    db.close()


if __name__ == "__main__":
    main()
