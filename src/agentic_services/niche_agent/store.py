from __future__ import annotations

import hashlib
from contextlib import nullcontext
import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from ..niche_discovery import NicheStore, NicheDraft, WEIGHTS, now


class DailyBudgetExhausted(RuntimeError):
    pass


class ManagerStore(NicheStore):
    """SQLite transactions are the authority for leases, receipts and revisions."""

    def __init__(self, path: Path):
        super().__init__(path)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS manager_worker_health (
                    id INTEGER PRIMARY KEY CHECK(id=1), heartbeat REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS manager_tool_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    tool TEXT NOT NULL, status TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS manager_events (
                    id TEXT PRIMARY KEY, dedupe TEXT UNIQUE NOT NULL, kind TEXT NOT NULL,
                    payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                    available REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    run_id TEXT, created TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS manager_pending ON manager_events(status,available);
                CREATE TABLE IF NOT EXISTS manager_lease (
                    id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS manager_runs (
                    id TEXT PRIMARY KEY, status TEXT NOT NULL, started TEXT NOT NULL,
                    finished TEXT, result TEXT);
                CREATE TABLE IF NOT EXISTS manager_memory (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL, updated TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS manager_receipts (
                    operation TEXT PRIMARY KEY, digest TEXT NOT NULL, result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS manager_revisions (
                    niche_id TEXT PRIMARY KEY, revision INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS manager_revision_log (
                    niche_id TEXT NOT NULL, revision INTEGER NOT NULL, metadata TEXT NOT NULL,
                    created TEXT NOT NULL, PRIMARY KEY(niche_id,revision));
                CREATE TABLE IF NOT EXISTS manager_budget (
                    day TEXT PRIMARY KEY, requests INTEGER NOT NULL, tokens INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS manager_subscriptions (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, secret_env TEXT NOT NULL,
                    interval INTEGER NOT NULL, enabled INTEGER NOT NULL, next_poll REAL NOT NULL);
            ''')

    def enqueue(self, kind: str, payload: dict, dedupe: str, *, available: float | None = None) -> str:
        raw = json.dumps(payload, sort_keys=True)
        if len(raw.encode()) > 16000:
            raise ValueError('Event payload too large')
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO manager_events(id,dedupe,kind,payload,available,created) VALUES(?,?,?,?,?,?)',
                       ('evt_' + uuid.uuid4().hex, dedupe, kind, raw, available or time.time(), now()))
            return db.execute('SELECT id FROM manager_events WHERE dedupe=?', (dedupe,)).fetchone()[0]

    def claim(self, lease_seconds: int = 180, limit: int = 8) -> tuple[str, list[dict]] | None:
        timestamp = time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            lease = db.execute('SELECT * FROM manager_lease WHERE id=1').fetchone()
            if lease and lease['expires'] > timestamp:
                return None
            # Recovery is safe only after the previous manager lease expired.
            db.execute("UPDATE manager_events SET status=CASE WHEN attempts>=3 THEN 'dead' ELSE 'pending' END,run_id=NULL WHERE status='leased'")
            db.execute("UPDATE manager_runs SET status='interrupted',finished=? WHERE status='running'", (now(),))
            rows = db.execute("SELECT * FROM manager_events WHERE status='pending' AND available<=? ORDER BY available,id LIMIT ?", (timestamp, limit)).fetchall()
            if not rows:
                return None
            owner = 'mgr_' + uuid.uuid4().hex
            db.execute('INSERT OR REPLACE INTO manager_lease VALUES(1,?,?)', (owner, timestamp + lease_seconds))
            db.execute('INSERT INTO manager_runs(id,status,started) VALUES(?,?,?)', (owner, 'running', now()))
            for row in rows:
                db.execute("UPDATE manager_events SET status='leased',run_id=?,attempts=attempts+1 WHERE id=?", (owner, row['id']))
            return owner, [{**dict(row), 'payload': json.loads(row['payload'])} for row in rows]

    def assert_owner(self, db, owner: str) -> None:
        lease = db.execute('SELECT * FROM manager_lease WHERE id=1').fetchone()
        if not lease or lease['owner'] != owner or lease['expires'] <= time.time():
            raise RuntimeError('Manager lease lost')

    def heartbeat(self, owner: str) -> None:
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.assert_owner(db, owner)
            db.execute('UPDATE manager_lease SET expires=? WHERE owner=?', (time.time() + 180, owner))

    def finish(self, owner: str, *, success: bool, result: str, budget_wait: bool = False) -> None:
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.assert_owner(db, owner)
            db.execute('UPDATE manager_runs SET status=?,finished=?,result=? WHERE id=?',
                       ('completed' if success else 'failed', now(), result[:8000], owner))
            event_ids = [r[0] for r in db.execute('SELECT id FROM manager_events WHERE run_id=?', (owner,))]
            db.execute("UPDATE manager_events SET status=CASE WHEN ? THEN 'handled' WHEN attempts>=3 THEN 'dead' ELSE 'pending' END,available=?,run_id=NULL WHERE run_id=?",
                       (success, time.time() + (0 if success else 900), owner))
            if budget_wait:
                tomorrow = (int(time.time() // 86400) + 1) * 86400
                for event_id in event_ids:
                    db.execute("UPDATE manager_events SET status='pending',attempts=MAX(0,attempts-1),available=? WHERE id=?",
                               (tomorrow, event_id))
            db.execute('DELETE FROM manager_lease WHERE owner=?', (owner,))

    def remember(self, owner: str, key: str, value: str) -> None:
        if not 1 <= len(key) <= 160 or len(value) > 8000:
            raise ValueError('Memory key/value too large')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.assert_owner(db, owner)
            db.execute('INSERT INTO manager_memory VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated=excluded.updated', (key, value, now()))

    def memories(self, query: str = '') -> list[dict]:
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM manager_memory WHERE key LIKE ? OR value LIKE ? ORDER BY updated DESC LIMIT 30', ('%' + query + '%', '%' + query + '%'))]

    def reserve(self, owner: str, tokens: int, max_requests: int, max_tokens: int) -> None:
        day = datetime.now(UTC).date().isoformat()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.assert_owner(db, owner)
            db.execute('INSERT OR IGNORE INTO manager_budget VALUES(?,0,0)', (day,))
            row = db.execute('SELECT * FROM manager_budget WHERE day=?', (day,)).fetchone()
            if row['requests'] >= max_requests or row['tokens'] + tokens > max_tokens:
                raise DailyBudgetExhausted('Daily model budget exhausted')
            db.execute('UPDATE manager_budget SET requests=requests+1,tokens=tokens+? WHERE day=?', (tokens, day))

    def settle_reservation(self, owner: str, reserved: int, actual: int) -> None:
        # Successful calls release only the unused reservation. Failed/unknown calls
        # keep their full reservation to avoid overspending on uncertain delivery.
        if actual < 0 or actual > reserved:
            return
        day = datetime.now(UTC).date().isoformat()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.assert_owner(db, owner)
            db.execute('UPDATE manager_budget SET tokens=MAX(0,tokens-?) WHERE day=?', (reserved - actual, day))

    def revise(self, owner: str, operation: str, niche_id: str | None, expected_revision: int,
               draft: NicheDraft, rationale: str, confidence: str, counterevidence: str,
               retire_ids: list[str] | None = None, *, transaction=None) -> dict:
        """Atomic create/update/merge/split primitive. Receipt replay never writes twice."""
        if not operation or len(operation) > 160 or len(rationale) < 30:
            raise ValueError('A stable operation id and substantive rationale are required')
        if confidence not in {'low', 'medium', 'high'} or len(counterevidence) < 20:
            raise ValueError('State confidence and counterevidence or explicit evidence gaps')
        retirement = list(dict.fromkeys(retire_ids or []))
        if len(retirement) > 20 or niche_id in retirement:
            raise ValueError('Invalid retirement set')
        payload = {'id': niche_id, 'revision': expected_revision, 'draft': draft.model_dump(),
                   'rationale': rationale, 'confidence': confidence, 'counterevidence': counterevidence, 'retire': retirement}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        ids = list(dict.fromkeys(draft.signal_ids))
        with (nullcontext(transaction) if transaction is not None else self.connect()) as db:
            if not db.in_transaction:
                db.execute('BEGIN IMMEDIATE')
            self.assert_owner(db, owner)
            prior = db.execute('SELECT * FROM manager_receipts WHERE operation=?', (operation,)).fetchone()
            if prior:
                if prior['digest'] != digest:
                    raise ValueError('Operation id reused with different input')
                result = json.loads(prior['result'])
                if not db.execute('SELECT 1 FROM niches WHERE id=?', (result['id'],)).fetchone():
                    raise ValueError('Previous operation result was removed; reassess current evidence')
                return result
            current = db.execute('SELECT * FROM niches WHERE id=?', (niche_id,)).fetchone() if niche_id else None
            revision_row = db.execute('SELECT revision FROM manager_revisions WHERE niche_id=?', (niche_id,)).fetchone()
            revision = revision_row[0] if revision_row else 0
            if (niche_id and not current) or revision != expected_revision or (not niche_id and expected_revision != 0):
                raise ValueError('Niche missing or revision conflict; reload before editing')
            signals = db.execute(f"SELECT * FROM niche_signals WHERE id IN ({','.join('?' for _ in ids)})", ids).fetchall()
            if len(signals) != len(ids):
                raise ValueError('Unknown or deleted evidence')
            if draft.publish and (len(ids) < 3 or len({r['source_domain'] for r in signals}) < 2 or confidence == 'low'):
                raise ValueError('Publication requires 3 signals, 2 domains and at least medium confidence')
            target = niche_id or 'niche_' + uuid.uuid4().hex
            score = round(sum(getattr(draft, k) * weight / 5 for k, weight in WEIGHTS.items()))
            db.execute('INSERT INTO niches VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET draft_json=excluded.draft_json,score=excluded.score,published=excluded.published,updated_at=excluded.updated_at',
                       (target, draft.model_dump_json(), score, int(draft.publish), now(), now()))
            db.execute('DELETE FROM niche_links WHERE niche_id=?', (target,))
            db.executemany('INSERT INTO niche_links VALUES(?,?)', [(target, i) for i in ids])
            metadata = {'rationale': rationale, 'confidence': confidence, 'counterevidence': counterevidence,
                        'operation': operation, 'draft': draft.model_dump(), 'signalIds': ids, 'retiredIds': retirement}
            db.execute('INSERT OR REPLACE INTO manager_revisions VALUES(?,?)', (target, revision + 1))
            db.execute('INSERT INTO manager_revision_log VALUES(?,?,?,?)', (target, revision + 1, json.dumps(metadata), now()))
            for retired in retirement:
                if not db.execute('SELECT 1 FROM niches WHERE id=?', (retired,)).fetchone():
                    raise ValueError('Unknown niche to retire')
                db.execute('UPDATE niches SET published=0,updated_at=? WHERE id=?', (now(), retired))
                db.execute('INSERT INTO manager_revisions VALUES(?,1) ON CONFLICT(niche_id) DO UPDATE SET revision=revision+1', (retired,))
            result = {'id': target, 'revision': revision + 1, 'published': draft.publish, 'retiredIds': retirement}
            db.execute('INSERT INTO manager_receipts VALUES(?,?,?)', (operation, digest, json.dumps(result)))
            return result

    def split(self, owner: str, operation: str, niche_id: str, expected_revision: int,
              children: list[NicheDraft], rationale: str, confidence: str, counterevidence: str) -> dict:
        if not 2 <= len(children) <= 6:
            raise ValueError('Split requires 2..6 narrower drafts')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.assert_owner(db, owner)
            row = db.execute('SELECT draft_json FROM niches WHERE id=?', (niche_id,)).fetchone()
            if not row:
                raise ValueError('Unknown parent niche')
            parent = NicheDraft.model_validate_json(row[0]).model_copy(update={'publish': False})
            results = []
            for i, child in enumerate(children):
                results.append(self.revise(owner, f'{operation}:child:{i}', None, 0, child,
                                           rationale, confidence, counterevidence, transaction=db))
            retired = self.revise(owner, f'{operation}:parent', niche_id, expected_revision, parent,
                                  rationale, confidence, counterevidence, transaction=db)
            return {'children': results, 'parent': retired}

    def catalog(self, query: str = '') -> list[dict]:
        with self.connect() as db:
            rows = db.execute('SELECT n.id,n.published,n.draft_json,COALESCE(r.revision,0) revision FROM niches n LEFT JOIN manager_revisions r ON r.niche_id=n.id ORDER BY n.updated_at DESC LIMIT 100').fetchall()
        return [{**dict(r), 'draft_json': json.loads(r['draft_json'])} for r in rows if query.lower() in r['draft_json'].lower()]

    def subscriptions(self) -> list[dict]:
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM manager_subscriptions')]

    def subscribe(self, owner: str | None, identifier: str, source: str, secret_env: str, interval: int, enabled: bool) -> dict:
        import re
        if source not in {'github', 'hacker-news', 'gdelt', 'reddit', 'search'}:
            raise ValueError('Unregistered source')
        if not re.fullmatch(r'[a-z0-9-]{1,64}', identifier) or not re.fullmatch(r'NICHE_CALLBACK_[A-Z0-9_]+', secret_env):
            raise ValueError('Invalid subscription id or secret environment reference')
        if not 3600 <= interval <= 604800:
            raise ValueError('Polling interval must be between 1 hour and 7 days')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if owner is not None:
                self.assert_owner(db, owner)
            db.execute('INSERT INTO manager_subscriptions VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET source=excluded.source,secret_env=excluded.secret_env,interval=excluded.interval,enabled=excluded.enabled',
                       (identifier, source, secret_env, interval, int(enabled), time.time()))
        return {'id': identifier, 'callbackPath': '/niche-discovery/v1/source-events/' + identifier,
                'enabled': enabled, 'externalRegistration': 'not_registered'}

    def schedule(self, tick_seconds: int) -> None:
        timestamp = time.time()
        self.enqueue('schedule.tick', {}, 'tick:' + str(int(timestamp // tick_seconds)))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in db.execute('SELECT * FROM manager_subscriptions WHERE enabled=1 AND next_poll<=?', (timestamp,)).fetchall():
                dedupe = f"poll:{row['id']}:{int(timestamp // row['interval'])}"
                db.execute('INSERT OR IGNORE INTO manager_events(id,dedupe,kind,payload,available,created) VALUES(?,?,?,?,?,?)',
                           ('evt_' + uuid.uuid4().hex, dedupe, 'subscription.poll', json.dumps({'source': row['source'], 'subscription': row['id']}), timestamp, now()))
                db.execute('UPDATE manager_subscriptions SET next_poll=? WHERE id=?', (timestamp + row['interval'], row['id']))

    def status(self) -> dict:
        with self.connect() as db:
            return {'workerHeartbeat': r[0] if (r := db.execute('SELECT heartbeat FROM manager_worker_health WHERE id=1').fetchone()) else None,
                    'queue': {r[0]: r[1] for r in db.execute('SELECT status,COUNT(*) FROM manager_events GROUP BY status')},
                    'lease': dict(r) if (r := db.execute('SELECT * FROM manager_lease WHERE id=1').fetchone()) else None,
                    'toolCalls': [dict(r) for r in db.execute('SELECT * FROM manager_tool_calls ORDER BY id DESC LIMIT 100')],
                    'runs': [dict(r) for r in db.execute('SELECT * FROM manager_runs ORDER BY started DESC LIMIT 20')],
                    'budgets': [dict(r) for r in db.execute('SELECT * FROM manager_budget ORDER BY day DESC LIMIT 7')]}
