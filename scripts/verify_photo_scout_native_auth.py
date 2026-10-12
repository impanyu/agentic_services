"""Verify deployed native OAuth routing without passwords or a Google sign-in."""
from urllib.parse import urlsplit,parse_qs
import httpx

def verify(origin='https://photo-scout.aisoup.net'):
    with httpx.Client(base_url=origin,timeout=25,follow_redirects=False) as client:
        me=client.get('/api/v1/auth/me');me.raise_for_status()
        assert me.json().get('nativeAuthReady') is True,'Native auth capability missing'
        start=client.get('/api/v1/auth/login',params={'native_challenge':'a'*43})
        assert start.status_code==302,'Native login did not start'
        query=parse_qs(urlsplit(start.headers['location']).query)
        assert query['redirect_uri']==[origin+'/api/v1/auth/callback'],'Unexpected OAuth callback'
        finish=client.get('/api/v1/auth/callback',params={'state':query['state'][0],'error':'access_denied'})
        assert finish.status_code==302 and finish.headers['location']=='net.aisoup.photoscout://auth?error=cancelled','Native callback fell back to a browser page'
        exchange=client.post('/api/v1/auth/native/exchange',json={'code':'a'*43,'verifier':'v'*43},headers={'Origin':origin})
        assert exchange.status_code==400,'Native exchange route missing or accepts invalid proof'
    print('Native auth deployment verified: capability, bound callback, and invalid proof rejection')

if __name__=='__main__':verify()
