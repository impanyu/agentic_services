import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import HTTPException

from agentic_services.niche_discovery import NicheDraft, NicheStore
from agentic_services.niche_search import collect_search, leads, normalized_url, SearchUnavailable


def test_no_network_without_configuration(tmp_path, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("No external request permitted")
    monkeypatch.setattr(httpx.AsyncClient, 'get', forbidden)
    store = NicheStore(tmp_path / 'db')
    monkeypatch.setenv('NICHE_COLLECT_SEARCH', '0')
    assert asyncio.run(collect_search(store))['status'] == 'disabled'
    monkeypatch.setenv('NICHE_COLLECT_SEARCH', '1')
    monkeypatch.delenv('BRAVE_SEARCH_API_KEY', raising=False)
    assert asyncio.run(collect_search(store))['status'] == 'not_configured'
    monkeypatch.setenv('BRAVE_SEARCH_API_KEY', 'test-secret')
    monkeypatch.delenv('NICHE_SEARCH_STORAGE_REFERENCE', raising=False)
    assert asyncio.run(collect_search(store))['status'] == 'storage_permission_needed'


def test_search_leads_isolated_deduplicated_bounded_and_expired(tmp_path, monkeypatch):
    for k,v in {'NICHE_COLLECT_SEARCH':'1','BRAVE_SEARCH_API_KEY':'test-secret',
                'NICHE_SEARCH_STORAGE_REFERENCE':'test-storage-grant','NICHE_SEARCH_DAILY_REQUEST_LIMIT':'2'}.items():
        monkeypatch.setenv(k,v)
    calls=[]
    async def fake_get(self, url, **kwargs):
        assert url == 'https://api.search.brave.com/res/v1/web/search'
        calls.append(kwargs['params']['q'])
        return httpx.Response(200,json={'web':{'results':[
            {'url':'https://www.reddit.com/r/test/comments/abc/?utm_source=x#comment','description':'private excerpt'},
            {'url':'https://www.reddit.com/r/test/comments/abc/','title':'private title'},
            {'url':'http://127.0.0.1/secret'}, {'url':'javascript:alert(1)'}]}})
    monkeypatch.setattr(httpx.AsyncClient,'get',fake_get)
    store=NicheStore(tmp_path/'db')
    first=asyncio.run(collect_search(store))
    assert first['leadsAdded']==1 and first['requestsMade']==2
    assert asyncio.run(collect_search(store))['requestsMade']==0
    assert len(calls)==2 and store.signals()==[]
    lead=leads(store)[0]
    assert lead['state']=='discovery_only' and 'description' not in lead and 'title' not in lead
    draft=NicheDraft(title='Test unpublished niche opportunity',problem='A recurring workflow issue requires independent evidence.',
        buyer='Operators',category='tools',solution_hypothesis='An automation tool for recurring manual tasks.',
        pain=2,frequency=2,willingness_to_pay=2,reachable_buyers=2,feasibility=2,competition_gap=2,
        evaluation_notes='This discovery lead cannot serve as verified source evidence.',signal_ids=[lead['id']],publish=True)
    with pytest.raises(HTTPException) as error: store.save_niche(draft)
    assert error.value.status_code==422
    with store.connect() as db:
        db.execute('UPDATE niche_search_leads SET last_seen=?',((datetime.now(UTC)-timedelta(days=31)).isoformat(),))
    asyncio.run(collect_search(store))
    assert leads(store)==[]
    with store.connect() as db: assert db.execute('SELECT COUNT(*) FROM niche_search_matches').fetchone()[0]==0


def test_url_validation():
    for url in ['http://localhost/a','http://192.168.0.1/a','https://user:pass@example.org/a','https://example.org:9000/a','https://abc.local/a']:
        assert normalized_url(url) is None
    assert normalized_url('https://Example.org/a?utm_source=x&b=2&a=1#fragment')=='https://example.org/a?a=1&b=2'


def test_rate_limit_stops_without_retry(tmp_path,monkeypatch):
    for k,v in {'NICHE_COLLECT_SEARCH':'1','BRAVE_SEARCH_API_KEY':'test-secret','NICHE_SEARCH_STORAGE_REFERENCE':'test-grant'}.items(): monkeypatch.setenv(k,v)
    calls=[]
    async def limited(*args,**kwargs):
        calls.append(1)
        return httpx.Response(429)
    monkeypatch.setattr(httpx.AsyncClient,'get',limited)
    with pytest.raises(SearchUnavailable,match='429'): asyncio.run(collect_search(NicheStore(tmp_path/'db')))
    assert len(calls)==1
