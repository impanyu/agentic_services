"""Live recorder: BRTI ticks + every KXBTC15M trade over ONE Kalshi WebSocket.

Trade feed: subscribes to the `trade` channel WITHOUT market tickers (= all
Kalshi trades) and keeps only KXBTC15M-*. This avoids resubscribing at every
15-minute rollover, so a new market's first trades are never missed.

Writes are buffered in memory and flushed once per second in one short
transaction. A watchdog reconnects if no BRTI tick arrives for 30 s (BRTI is a
1 Hz feed, so silence means a dead socket). Every session is logged in
ws_sessions; the daily verifier (verify.py) back-fills anything a gap missed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import signal
import time

import websockets

from common import (BRTI_INDEX, TICKER_PREFIX, WS_PATH, WS_URL, KalshiAuth, cents, connect)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("krec")

STALE_SEC = 30
FLUSH_SEC = 1.0

_stop = asyncio.Event()


class Buffer:
    def __init__(self) -> None:
        self.trades: list[tuple] = []
        self.brti: list[tuple] = []
        self.n_trades = 0
        self.n_brti = 0

    def flush(self, db) -> None:
        if not self.trades and not self.brti:
            return
        with db:
            if self.trades:
                db.executemany(
                    "INSERT OR IGNORE INTO btc15m_trades "
                    "(trade_id, ticker, ts_ms, yes_price, no_price, count, taker_side, source, recv_ms) "
                    "VALUES (?,?,?,?,?,?,?,'ws',?)", self.trades)
            if self.brti:
                db.executemany(
                    "INSERT OR IGNORE INTO brti_ticks "
                    "(ts_ms, index_id, value, avg_60s_value, avg_60s_window_sz, received_at_ms) "
                    "VALUES (?,?,?,?,?,?)", self.brti)
        self.n_trades += len(self.trades)
        self.n_brti += len(self.brti)
        self.trades.clear()
        self.brti.clear()


def parse_trade(m: dict, recv_ms: int):
    tk = m.get("market_ticker") or m.get("ticker") or ""
    if not tk.startswith(TICKER_PREFIX):
        return None
    tid = m.get("trade_id")
    if not tid:
        return None
    ts_ms = m.get("ts_ms") or (int(m["ts"]) * 1000 if m.get("ts") else recv_ms)
    yes = cents(m.get("yes_price_dollars")) if m.get("yes_price_dollars") is not None else m.get("yes_price")
    no = cents(m.get("no_price_dollars")) if m.get("no_price_dollars") is not None else m.get("no_price")
    if yes is None:
        return None
    if no is None:
        no = round(100.0 - float(yes), 4)
    cnt = m.get("count_fp", m.get("count"))
    return (tid, tk, int(ts_ms), float(yes), float(no), float(cnt or 0),
            m.get("taker_side"), recv_ms)


def parse_brti(payload: dict):
    if str(payload.get("index_id", "")).upper() != BRTI_INDEX:
        return None
    data = payload.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict) or data.get("value") is None or data.get("time") is None:
        return None
    ts_ms = int(data["time"])
    if ts_ms < 1_000_000_000_000:
        ts_ms *= 1000
    avg = payload.get("avg_60s_data") or {}
    av, asz = avg.get("value"), avg.get("window_size")
    return (ts_ms, BRTI_INDEX, float(data["value"]),
            float(av) if av is not None else None,
            int(asz) if asz is not None else None,
            int(payload.get("received_at") or ts_ms))


async def session(auth: KalshiAuth, db, buf: Buffer) -> str:
    async with websockets.connect(WS_URL, additional_headers=auth.headers("GET", WS_PATH),
                                  ping_interval=20, ping_timeout=20, close_timeout=3,
                                  max_size=2**23) as ws:
        await ws.send(json.dumps({"id": 1, "cmd": "subscribe",
                                  "params": {"channels": ["cfbenchmarks_value"], "index_ids": [BRTI_INDEX]}}))
        await ws.send(json.dumps({"id": 2, "cmd": "subscribe", "params": {"channels": ["trade"]}}))
        log.info("connected + subscribed (BRTI, all trades → keep %s*)", TICKER_PREFIX)
        last_brti = time.monotonic()
        last_flush = time.monotonic()
        last_log = 0.0
        while not _stop.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                raw = None
            now = time.monotonic()
            if raw is not None:
                recv_ms = int(time.time() * 1000)
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    msg = {}
                t = msg.get("type")
                if t == "trade":
                    row = parse_trade(msg.get("msg") or {}, recv_ms)
                    if row:
                        buf.trades.append(row)
                elif t == "cfbenchmarks_value":
                    row = parse_brti(msg.get("msg") or {})
                    if row:
                        buf.brti.append(row)
                        last_brti = now
                elif t == "error":
                    log.error("ws error: %s", msg)
            if now - last_flush >= FLUSH_SEC:
                buf.flush(db)
                last_flush = now
            if now - last_brti > STALE_SEC:
                return f"no BRTI for {STALE_SEC}s"
            if now - last_log >= 60:
                log.info("alive: trades=%d brti=%d (session totals)", buf.n_trades, buf.n_brti)
                last_log = now
        return "stop requested"


async def main() -> None:
    auth = KalshiAuth.from_env()
    db = connect()
    loop = asyncio.get_running_loop()
    for s in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(s, _stop.set)
    backoff = 1
    while not _stop.is_set():
        buf = Buffer()
        t0 = int(time.time() * 1000)
        sid = db.execute("INSERT INTO ws_sessions (connected_ms) VALUES (?)", (t0,)).lastrowid
        db.commit()
        reason = "?"
        try:
            reason = await session(auth, db, buf)
            backoff = 1
        except Exception as e:  # network / auth / protocol
            reason = f"{type(e).__name__}: {e}"[:300]
            log.warning("session ended: %s", reason)
        finally:
            try:
                buf.flush(db)
            except Exception as e:
                log.error("final flush failed: %s", e)
            db.execute("UPDATE ws_sessions SET closed_ms=?, reason=?, n_trades=?, n_brti=? WHERE id=?",
                       (int(time.time() * 1000), reason, buf.n_trades, buf.n_brti, sid))
            db.commit()
        if _stop.is_set():
            break
        log.info("reconnect in %ds (%s)", backoff, reason)
        try:
            await asyncio.wait_for(_stop.wait(), timeout=backoff)
        except asyncio.TimeoutError:
            pass
        backoff = min(backoff * 2, 30)
    db.close()
    log.info("stopped")


if __name__ == "__main__":
    asyncio.run(main())
