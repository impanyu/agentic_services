"""Exact first-party website aliases during the subdomain migration."""
import os

CANONICAL_ORIGIN = 'https://photo-scout.aisoup.net'


def allowed_web_origin(value):
    return value in {CANONICAL_ORIGIN, 'https://aisoup.net', 'https://www.aisoup.net',
                     os.getenv('PHOTO_SCOUT_WEB_ORIGIN', 'https://aisoup.net').rstrip('/')}


def canonical_request(request):
    # Caddy replaces this forwarding header on requests from public clients.
    return request.headers.get('x-forwarded-host', '').lower() == 'photo-scout.aisoup.net'


def web_home(request):
    if canonical_request(request):
        return CANONICAL_ORIGIN + '/'
    return os.getenv('PHOTO_SCOUT_WEB_ORIGIN', 'https://aisoup.net').rstrip('/') + '/photo-scout/'


def oauth_callback(request):
    if canonical_request(request):
        return CANONICAL_ORIGIN + '/api/v1/auth/callback'
    return os.getenv('PHOTO_SCOUT_OAUTH_CALLBACK', 'https://api.aisoup.net/photo-scout/v1/auth/callback')
