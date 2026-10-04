"""Read/write API over the hot SQLite + Parquet archive.

Bound to 127.0.0.1 behind the public reverse proxy's /kalshi-rec/v1/ prefix.
Read routes accept KREC_READ_TOKEN; upload, stats, and schema routes require
KREC_API_TOKEN. Both tokens remain private to the owner.

Reads (fmt=parquet → raw Parquet bytes in the archive's compact encoding; fmt=json → decoded rows):
  GET  /health
  GET  /stats
  GET  /btc15m/markets?start=YYYY-MM-DD&end=YYYY-MM-DD        (verification + metadata)
  GET  /btc15m/trades?ticker=KXBTC15M-...                     (one or more ticker=)
  GET  /btc15m/trades?day=YYYY-MM-DD                           (ET day by market close)
  GET  /brti?start_ms=&end_ms=
Writes (idempotent, for uploading the Mac's history; body = Parquet with the archive schema):
  POST /btc15m/trades     merges by trade_id into the day files
  POST /btc15m/markets    JSON list of market metadata rows (upsert, does not touch verification)
  POST /brti              merges by ts_ms
"""
from __future__ import annotations

import io
import os
import secrets
import sqlite3
from datetime import date, datetime, timedelta

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.openapi.utils import get_openapi

import archive as A
from common import DB_PATH, connect, load_env

load_env()
connect().close()   # make sure the hot DB + schema exist before the first read-only open
TOKEN = os.environ.get("KREC_API_TOKEN", "")
READ_TOKEN = os.environ.get("KREC_READ_TOKEN", "")
JSON_ROW_LIMIT = 500_000

PUBLIC_BASE_URL = "https://api.aisoup.net/kalshi-rec/v1"
app = FastAPI(title="Kalshi 15-minute BTC Recorder API", version="0.1.0",
              openapi_url=None, docs_url=None, redoc_url=None)


def admin_auth(authorization: str = Header(default="")) -> None:
    if not TOKEN or not secrets.compare_digest(authorization, f"Bearer {TOKEN}"):
        raise HTTPException(401, "bad token")


def read_auth(authorization: str = Header(default="")) -> None:
    if not any(token and secrets.compare_digest(authorization, f"Bearer {token}")
               for token in (READ_TOKEN, TOKEN)):
        raise HTTPException(401, "bad token")


@app.get("/openapi.json", dependencies=[Depends(admin_auth)], include_in_schema=False)
def owner_openapi():
    schema = get_openapi(title=app.title, version=app.version, routes=app.routes,
                         servers=[{"url": PUBLIC_BASE_URL}])
    schema.setdefault("components", {})["securitySchemes"] = {
        "BearerAuth": {"type": "http", "scheme": "bearer"}}
    for path in schema["paths"].values():
        for operation in path.values():
            operation["parameters"] = [p for p in operation.get("parameters", [])
                                       if p.get("name", "").lower() != "authorization"]
            operation["security"] = [{"BearerAuth": []}]
    return schema


def ro_db():
    db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    return db


def _hot_trades(where: str, args: tuple) -> pa.Table:
    db = ro_db()
    try:
        rows = db.execute("SELECT trade_id, ticker, ts_ms, yes_price, count, taker_side FROM btc15m_trades "
                          f"WHERE {where}", args).fetchall()
    finally:
        db.close()
    return A.trades_to_table([tuple(r) for r in rows]) if rows else A.TRADE_SCHEMA.empty_table()


def _dedup(t: pa.Table, key: str) -> pa.Table:
    seen, keep = set(), []
    for k in t.column(key).to_pylist():
        keep.append(k not in seen)
        seen.add(k)
    return t.filter(pa.array(keep, pa.bool_())) if t.num_rows else t


def _emit(t: pa.Table, fmt: str, kind: str) -> Response:
    if fmt == "json":
        if t.num_rows > JSON_ROW_LIMIT:
            raise HTTPException(413, f"{t.num_rows} rows; use fmt=parquet")
        rows = A.table_to_records(t) if kind == "trades" else t.to_pylist()
        return Response(content=__import__("json").dumps({"n": len(rows), "rows": rows}),
                        media_type="application/json")
    buf = io.BytesIO()
    pq.write_table(t, buf, compression="zstd")
    return Response(content=buf.getvalue(), media_type="application/vnd.apache.parquet",
                    headers={"X-Rows": str(t.num_rows)})


@app.get("/health", dependencies=[Depends(read_auth)])
def health():
    return {"ok": True}


@app.get("/stats", dependencies=[Depends(admin_auth)])
def stats():
    db = ro_db()
    try:
        hot = {
            "trades": db.execute("SELECT COUNT(*), MIN(ts_ms), MAX(ts_ms) FROM btc15m_trades").fetchone()[:],
            "trades_by_source": dict(db.execute("SELECT source, COUNT(*) FROM btc15m_trades GROUP BY source").fetchall()),
            "brti": db.execute("SELECT COUNT(*), MIN(ts_ms), MAX(ts_ms) FROM brti_ticks").fetchone()[:],
            "markets_verified": db.execute("SELECT COUNT(*), SUM(rest_n), SUM(missing_n), SUM(extra_n), SUM(mismatch_n) "
                                           "FROM btc15m_markets WHERE verified_at IS NOT NULL").fetchone()[:],
            "last_session": dict(db.execute("SELECT * FROM ws_sessions ORDER BY id DESC LIMIT 1").fetchone() or {}),
        }
    finally:
        db.close()
    return {"hot_db_bytes": os.path.getsize(DB_PATH), "hot": hot, "archive": A.usage()}


