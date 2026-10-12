"""Browser-to-iOS sign-in transfer: proof binding, expiry and revocation."""
import sqlite3
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from agentic_services.photo_scout.native_auth import NativeAuth, challenge, digest
from test_photo_accounts import fixture, seed


def handoff(path, proof, token='session'):
    def db():
        d = sqlite3.connect(path, timeout=15)
        d.row_factory = sqlite3.Row
        return d
    native = NativeAuth(db)
    native.begin('state', proof)
    url = native.finish('state', digest(token))
    assert url.startswith('net.aisoup.photoscout://auth?code=')
    return parse_qs(urlsplit(url).query)['code'][0]


def exchange(client, code, verifier, origin='https://photo-scout.aisoup.net'):
    return client.post('/photo-scout/v1/auth/native/exchange', json={'code': code, 'verifier': verifier}, headers={'Origin': origin})


def test_native_login_challenge_and_cancel(tmp_path, monkeypatch):
    client, path = fixture(tmp_path, monkeypatch)
    assert client.get('/photo-scout/v1/auth/login?native_challenge=bad').status_code == 400
    response = client.get('/photo-scout/v1/auth/login', params={'native_challenge': challenge('v' * 43)}, follow_redirects=False)
    state = parse_qs(urlsplit(response.headers['location']).query)['state'][0]
    response = client.get('/photo-scout/v1/auth/callback', params={'state': state, 'error': 'denied'}, follow_redirects=False)
    assert response.headers['location'] == 'net.aisoup.photoscout://auth?error=cancelled'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM photo_native_oauth').fetchone()[0] == 0


def test_native_exchange_proof_origin_one_use_and_linked_logout(tmp_path, monkeypatch):
    client, path = fixture(tmp_path, monkeypatch)
    seed(client, path, 'alice')
    verifier = 'v' * 43
    code = handoff(path, challenge(verifier))
    client.cookies.clear()
    assert exchange(client, code, verifier, 'https://evil.test').status_code == 403
    assert exchange(client, code, 'x' * 43).status_code == 400
    response = exchange(client, code, verifier)
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert all(part in response.headers['set-cookie'] for part in ('HttpOnly', 'Secure', 'SameSite=lax'))
    me = client.get('/photo-scout/v1/auth/me').json()
    assert me['user']['id'] == 'alice'
    assert exchange(client, code, verifier).status_code == 400
    assert client.post('/photo-scout/v1/auth/logout', headers={'Origin': 'https://photo-scout.aisoup.net', 'X-CSRF-Token': me['csrfToken']}).status_code == 200
    client.cookies.clear()
    client.cookies.set('photo_scout_session', 'session')
    assert client.get('/photo-scout/v1/auth/me').json()['user'] is None


@pytest.mark.parametrize('invalid', ['expired_code', 'expired_session', 'revoked_session'])
def test_native_exchange_rejects_stale_or_revoked_session(tmp_path, monkeypatch, invalid):
    client, path = fixture(tmp_path, monkeypatch)
    seed(client, path, 'alice')
    verifier = 'v' * 43
    code = handoff(path, challenge(verifier))
    with sqlite3.connect(path) as db:
        if invalid == 'expired_code':
            db.execute('UPDATE photo_native_codes SET expires=?', (time.time() - 1,))
        elif invalid == 'expired_session':
            db.execute('UPDATE photo_sessions SET expires=?', (time.time() - 1,))
        else:
            db.execute('DELETE FROM photo_sessions')
    assert exchange(client, code, verifier).status_code == 400
