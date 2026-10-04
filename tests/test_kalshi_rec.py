from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_compaction_keeps_unverified_trades_and_uses_et_day_boundary(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    service = Path(__file__).resolve().parents[1] / "services" / "kalshi_rec"
    env = {**os.environ, "KREC_DB": str(tmp_path / "hot.db"),
           "KREC_ARCHIVE": str(tmp_path / "archive")}
    code = """
import uuid
from datetime import date, datetime, timedelta
from archive import ET, read_trades
from common import connect
from compact import end_of_et_day_ms, main

dst_start = date(2026, 3, 8)
start = datetime(2026, 3, 8, tzinfo=ET).timestamp()
end = end_of_et_day_ms(dst_start) / 1000
assert end - start == 23 * 3600

old_day = datetime.now(ET).date() - timedelta(days=10)
month = 'JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC'.split()[old_day.month - 1]
prefix = f'KXBTC15M-{old_day.year % 100:02d}{month}{old_day.day:02d}'
verified, unverified = prefix + '1200-00', prefix + '1215-00'
ts_ms = int(datetime(old_day.year, old_day.month, old_day.day, 12, tzinfo=ET).timestamp() * 1000)
db = connect()
with db:
    for ticker in (verified, unverified):
        db.execute('INSERT INTO btc15m_trades VALUES (?,?,?,?,?,?,?,?,?)',
                   (str(uuid.uuid4()), ticker, ts_ms, 50.0, 50.0, 1.0, 'yes', 'ws', ts_ms))
        db.execute('INSERT INTO btc15m_markets (ticker, verified_at) VALUES (?,?)',
                   (ticker, 'verified' if ticker == verified else None))
    db.execute('INSERT INTO brti_ticks VALUES (?,?,?,?,?,?)',
               (ts_ms, 'BRTI', 68000.0, 68000.0, 1, ts_ms))
db.close()
main()
db = connect()
assert db.execute('SELECT ticker FROM btc15m_trades').fetchall() == [(unverified,)]
assert db.execute('SELECT COUNT(*) FROM brti_ticks').fetchone()[0] == 0
assert read_trades([old_day]).num_rows == 1
db.close()
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=service, env=env,
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_public_read_token_cannot_use_admin_endpoints(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    pytest.importorskip("fastapi")
    service = Path(__file__).resolve().parents[1] / "services" / "kalshi_rec"
    env = {**os.environ, "KREC_DB": str(tmp_path / "hot.db"),
           "KREC_ARCHIVE": str(tmp_path / "archive"),
           "KREC_API_TOKEN": "test-admin-token", "KREC_READ_TOKEN": "test-read-token"}
    code = """
from fastapi.testclient import TestClient
from api import app

client = TestClient(app)
reader = {'Authorization': 'Bearer test-read-token'}
admin = {'Authorization': 'Bearer test-admin-token'}
assert client.get('/health').status_code == 401
assert client.get('/health', headers={'Authorization': 'Bearer invalid'}).status_code == 401
assert client.get('/health', headers=reader).json() == {'ok': True}
assert client.get('/health', headers=admin).json() == {'ok': True}
assert client.get('/brti', params={'start_ms': 0, 'end_ms': 1, 'fmt': 'json'}, headers=reader).status_code == 200
assert client.get('/stats', headers=reader).status_code == 401
assert client.post('/brti', headers=reader, content=b'invalid').status_code == 401
assert client.post('/brti', headers=admin, content=b'invalid').status_code == 400
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=service, env=env,
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