@app.get("/btc15m/markets", dependencies=[Depends(read_auth)])
def markets(start: date = Query(...), end: date = Query(...)):
    lo = datetime(start.year, start.month, start.day, tzinfo=A.ET)
    hi = datetime(end.year, end.month, end.day, tzinfo=A.ET) + timedelta(days=1)
    db = ro_db()
    try:
        rows = [dict(r) for r in db.execute("SELECT * FROM btc15m_markets ORDER BY close_time")]
    finally:
        db.close()
    rows = [r for r in rows if lo <= A.ticker_close_et(r["ticker"]) < hi]
    return {"n": len(rows), "rows": rows}


@app.get("/btc15m/trades", dependencies=[Depends(read_auth)])
def trades(ticker: list[str] | None = Query(None), day: date | None = None, fmt: str = "parquet"):
    if not ticker and not day:
        raise HTTPException(400, "give ticker= or day=")
    if ticker:
        days = sorted({A.ticker_day(t) for t in ticker})
        q = ",".join("?" * len(ticker))
        hot = _hot_trades(f"ticker IN ({q})", tuple(ticker))
        cold = A.read_trades(days, ticker)
    else:
        days = [day]
        db = ro_db()
        try:
            hot_t = [r[0] for r in db.execute("SELECT DISTINCT ticker FROM btc15m_trades")
                     if A.ticker_day(r[0]) == day]
        finally:
            db.close()
        q = ",".join("?" * len(hot_t)) or "''"
        hot = _hot_trades(f"ticker IN ({q})", tuple(hot_t)) if hot_t else A.TRADE_SCHEMA.empty_table()
        cold = A.read_trades(days)
    t = pa.concat_tables([cold.cast(A.TRADE_SCHEMA), hot.cast(A.TRADE_SCHEMA)]).unify_dictionaries()
    t = A.sort_table(_dedup(t, "trade_id"), [("ticker", "ascending"), ("ts_ms", "ascending")])
    return _emit(t, fmt, "trades")


@app.get("/brti", dependencies=[Depends(read_auth)])
def brti(start_ms: int, end_ms: int, fmt: str = "parquet"):
    if end_ms - start_ms > 40 * 86_400_000:
        raise HTTPException(400, "max 40 days per request")
    db = ro_db()
    try:
        rows = db.execute("SELECT ts_ms, value, avg_60s_value, avg_60s_window_sz, received_at_ms FROM brti_ticks "
                          "WHERE ts_ms >= ? AND ts_ms < ?", (start_ms, end_ms)).fetchall()
    finally:
        db.close()
    hot = A.brti_to_table([tuple(r) for r in rows]) if rows else A.BRTI_SCHEMA.empty_table()
    t = _dedup(pa.concat_tables([A.read_brti(start_ms, end_ms), hot]), "ts_ms").sort_by("ts_ms")
    return _emit(t, fmt, "brti")


async def _body_table(req: Request) -> pa.Table:
    raw = await req.body()
    if not raw:
        raise HTTPException(400, "empty body (expect Parquet)")
    try:
        return pq.read_table(io.BytesIO(raw))
    except Exception as e:
        raise HTTPException(400, f"not a Parquet file: {e}")


@app.post("/btc15m/trades", dependencies=[Depends(admin_auth)])
async def post_trades(req: Request):
    t = await _body_table(req)
    missing = set(A.TRADE_SCHEMA.names) - set(t.column_names)
    if missing:
        raise HTTPException(400, f"missing columns {sorted(missing)}")
    bad = [x for x in t.column("ticker").to_pylist() if not str(x).startswith("KXBTC15M-")]
    if bad:
        raise HTTPException(400, f"non-KXBTC15M tickers, e.g. {bad[0]}")
    added = A.merge_trades(t.select(A.TRADE_SCHEMA.names))
    return {"rows_in": t.num_rows, "added_by_day": added, "added": sum(added.values())}


@app.post("/brti", dependencies=[Depends(admin_auth)])
async def post_brti(req: Request):
    t = await _body_table(req)
    missing = set(A.BRTI_SCHEMA.names) - set(t.column_names)
    if missing:
        raise HTTPException(400, f"missing columns {sorted(missing)}")
    added = A.merge_brti(t.select(A.BRTI_SCHEMA.names))
    return {"rows_in": t.num_rows, "added_by_day": added, "added": sum(added.values())}


@app.post("/btc15m/markets", dependencies=[Depends(admin_auth)])
async def post_markets(rows: list[dict]):
    cols = ["ticker", "open_time", "close_time", "floor_strike", "result", "expiration_value", "volume"]
    db = sqlite3.connect(DB_PATH, timeout=30)
    try:
        with db:
            for r in rows:
                if not str(r.get("ticker", "")).startswith("KXBTC15M-"):
                    continue
                db.execute(
                    "INSERT INTO btc15m_markets (ticker, open_time, close_time, floor_strike, result, "
                    "expiration_value, volume) VALUES (?,?,?,?,?,?,?) ON CONFLICT(ticker) DO UPDATE SET "
                    "open_time=COALESCE(excluded.open_time, open_time), close_time=COALESCE(excluded.close_time, close_time), "
                    "floor_strike=COALESCE(excluded.floor_strike, floor_strike), result=COALESCE(excluded.result, result), "
                    "expiration_value=COALESCE(excluded.expiration_value, expiration_value), "
                    "volume=COALESCE(excluded.volume, volume)",
                    tuple(r.get(c) for c in cols))
    finally:
        db.close()
    return {"upserted": len(rows)}
