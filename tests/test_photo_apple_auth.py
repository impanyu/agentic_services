import base64
import hashlib
import json
import sqlite3
import time
from urllib.parse import parse_qs, urlsplit
import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from agentic_services.photo_scout.apple_auth import APPLE_STATE_COOKIE
from agentic_services.photo_scout.native_auth import challenge
from test_photo_accounts import fixture


def setup(tmp_path, monkeypatch):
    client, path = fixture(tmp_path, monkeypatch)
    client.headers['X-Forwarded-Host'] = 'photo-scout.aisoup.net'
    key = ec.generate_private_key(ec.SECP256R1())
    monkeypatch.setenv('PHOTO_SCOUT_APPLE_CLIENT_ID', 'net.aisoup.photoscout.web')
    monkeypatch.setenv('PHOTO_SCOUT_APPLE_TEAM_ID', 'TESTTEAM01')
    monkeypatch.setenv('PHOTO_SCOUT_APPLE_KEY_ID', 'TESTKEY001')
    monkeypatch.setenv('PHOTO_SCOUT_APPLE_PRIVATE_KEY', key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode())
    signing = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = signing.public_key().public_numbers()
    encode = lambda n: base64.urlsafe_b64encode(n.to_bytes((n.bit_length()+7)//8, 'big')).decode().rstrip('=')
    jwk = {'kty': 'RSA', 'kid': 'apple-fixture', 'alg': 'RS256', 'n': encode(numbers.n), 'e': encode(numbers.e)}
    values = {}
    def handler(request):
        if request.url.path.endswith('/token'):
            form = parse_qs(request.content.decode())
            secret = jwt.decode(form['client_secret'][0], key.public_key(), algorithms=['ES256'], audience='https://appleid.apple.com', issuer='TESTTEAM01')
            assert secret['sub'] == 'net.aisoup.photoscout.web'
            assert form['redirect_uri'] == ['https://photo-scout.aisoup.net/api/v1/auth/apple/callback']
            claims = {'iss': 'https://appleid.apple.com', 'sub': 'verified-apple-subject', 'aud': 'net.aisoup.photoscout.web', 'iat': int(time.time()), 'exp': int(time.time())+600, 'nonce': values['nonce'], 'email': 'relay@privaterelay.appleid.com', 'email_verified': 'true', **values.get('claims', {})}
            sign = values.get('sign', signing)
            return httpx.Response(200, json={'id_token': jwt.encode(claims, sign, algorithm='RS256', headers={'kid': 'apple-fixture'})})
        assert request.url.path == '/auth/keys'
        return httpx.Response(200, json={'keys': [jwk]})
    import agentic_services.photo_scout.apple_auth as apple
    real = httpx.AsyncClient
    monkeypatch.setattr(apple.httpx, 'AsyncClient', lambda **kw: real(transport=httpx.MockTransport(handler)))
    def start(native=False):
        params = {'native_challenge': challenge('v'*43)} if native else {}
        response = client.get('/photo-scout/v1/auth/apple/login', params=params, follow_redirects=False)
        assert response.status_code == 302
        query = parse_qs(urlsplit(response.headers['location']).query)
        assert query['scope'] == ['name email'] and query['response_mode'] == ['form_post']
        assert all(s in response.headers['set-cookie'] for s in ['HttpOnly', 'Secure', 'SameSite=none'])
        state = query['state'][0]
        with sqlite3.connect(path) as d:
            values['nonce'] = d.execute('SELECT nonce FROM photo_apple_oauth WHERE hash=?', (hashlib.sha256(state.encode()).hexdigest(),)).fetchone()[0]
        return state
    return client, path, values, start


def callback(client, state, **fields):
    return client.post('/photo-scout/v1/auth/apple/callback', data={'state': state, 'code': 'fixture-code', **fields}, follow_redirects=False)


def test_unconfigured_apple_is_disabled(tmp_path, monkeypatch):
    for key in ['CLIENT_ID', 'TEAM_ID', 'KEY_ID', 'PRIVATE_KEY', 'PRIVATE_KEY_FILE']:
        monkeypatch.delenv('PHOTO_SCOUT_APPLE_'+key, raising=False)
    client, _ = fixture(tmp_path, monkeypatch)
    assert client.get('/photo-scout/v1/auth/me').json()['providers'] == {'google': True, 'apple': False}
    assert client.get('/photo-scout/v1/auth/apple/login').status_code == 503


@pytest.mark.parametrize('native', [False, True])
def test_verified_apple_login_name_persistence_and_one_use_native_handoff(tmp_path, monkeypatch, native):
    client, path, values, start = setup(tmp_path, monkeypatch)
    state = start(native)
    assert callback(client, 'wrong').status_code == 400
    response = callback(client, state, user=json.dumps({'name': {'firstName': 'Test', 'lastName': 'Person'}, 'email': 'untrusted@example.test'}))
    assert response.status_code == 303
    if native:
        code = parse_qs(urlsplit(response.headers['location']).query)['code'][0]
        client.cookies.clear()
        body = {'code': code, 'verifier': 'v'*43}
        headers = {'Origin': 'https://photo-scout.aisoup.net'}
        assert client.post('/photo-scout/v1/auth/native/exchange', json={**body, 'verifier': 'x'*43}, headers=headers).status_code == 400
        assert client.post('/photo-scout/v1/auth/native/exchange', json=body, headers=headers).status_code == 200
        assert client.post('/photo-scout/v1/auth/native/exchange', json=body, headers=headers).status_code == 400
    else:
        assert response.headers['location'] == 'https://photo-scout.aisoup.net/'
    user = client.get('/photo-scout/v1/auth/me').json()['user']
    assert user == {'id': 'apple:verified-apple-subject', 'name': 'Test Person', 'email': 'relay@privaterelay.appleid.com'}
    client.cookies.set(APPLE_STATE_COOKIE, state, domain='api.test', path='/photo-scout/v1/auth/')
    assert callback(client, state).status_code == 400
    state = start()
    assert callback(client, state).status_code == 303
    assert client.get('/photo-scout/v1/auth/me').json()['user']['name'] == 'Test Person'


@pytest.mark.parametrize('invalid', ['nonce', 'audience', 'issuer', 'expiry', 'email', 'signature'])
def test_apple_rejects_untrusted_identities(tmp_path, monkeypatch, invalid):
    client, path, values, start = setup(tmp_path, monkeypatch)
    state = start()
    values['claims'] = {'nonce': 'wrong'} if invalid == 'nonce' else {'aud': 'wrong'} if invalid == 'audience' else {'iss': 'https://evil.test'} if invalid == 'issuer' else {'exp': int(time.time())-120} if invalid == 'expiry' else {'email_verified': 'false'} if invalid == 'email' else {}
    if invalid == 'signature':
        values['sign'] = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert callback(client, state).status_code == 400
    with sqlite3.connect(path) as d:
        assert d.execute('SELECT count(*) FROM photo_sessions').fetchone()[0] == 0


def test_apple_cancellation_and_expired_state(tmp_path, monkeypatch):
    client, path, _, start = setup(tmp_path, monkeypatch)
    state = start(True)
    response = callback(client, state, error='user_cancelled_authorize')
    assert response.status_code == 303 and response.headers['location'] == 'net.aisoup.photoscout://auth?error=cancelled'
    state = start()
    with sqlite3.connect(path) as d:
        d.execute('UPDATE photo_apple_oauth SET expires=0')
    assert callback(client, state).status_code == 400
