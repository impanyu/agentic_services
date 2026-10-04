"""Cold storage: one zstd Parquet file per ET day (by market close) for trades, per ET day for BRTI.

Measured on 2026-09-29 (3.42M KXBTC15M trades): 13.9 bytes/trade with the 16-byte
trade_id kept (vs ~281 B/row in the Mac's SQLite), 2.7 B/trade without it.

Trade columns (compact integer encoding, all lossless):
  ticker     dictionary<string>
  trade_id   fixed_size_binary(16)   (UUID bytes)
  ts_ms      int64                   exchange time
  yes_px10   int16                   yes price in tenths of a cent (99.9c -> 999)
  count100   int64                   contracts * 100 (count_fp has 2 decimals)
  taker_yes  bool                    taker_side == 'yes' (null if unknown)
BRTI columns: ts_ms int64, value float64, avg_60s_value float64, avg_60s_window_sz int16, received_at_ms int64
"""
from __future__ import annotations

import fcntl
import os
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from common import ROOT

ARCH = Path(os.environ.get("KREC_ARCHIVE", str(ROOT / "data" / "archive")))
ET = ZoneInfo("America/New_York")
MONTHS = {m: i for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)}

TRADE_SCHEMA = pa.schema([
    ("ticker", pa.dictionary(pa.int32(), pa.string())),
    ("trade_id", pa.binary(16)),
    ("ts_ms", pa.int64()),
    ("yes_px10", pa.int16()),
    ("count100", pa.int64()),
    ("taker_yes", pa.bool_()),
])
BRTI_SCHEMA = pa.schema([
    ("ts_ms", pa.int64()),
    ("value", pa.float64()),
    ("avg_60s_value", pa.float64()),
    ("avg_60s_window_sz", pa.int16()),
    ("received_at_ms", pa.int64()),
])


def ticker_close_et(ticker: str) -> datetime:
    """KXBTC15M-26OCT041830-30 -> 2026-10-04 18:30 America/New_York (market close)."""
    s = ticker.split("-")[1]
    y, mon, d, hh, mm = 2000 + int(s[0:2]), MONTHS[s[2:5]], int(s[5:7]), int(s[7:9]), int(s[9:11])
    return datetime(y, mon, d, hh, mm, tzinfo=ET)


def ticker_day(ticker: str) -> date:
    return ticker_close_et(ticker).date()


def ms_day(ts_ms: int) -> date:
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).astimezone(ET).date()


def trade_path(d: date) -> Path:
    return ARCH / "btc15m_trades" / f"{d.isoformat()}.parquet"


def brti_path(d: date) -> Path:
    return ARCH / "brti" / f"{d.isoformat()}.parquet"


@contextmanager
def locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path) + ".lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


# ---------- row <-> arrow ----------
def trades_to_table(rows) -> pa.Table:
    """rows: (trade_id, ticker, ts_ms, yes_price_cents, count, taker_side)."""
    return pa.table({
        "ticker": pa.array([r[1] for r in rows], pa.string()).dictionary_encode(),
        "trade_id": pa.array([uuid.UUID(r[0]).bytes for r in rows], pa.binary(16)),
        "ts_ms": pa.array([int(r[2]) for r in rows], pa.int64()),
        "yes_px10": pa.array([int(round(float(r[3]) * 10)) for r in rows], pa.int16()),
        "count100": pa.array([int(round(float(r[4]) * 100)) for r in rows], pa.int64()),
        "taker_yes": pa.array([None if r[5] is None else r[5] == "yes" for r in rows], pa.bool_()),
    }, schema=TRADE_SCHEMA)


def table_to_records(t: pa.Table) -> list[dict]:
    """Decode to the human form used by the API / backtests."""
    out = []
    cols = {c: t.column(c).to_pylist() for c in t.column_names}
    for i in range(t.num_rows):
        px = cols["yes_px10"][i] / 10.0
        ty = cols["taker_yes"][i]
        out.append({
            "ticker": cols["ticker"][i],
            "trade_id": str(uuid.UUID(bytes=cols["trade_id"][i])),
            "ts_ms": cols["ts_ms"][i],
            "yes_price": px,
            "no_price": round(100.0 - px, 1),
            "count": cols["count100"][i] / 100.0,
            "taker_side": None if ty is None else ("yes" if ty else "no"),
        })
    return out


def brti_to_table(rows) -> pa.Table:
    """rows: (ts_ms, value, avg_60s_value, avg_60s_window_sz, received_at_ms)."""
    return pa.table({
        "ts_ms": pa.array([int(r[0]) for r in rows], pa.int64()),
        "value": pa.array([float(r[1]) for r in rows], pa.float64()),
        "avg_60s_value": pa.array([None if r[2] is None else float(r[2]) for r in rows], pa.float64()),
        "avg_60s_window_sz": pa.array([None if r[3] is None else int(r[3]) for r in rows], pa.int16()),
        "received_at_ms": pa.array([None if r[4] is None else int(r[4]) for r in rows], pa.int64()),
    }, schema=BRTI_SCHEMA)


