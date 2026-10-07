"""Private, durable research objectives. Queries never become source evidence."""
import hashlib
import json
import time
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..config import Settings
from ..niche_discovery import now


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    query: str = Field(min_length=3, max_length=500)
    category: str | None = Field(default=None, max_length=80)
    buyer: str | None = Field(default=None, max_length=120)
    region: str | None = Field(default=None, max_length=120)
    @field_validator('query')
    @classmethod
    def meaningful(cls, value):
        if len(value.strip()) < 3:
            raise ValueError('Enter a specific research objective')
        return value.strip()


class ResearchStore:
    def init_research(self, db):
        db.executescript('''
            CREATE TABLE IF NOT EXISTS niche_research (
                id TEXT PRIMARY KEY, principal TEXT NOT NULL, operation TEXT NOT NULL,
                digest TEXT NOT NULL, criteria TEXT NOT NULL, status TEXT NOT NULL,
                event_id TEXT NOT NULL, result TEXT, created REAL NOT NULL, updated REAL NOT NULL,
                UNIQUE(principal,operation));
            CREATE TABLE IF NOT EXISTS niche_research_aliases (
                principal TEXT NOT NULL, operation TEXT NOT NULL, digest TEXT NOT NULL,
                research_id TEXT NOT NULL, PRIMARY KEY(principal,operation));
            CREATE TABLE IF NOT EXISTS niche_research_quota (
                principal TEXT NOT NULL, day TEXT NOT NULL, count INTEGER NOT NULL,
                PRIMARY KEY(principal,day));
        ''')

    def request_research(self, principal, operation, criteria, quota=3):
        raw = json.dumps(criteria, sort_keys=True)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        day = datetime.now(UTC).date().isoformat()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT a.digest,a.research_id AS id FROM niche_research_aliases a JOIN niche_research r ON r.id=a.research_id WHERE a.principal=? AND a.operation=?', (principal, operation)).fetchone()
            if old:
                if old['digest'] != digest:
                    raise HTTPException(409, 'Idempotency-Key was used with different criteria')
                return old['id']
            # Same-day identical requests share a job, including a pending one.
            old = db.execute('SELECT id FROM niche_research WHERE principal=? AND digest=? AND created>? ORDER BY created DESC LIMIT 1', (principal, digest, time.time()-86400)).fetchone()
            if old:
                db.execute('INSERT INTO niche_research_aliases VALUES(?,?,?,?)',(principal,operation,digest,old[0]))
                return old[0]
            used = db.execute('SELECT count FROM niche_research_quota WHERE principal=? AND day=?', (principal, day)).fetchone()
            if used and used[0] >= quota:
                raise HTTPException(429, 'Daily research quota reached; resets at 00:00 UTC', headers={'Retry-After':str(int((int(time.time()//86400)+1)*86400-time.time()))})
            pending = db.execute("SELECT COUNT(*) FROM niche_research WHERE status IN ('queued','running','waiting_for_budget')").fetchone()[0]
            if pending >= 100:
                raise HTTPException(503, 'Research queue is full; retry later')
            identifier, event = 'research_'+uuid.uuid4().hex, 'evt_'+uuid.uuid4().hex
            db.execute('INSERT INTO niche_research VALUES(?,?,?,?,?,?,?,?,?,?)',
                (identifier, principal, operation, digest, raw, 'queued', event, None, time.time(), time.time()))
            db.execute('INSERT INTO niche_research_aliases VALUES(?,?,?,?)',(principal,operation,digest,identifier))
            db.execute('INSERT INTO manager_events(id,dedupe,kind,payload,available,created) VALUES(?,?,?,?,?,?)',
                (event, identifier, 'research.request', json.dumps({'requestId':identifier,'criteria':criteria}), time.time(), now()))
            db.execute('INSERT INTO niche_research_quota VALUES(?,?,1) ON CONFLICT(principal,day) DO UPDATE SET count=count+1', (principal,day))
        return identifier

    def research_result(self, identifier, principal):
        with self.connect() as db:
            row = db.execute('SELECT * FROM niche_research WHERE id=? AND principal=? AND created>?', (identifier,principal,time.time()-30*86400)).fetchone()
            if not row:
                raise HTTPException(404, 'Research request not found')
            event = db.execute('SELECT available FROM manager_events WHERE id=?', (row['event_id'],)).fetchone()
        result = json.loads(row['result']) if row['result'] else {'nicheIds':[], 'summary':None}
        records = []
        for nid in result['nicheIds']:
            try:
                record = self.niche(nid, include_private=True)
                with self.connect() as db:
                    published = bool(db.execute('SELECT published FROM niches WHERE id=?',(nid,)).fetchone()[0])
                records.append({**record,'published':published,'provisional':not published})
            except HTTPException:
                continue  # Removed evidence/records must not be served from a cached report.
        return {'id':identifier,'status':row['status'],'criteria':json.loads(row['criteria']),
                'summary':result['summary'],'niches':records,'statusUrl':'/niche-discovery/v1/research/'+identifier,
                'nextAttemptAt':event[0] if event and row['status']=='waiting_for_budget' else None}

    def complete_research(self, owner, identifier, niche_ids, outcome, summary):
        if outcome not in {'completed','insufficient_evidence','blocked'} or not 20 <= len(summary) <= 2000:
            raise ValueError('Provide a terminal outcome and substantive summary')
        ids = list(dict.fromkeys(niche_ids))
        if len(ids)>5 or (outcome=='completed' and not ids) or (outcome!='completed' and ids):
            raise ValueError('Completed requests require 1..5 records; other outcomes require no records')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE'); self.assert_owner(db,owner)
            row = db.execute('SELECT r.* FROM niche_research r JOIN manager_events e ON e.id=r.event_id WHERE r.id=? AND e.run_id=? AND e.status=\'leased\'',(identifier,owner)).fetchone()
            if not row:
                raise ValueError('Research request is not leased to this run')
            for nid in ids:
                # Only public knowledge or records assessed in this run may be returned.
                record=db.execute('SELECT published FROM niches WHERE id=?',(nid,)).fetchone()
                revisions=db.execute('SELECT metadata FROM manager_revision_log WHERE niche_id=?',(nid,)).fetchall()
                if not record or (not record['published'] and not any(json.loads(r[0]).get('runId')==owner for r in revisions)):
                    raise ValueError('Assess this private record in the current run before returning it')
            result = json.dumps({'nicheIds':ids,'summary':summary})
            if row['result'] and row['result']!=result:
                raise ValueError('Research outcome already committed')
            db.execute('UPDATE niche_research SET status=?,result=?,updated=? WHERE id=?',(outcome,result,time.time(),identifier))
        return {'id':identifier,'status':outcome,'nicheIds':ids}

    def prune_research(self):
        with self.connect() as db:
            # Delete query payloads and principal identifiers after 30 days. Retain
            # idempotency receipts until the same retention boundary.
            events = [r[0] for r in db.execute('SELECT event_id FROM niche_research WHERE created<?',(time.time()-30*86400,))]
            for eid in events:
                db.execute("DELETE FROM manager_events WHERE id=? AND status!='leased'",(eid,))
            db.execute("DELETE FROM niche_research WHERE created<? AND status!='running'",(time.time()-30*86400,))
            db.execute('DELETE FROM niche_research_aliases WHERE research_id NOT IN (SELECT id FROM niche_research)')
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='agent_sessions'").fetchone():
                cutoff=datetime.fromtimestamp(time.time()-30*86400,UTC).strftime('%Y-%m-%d %H:%M:%S')
                db.execute("DELETE FROM agent_messages WHERE session_id IN (SELECT session_id FROM agent_sessions WHERE created_at<? AND session_id LIKE 'niche-manager:%')",(cutoff,))
                db.execute("DELETE FROM agent_sessions WHERE created_at<? AND session_id LIKE 'niche-manager:%'",(cutoff,))
            db.execute('DELETE FROM niche_research_quota WHERE day<?',((datetime.now(UTC).date()).isoformat(),))


def create_research_router(settings: Settings):
    from .store import ManagerStore
    import os
    router = APIRouter(prefix='/niche-discovery/v1')
    store = ManagerStore(settings.database_path)

    @router.get('/sources')
    def sources():
        from .runtime import source_status
        from .sources import source_registry
        from .websub import public_status
        status=source_status(store)
        return {'configured':status['configured'],'adapters':source_registry(),
                'recentCollections':[{**r,'result':{k:v for k,v in r['result'].items() if k in {'source','status','signalsAdded','examined','errorType','limitation'}}} for r in status['recentCollections']],'subscriptions':public_status(store),
                'note':'Configured sources are not necessarily collecting. Counts are sampled signals, not paid-demand validation.'}

    return router
