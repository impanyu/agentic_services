"""One-use, S256-bound transfer from the system browser to the iOS web view."""
import base64
import hashlib
import hmac
import secrets
import time

from fastapi import HTTPException
from pydantic import BaseModel, Field

CALLBACK = 'net.aisoup.photoscout://auth'


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')


class NativeExchange(BaseModel):
    code: str = Field(pattern=r'^[A-Za-z0-9_-]{43}$')
    verifier: str = Field(pattern=r'^[A-Za-z0-9._~-]{43,128}$')


class NativeAuth:
    def __init__(self, db):
        self.db = db
        with db() as d:
            d.execute('CREATE TABLE IF NOT EXISTS photo_native_oauth (state TEXT PRIMARY KEY, challenge TEXT NOT NULL, expires REAL NOT NULL)')
            d.execute('CREATE TABLE IF NOT EXISTS photo_native_codes (hash TEXT PRIMARY KEY, challenge TEXT NOT NULL, session_hash TEXT NOT NULL, expires REAL NOT NULL)')

    def begin(self, state, proof):
        with self.db() as d:
            d.execute('DELETE FROM photo_native_oauth WHERE expires<?', (time.time(),))
            d.execute('INSERT INTO photo_native_oauth VALUES(?,?,?)', (digest(state), proof, time.time() + 600))

    def finish(self, state, session_hash=None):
        with self.db() as d:
            d.execute('BEGIN IMMEDIATE')
            row = d.execute('SELECT challenge FROM photo_native_oauth WHERE state=? AND expires>?', (digest(state), time.time())).fetchone()
            d.execute('DELETE FROM photo_native_oauth WHERE state=?', (digest(state),))
            if not row:
                return None
            if session_hash is None:
                return CALLBACK + '?error=cancelled'
            code = secrets.token_urlsafe(32)
            d.execute('DELETE FROM photo_native_codes WHERE expires<?', (time.time(),))
            d.execute('INSERT INTO photo_native_codes VALUES(?,?,?,?)', (digest(code), row['challenge'], session_hash, time.time() + 60))
            return CALLBACK + '?code=' + code

    def exchange(self, payload):
        with self.db() as d:
            d.execute('BEGIN IMMEDIATE')
            row = d.execute('SELECT c.challenge, s.* FROM photo_native_codes c JOIN photo_sessions s ON s.hash=c.session_hash WHERE c.hash=? AND c.expires>? AND s.expires>?', (digest(payload.code), time.time(), time.time())).fetchone()
            if not row or not hmac.compare_digest(row['challenge'], challenge(payload.verifier)):
                raise HTTPException(400, 'Sign-in expired or invalid; please try again')
            d.execute('DELETE FROM photo_native_codes WHERE hash=?', (digest(payload.code),))
            token = secrets.token_urlsafe(32)
            family = d.execute('SELECT family FROM photo_session_families WHERE hash=?', (row['hash'],)).fetchone()
            family = family['family'] if family else row['hash']
            d.execute('INSERT OR IGNORE INTO photo_session_families VALUES(?,?)', (row['hash'], family))
            d.execute('INSERT INTO photo_sessions VALUES(?,?,?,?,?)', (digest(token), row['user_id'], row['user_json'], secrets.token_urlsafe(32), time.time() + 30 * 86400))
            d.execute('INSERT INTO photo_session_families VALUES(?,?)', (digest(token), family))
            return token, row['user_id']
