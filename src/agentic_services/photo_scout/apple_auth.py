"""Sign in with Apple: cookie-bound, one-use authorization and verified identity."""
import hmac
import json
import os
import re
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode

import httpx
import jwt
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from .native_auth import digest
from .web_origin import oauth_callback, web_home

APPLE_STATE_COOKIE = 'photo_scout_apple_state'


def configuration():
    key = os.getenv('PHOTO_SCOUT_APPLE_PRIVATE_KEY', '').replace('\\n', '\n')
    if not key and os.getenv('PHOTO_SCOUT_APPLE_PRIVATE_KEY_FILE'):
        try:
            key = Path(os.environ['PHOTO_SCOUT_APPLE_PRIVATE_KEY_FILE']).read_text()
        except OSError:
            pass
    return (os.getenv('PHOTO_SCOUT_APPLE_CLIENT_ID', ''),
            os.getenv('PHOTO_SCOUT_APPLE_TEAM_ID', ''),
            os.getenv('PHOTO_SCOUT_APPLE_KEY_ID', ''), key)


def configured():
    return all(configuration())


def callback_url(request):
    return oauth_callback(request).replace('/auth/callback', '/auth/apple/callback')


def client_secret():
    client, team, key_id, key = configuration()
    now = int(time.time())
    return jwt.encode({'iss': team, 'sub': client, 'aud': 'https://appleid.apple.com',
                       'iat': now, 'exp': now + 300}, key, algorithm='ES256', headers={'kid': key_id})


def install_apple_auth(router, db, require_api, native_auth, complete_login):
    with db() as d:
        d.execute('CREATE TABLE IF NOT EXISTS photo_apple_oauth (hash TEXT PRIMARY KEY, nonce TEXT NOT NULL, expires REAL NOT NULL)')
        d.execute('CREATE TABLE IF NOT EXISTS photo_apple_profiles (subject TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL)')

    def state_cookie(response, value, age):
        # Apple returns a cross-site top-level form POST. Lax would omit this cookie.
        response.set_cookie(APPLE_STATE_COOKIE, value, max_age=age, httponly=True,
                            secure=True, samesite='none', path='/photo-scout/v1/auth/')
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @router.get('/photo-scout/v1/auth/apple/login')
    def login(request: Request, native_challenge: str = ''):
        require_api(request.headers.get('authorization'))
        if native_challenge and not re.fullmatch(r'[A-Za-z0-9_-]{43}', native_challenge):
            raise HTTPException(400, 'Invalid native sign-in challenge')
        if not configured():
            raise HTTPException(503, 'Sign in with Apple is not available yet')
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with db() as d:
            d.execute('DELETE FROM photo_apple_oauth WHERE expires<?', (time.time(),))
            d.execute('INSERT INTO photo_apple_oauth VALUES(?,?,?)', (digest(state), nonce, time.time() + 600))
        if native_challenge:
            native_auth.begin(state, native_challenge)
        url = 'https://appleid.apple.com/auth/authorize?' + urlencode({
            'client_id': configuration()[0], 'redirect_uri': callback_url(request),
            'response_type': 'code', 'response_mode': 'form_post', 'scope': 'name email',
            'state': state, 'nonce': nonce})
        return state_cookie(RedirectResponse(url, 302), state, 600)

    @router.post('/photo-scout/v1/auth/apple/callback')
    async def finish(request: Request):
        require_api(request.headers.get('authorization'))
        if request.headers.get('content-type', '').split(';')[0].lower() != 'application/x-www-form-urlencoded':
            raise HTTPException(400, 'Invalid Apple sign-in response')
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 16384:
                raise HTTPException(413, 'Apple sign-in response is too large')
        try:
            fields = parse_qs(body.decode(), keep_blank_values=True, max_num_fields=10)
        except (ValueError, UnicodeError) as e:
            raise HTTPException(400, 'Invalid Apple sign-in response') from e
        if any(len(values) != 1 for values in fields.values()):
            raise HTTPException(400, 'Invalid Apple sign-in response')
        form = {key: value[0] for key, value in fields.items()}
        state = form.get('state', '')
        if not state or len(state) > 200 or not hmac.compare_digest(state, request.cookies.get(APPLE_STATE_COOKIE, '')):
            raise HTTPException(400, 'Invalid sign-in state')
        with db() as d:
            d.execute('BEGIN IMMEDIATE')
            pending = d.execute('SELECT * FROM photo_apple_oauth WHERE hash=? AND expires>?', (digest(state), time.time())).fetchone()
            d.execute('DELETE FROM photo_apple_oauth WHERE hash=?', (digest(state),))
        if not pending:
            raise HTTPException(400, 'Sign-in expired; please try again')
        if form.get('error'):
            return state_cookie(RedirectResponse(native_auth.finish(state) or web_home(request) + '?login=cancelled', 303), '', 0)
        code = form.get('code', '')
        if not code or len(code) > 4000:
            raise HTTPException(400, 'Missing sign-in code')
        client = configuration()[0]
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as c:
                result = await c.post('https://appleid.apple.com/auth/token', data={
                    'client_id': client, 'client_secret': client_secret(), 'code': code,
                    'grant_type': 'authorization_code', 'redirect_uri': callback_url(request)})
                result.raise_for_status()
                token = result.json()['id_token']
                header = jwt.get_unverified_header(token)
                keys = await c.get('https://appleid.apple.com/auth/keys')
                keys.raise_for_status()
                key = next(k for k in keys.json()['keys'] if k['kid'] == header['kid'])
                claims = jwt.decode(token, jwt.PyJWK.from_dict(key).key, algorithms=['RS256'],
                                    audience=client, issuer='https://appleid.apple.com',
                                    options={'require': ['exp', 'iat', 'sub', 'aud', 'iss', 'nonce']}, leeway=30)
                if not isinstance(claims['nonce'], str) or not hmac.compare_digest(claims['nonce'], pending['nonce']):
                    raise ValueError('Invalid nonce')
                if not isinstance(claims['sub'], str) or not claims['sub'] or len(claims['sub']) > 255:
                    raise ValueError('Invalid subject')
                if claims.get('email') and claims.get('email_verified') not in (True, 'true'):
                    raise ValueError('Unverified email')
        except Exception as e:
            raise HTTPException(400, 'Apple sign-in could not be verified; please try again') from e
        # Apple supplies the name only on the first authorization. Never link by email,
        # including Apple's relay email; the signed subject is the account identifier.
        try:
            supplied = json.loads(form.get('user') or '{}')
            name = supplied.get('name') or {}
            name = ' '.join(str(name.get(k) or '') for k in ('firstName', 'lastName')).strip()[:200]
        except (ValueError, AttributeError):
            name = ''
        email = str(claims.get('email') or '')[:320]
        with db() as d:
            saved = d.execute('SELECT name,email FROM photo_apple_profiles WHERE subject=?', (claims['sub'],)).fetchone()
            name = name or (saved['name'] if saved else '') or 'Apple user'
            email = email or (saved['email'] if saved else '')
            d.execute('INSERT OR REPLACE INTO photo_apple_profiles VALUES(?,?,?)', (claims['sub'], name, email))
        user = {'id': 'apple:' + claims['sub'], 'name': name, 'email': email}
        response = complete_login(request, user, state)
        # A POST callback must become a GET when returning to the website.
        response.status_code = 303
        return state_cookie(response, '', 0)
