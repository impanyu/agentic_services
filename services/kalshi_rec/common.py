"""Shared config, Kalshi auth and SQLite schema for the KXBTC15M / BRTI recorder."""
from __future__ import annotations

import base64
import os
import sqlite3
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ROOT = Path(__file__).resolve().parent
DB_PATH = os.environ.get("KREC_DB", str(ROOT / "data" / "kalshi_rec.db"))

WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"
WS_PATH = "/trade-api/ws/v2"
REST_URL = "https://api.elections.kalshi.com/trade-api/v2"

SERIES = "KXBTC15M"
TICKER_PREFIX = SERIES + "-"
BRTI_INDEX = "BRTI"


def load_env(path: Path = ROOT / ".env") -> None:
    """Minimal KEY=VALUE loader (no override of already-set env vars)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("'\""))


class KalshiAuth:
    """RSA-PSS SHA256 over f"{ts_ms}{METHOD}{path}" (same as backend/app/kalshi/auth.py)."""

    def __init__(self, key_id: str, private_key_path: str) -> None:
        key = serialization.load_pem_private_key(Path(private_key_path).read_bytes(), password=None)
        if not isinstance(key, rsa.RSAPrivateKey):
            raise ValueError(f"expected RSA private key, got {type(key)}")
        self.key_id = key_id
        self.private_key = key

    @classmethod
    def from_env(cls) -> "KalshiAuth":
        load_env()
        kid = os.environ.get("KALSHI_API_KEY_ID", "")
        kp = os.environ.get("KALSHI_PRIVATE_KEY_PATH", "")
        if not kid or not kp:
            raise SystemExit("KALSHI_API_KEY_ID / KALSHI_PRIVATE_KEY_PATH not set (see .env)")
        if not Path(kp).is_absolute():
            kp = str(ROOT / kp)
        return cls(kid, kp)

    def headers(self, method: str, path: str) -> dict[str, str]:
        ts = str(int(time.time() * 1000))
        sig = self.private_key.sign(
            (ts + method + path).encode(),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256(),
        )
        return {"KALSHI-ACCESS-KEY": self.key_id,
                "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
                "KALSHI-ACCESS-TIMESTAMP": ts}


SCHEMA = """
-- 1 row per BRTI tick from the cfbenchmarks_value channel (same layout as the Mac's brti_ticks.db)
CREATE TABLE IF NOT EXISTS brti_ticks (
    ts_ms             INTEGER PRIMARY KEY,   -- index timestamp (ms)
    index_id          TEXT NOT NULL,
    value             REAL NOT NULL,         -- BRTI (BTC/USD real-time index)
    avg_60s_value     REAL,                  -- Kalshi-provided 60-SECOND rolling mean
    avg_60s_window_sz INTEGER,
    received_at_ms    INTEGER NOT NULL       -- Kalshi received_at
);

-- every KXBTC15M trade; source='ws' (live) or 'rest' (back-filled by the verifier)
CREATE TABLE IF NOT EXISTS btc15m_trades (
    trade_id     TEXT PRIMARY KEY,
    ticker       TEXT NOT NULL,
    ts_ms        INTEGER NOT NULL,           -- exchange trade time (ms)
    yes_price    REAL NOT NULL,              -- cents, e.g. 99.9
    no_price     REAL NOT NULL,
    count        REAL NOT NULL,              -- contracts (count_fp)
    taker_side   TEXT,
    source       TEXT NOT NULL,
    recv_ms      INTEGER                     -- our local receive time (ws only)
);
CREATE INDEX IF NOT EXISTS idx_btc15m_trades_ticker_ts ON btc15m_trades(ticker, ts_ms);

-- market metadata + per-market verification result
CREATE TABLE IF NOT EXISTS btc15m_markets (
    ticker           TEXT PRIMARY KEY,
    open_time        TEXT,
    close_time       TEXT,
    floor_strike     REAL,
    result           TEXT,
    expiration_value REAL,
    volume           REAL,                   -- Kalshi volume_fp
    verified_at      TEXT,
    rest_n           INTEGER,                -- trades per REST
    ws_n             INTEGER,                -- of those, already captured by WS
    missing_n        INTEGER,                -- REST trades the WS missed (now back-filled)
    extra_n          INTEGER,                -- WS trades REST does not return
    rest_ct          REAL,
    ws_ct            REAL,
    mismatch_n       INTEGER                 -- same trade_id, different price/count/side
);

-- WS session log (connect/disconnect), so gaps are visible without the verifier
CREATE TABLE IF NOT EXISTS ws_sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    connected_ms  INTEGER,
    closed_ms     INTEGER,
    reason        TEXT,
    n_trades      INTEGER,
    n_brti        INTEGER
);
"""


def connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(db_path, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA busy_timeout=30000")
    db.executescript(SCHEMA)
    return db


def cents(dollars) -> float | None:
    if dollars is None:
        return None
    return round(float(dollars) * 100.0, 4)