# ---------- merge-write (idempotent) ----------
def sort_table(t: pa.Table, sort_keys) -> pa.Table:
    """sort_by() can't sort dictionary columns: sort on a plain-string copy, keep the dictionary."""
    if "ticker" in t.column_names and pa.types.is_dictionary(t.schema.field("ticker").type):
        idx = pc.sort_indices(t.set_column(t.column_names.index("ticker"), "ticker", _plain(t.column("ticker"))),
                              sort_keys=sort_keys)
        return t.take(idx)
    return t.sort_by(sort_keys)


def _write(path: Path, t: pa.Table, sort_keys) -> None:
    t = sort_table(t, sort_keys)
    tmp = path.with_suffix(".tmp")
    pq.write_table(t, tmp, compression="zstd", compression_level=9,
                   use_dictionary=["ticker"] if "ticker" in t.column_names else False,
                   row_group_size=250_000)
    os.replace(tmp, path)


def _plain(col) -> pa.ChunkedArray:
    return col.cast(pa.string())


def merge_trades(t: pa.Table) -> dict:
    """Add trades to their day files, de-duplicating on trade_id. Returns {day: added}."""
    t = t.cast(TRADE_SCHEMA)
    names = pc.unique(_plain(t.column("ticker"))).to_pylist()
    added = {}
    for d in sorted({ticker_day(x) for x in names}):
        tick = [x for x in names if ticker_day(x) == d]
        part = t.filter(pc.is_in(_plain(t.column("ticker")), value_set=pa.array(tick)))
        p = trade_path(d)
        with locked(p):
            old = pq.read_table(p).cast(TRADE_SCHEMA) if p.exists() else TRADE_SCHEMA.empty_table()
            have = set(old.column("trade_id").to_pylist())
            mask = pa.array([b not in have for b in part.column("trade_id").to_pylist()], pa.bool_())
            new = part.filter(mask)
            # de-dup within the incoming batch too
            seen, keep = set(), []
            for b in new.column("trade_id").to_pylist():
                keep.append(b not in seen)
                seen.add(b)
            new = new.filter(pa.array(keep, pa.bool_()))
            if new.num_rows:
                merged = pa.concat_tables([old, new]).unify_dictionaries()
                merged = merged.cast(TRADE_SCHEMA)
                _write(p, merged, [("ticker", "ascending"), ("ts_ms", "ascending")])
            added[d.isoformat()] = new.num_rows
    return added


def merge_brti(t: pa.Table) -> dict:
    t = t.cast(BRTI_SCHEMA)
    days = sorted({ms_day(x) for x in t.column("ts_ms").to_pylist()})
    added = {}
    for d in days:
        lo = int(datetime(d.year, d.month, d.day, tzinfo=ET).timestamp() * 1000)
        hi = int((datetime(d.year, d.month, d.day, tzinfo=ET) + timedelta(days=1)).timestamp() * 1000)
        part = t.filter(pc.and_(pc.greater_equal(t.column("ts_ms"), lo), pc.less(t.column("ts_ms"), hi)))
        p = brti_path(d)
        with locked(p):
            old = pq.read_table(p).cast(BRTI_SCHEMA) if p.exists() else BRTI_SCHEMA.empty_table()
            have = set(old.column("ts_ms").to_pylist())
            seen, keep = set(have), []
            for x in part.column("ts_ms").to_pylist():
                keep.append(x not in seen)
                seen.add(x)
            new = part.filter(pa.array(keep, pa.bool_()))
            if new.num_rows:
                _write(p, pa.concat_tables([old, new]), [("ts_ms", "ascending")])
            added[d.isoformat()] = new.num_rows
    return added


# ---------- reads ----------
def read_trades(days: list[date], tickers: list[str] | None = None) -> pa.Table:
    parts = []
    for d in days:
        p = trade_path(d)
        if not p.exists():
            continue
        filt = [("ticker", "in", tickers)] if tickers else None
        parts.append(pq.read_table(p, filters=filt).cast(TRADE_SCHEMA))
    return pa.concat_tables(parts).unify_dictionaries() if parts else TRADE_SCHEMA.empty_table()


def read_brti(lo_ms: int, hi_ms: int) -> pa.Table:
    d, end = ms_day(lo_ms), ms_day(hi_ms)
    parts = []
    while d <= end:
        p = brti_path(d)
        if p.exists():
            parts.append(pq.read_table(p, filters=[("ts_ms", ">=", lo_ms), ("ts_ms", "<", hi_ms)]))
        d += timedelta(days=1)
    return pa.concat_tables(parts) if parts else BRTI_SCHEMA.empty_table()


def usage() -> dict:
    out = {}
    for kind in ("btc15m_trades", "brti"):
        fs = sorted((ARCH / kind).glob("*.parquet")) if (ARCH / kind).exists() else []
        out[kind] = {"files": len(fs), "bytes": sum(f.stat().st_size for f in fs),
                     "first": fs[0].stem if fs else None, "last": fs[-1].stem if fs else None}
    return out
