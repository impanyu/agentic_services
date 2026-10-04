# kalshi_rec — Kalshi KXBTC15M trade tape + BRTI recorder

Records every trade of Kalshi's 15-minute BTC market (series `KXBTC15M`) and the
1 Hz CF Benchmarks BRTI index (with Kalshi's 60-second rolling mean) over one
Kalshi WebSocket, verifies the tape daily against Kalshi's REST API, compacts
history into per-day Parquet + zstd files (~14 bytes/trade incl. trade_id,
~20x smaller than row-oriented SQLite), and exposes a token-protected
read/write API. Self-contained: does not import `src/agentic_services`.

## Files

| file | role |
|---|---|
| `recorder.py` | WebSocket recorder: `cfbenchmarks_value` (BRTI) + `trade` (all markets, keeps `KXBTC15M-*`). 1 s buffered writes, reconnects on 30 s BRTI silence, logs sessions in `ws_sessions`. |
| `verify.py` | Daily REST cross-check per market by `trade_id`; back-fills anything the WS missed (`source='rest'`), records missing/extra/mismatch counts in `btc15m_markets`. Public endpoints, no auth. |
| `compact.py` | Moves verified trade markets and BRTI ticks older than `KREC_KEEP_HOT_DAYS` (default 3 ET calendar days) from SQLite into `data/archive/{btc15m_trades,brti}/YYYY-MM-DD.parquet`, then deletes them from SQLite and vacuums. Unverified trades remain hot until REST reconciliation succeeds. |
| `api.py` | FastAPI on `127.0.0.1:8765`; read-only access uses `KREC_READ_TOKEN`, uploads and stats use `KREC_API_TOKEN`. Merges hot SQLite + archive transparently. |
| `archive.py` | Parquet schema/encoding, idempotent merge-write, reads. |
| `common.py` | Config, Kalshi RSA-PSS auth, SQLite schema. |
| `systemd/` | `kalshi-rec.service`, `kalshi-rec-api.service`, `kalshi-rec-nightly.{service,timer}` (09:30 UTC). |

## API

Reads (`fmt=parquet` default, `fmt=json` for decoded rows):
`GET /health`, `GET /stats`, `GET /btc15m/tickers?day=YYYY-MM-DD`,
`GET /btc15m/markets?start=&end=`,
`GET /btc15m/trades?ticker=...` or `?day=YYYY-MM-DD` (ET, by market close),
`GET /brti?start_ms=&end_ms=` (max 40 days).

The owner-only HTTPS URL is `https://api.aisoup.net/kalshi-rec/v1/`. Supply
`Authorization: Bearer <token>` on every request, including `/health`.
All existing GET and POST routes are exposed under that versioned prefix.
`GET /openapi.json` returns the full API contract with the public base URL;
it and `/stats` require the management token. The read token cannot call
uploads, `/stats`, or `/openapi.json`; uploads require `KREC_API_TOKEN`.
Do not issue either token to customers until data distribution rights and the
payment flow are settled. The company catalog does not list this service yet.
The personal history viewer is at `https://api.aisoup.net/kalshi-rec/ui/`;
it accepts only the read token, keeps it in page memory, and has no link from
the company website.

Writes (idempotent; Parquet body in the archive schema):
`POST /btc15m/trades` (dedup by `trade_id`), `POST /brti` (dedup by `ts_ms`),
`POST /btc15m/markets` (JSON upsert).

Archive trade encoding: `ticker` dictionary, `trade_id` 16-byte UUID,
`ts_ms` int64, `yes_px10` int16 (tenths of a cent), `count100` int64
(contracts x 100), `taker_yes` bool.

## Deployment (agentic-wiki VM)

Deployed at `/home/ypan12/kalshi_rec` (user `ypan12`), not from this checkout.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && chmod 600 .env        # fill KALSHI_API_KEY_ID, key path, both API tokens
sudo cp systemd/* /etc/systemd/system/ && sudo systemctl daemon-reload
sudo systemctl enable --now kalshi-rec kalshi-rec-api kalshi-rec-nightly.timer
```

To update: copy changed files to `/home/ypan12/kalshi_rec/` and
`sudo systemctl restart kalshi-rec kalshi-rec-api`.
