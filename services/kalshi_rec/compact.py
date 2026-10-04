"""Nightly: move finished ET days from the hot SQLite into the Parquet archive.

A day D (by market close, ET) is compacted when D <= today_ET - KEEP_HOT_DAYS.
Rows are written to the archive (idempotent merge on trade_id / ts_ms) and only
then deleted from SQLite. BRTI is compacted by ET day the same way.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from archive import ET, brti_to_table, merge_brti, merge_trades, ticker_day, trades_to_table
from common import connect

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("kcompact")

KEEP_HOT_DAYS = int(__import__("os").environ.get("KREC_KEEP_HOT_DAYS", "3"))


def main() -> None:
    db = connect()
    today = datetime.now(ET).date()
    cutoff = today - timedelta(days=KEEP_HOT_DAYS)

    tickers = [r[0] for r in db.execute("SELECT DISTINCT ticker FROM btc15m_trades")]
    by_day: dict = {}
    for tk in tickers:
        d = ticker_day(tk)
        if d <= cutoff:
            by_day.setdefault(d, []).append(tk)
    for d in sorted(by_day):
        tks = by_day[d]
        q = ",".join("?" * len(tks))
        rows = db.execute(f"SELECT trade_id, ticker, ts_ms, yes_price, count, taker_side FROM btc15m_trades "
                          f"WHERE ticker IN ({q})", tks).fetchall()
        added = merge_trades(trades_to_table(rows))
        with db:
            db.execute(f"DELETE FROM btc15m_trades WHERE ticker IN ({q})", tks)
        log.info("trades %s: %d rows → archive (+%s new), deleted from hot", d, len(rows), added)

    lo_ms = int(datetime(cutoff.year, cutoff.month, cutoff.day, tzinfo=ET).timestamp() * 1000) \
        + 86_400_000  # end of cutoff day
    rows = db.execute("SELECT ts_ms, value, avg_60s_value, avg_60s_window_sz, received_at_ms "
                      "FROM brti_ticks WHERE ts_ms < ?", (lo_ms,)).fetchall()
    if rows:
        added = merge_brti(brti_to_table(rows))
        with db:
            db.execute("DELETE FROM brti_ticks WHERE ts_ms < ?", (lo_ms,))
        log.info("brti: %d rows → archive %s", len(rows), added)

    if by_day or rows:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.execute("VACUUM")
        log.info("vacuumed hot db")
    db.close()


if __name__ == "__main__":
    main()
