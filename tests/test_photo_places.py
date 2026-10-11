import asyncio
import httpx
import pytest
from agentic_services.photo_scout import places,intent

def test_free_text_query_and_search_region_reach_google(monkeypatch):
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_API_KEY','fixture')
    real=httpx.AsyncClient
    def handler(request):
        import json
        payload=json.loads(request.content)
        assert payload['textQuery']=='independent bookstores'
        assert 'includedType' not in payload
        assert payload['locationRestriction']['rectangle']['low']['latitude']<40
        assert request.headers['X-Goog-FieldMask']==places.FIELDS
        return httpx.Response(200,json={'places':[
            {'id':'book','displayName':{'text':'Books'},'location':{'latitude':40,'longitude':-96},'primaryType':'book_store'},
            {'id':'far','displayName':{'text':'Too far'},'location':{'latitude':41,'longitude':-96}}]})
    monkeypatch.setattr(places.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler)))
    rows,status=asyncio.run(places.nearby_places(40,-96,1000,['independent bookstores']))
    assert [r['id'] for r in rows]==['google:book']
    assert status['provider']=='google-places'

def test_places_failure_never_falls_back_to_unrelated_scenic_pois(monkeypatch):
    async def fail(*args,**kwargs):raise ValueError('API unavailable')
    monkeypatch.setattr(places,'search_text',fail)
    rows,status=asyncio.run(places.nearby_places(40,-96,1000,['motels']))
    assert rows==[] and status['status']=='unavailable'

def test_single_query_paginates_and_returns_30_candidates(monkeypatch):
    import json
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_API_KEY','fixture')
    real=httpx.AsyncClient;calls=[]
    def handler(request):
        body=json.loads(request.content);calls.append(body)
        assert body['pageSize']==20
        start=20 if body.get('pageToken')=='second' else 0
        return httpx.Response(200,json={'places':[
            {'id':str(i),'displayName':{'text':f'Cafe {i}'},'location':{'latitude':40+i*.00001,'longitude':-96}}
            for i in range(start,start+20)],'nextPageToken':'second' if start==0 else 'third'})
    monkeypatch.setattr(places.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler)))
    rows,status=asyncio.run(places.nearby_places(40,-96,1000,['coffee shops'],limit=30))
    assert len(rows)==30 and len(calls)==2 and rows[-1]['id']=='google:29'
    assert {k:v for k,v in calls[1].items() if k!='pageToken'}==calls[0]
    assert status['count']==30

def test_explicit_address_is_geocoded_separately_from_discovery_query(monkeypatch):
    monkeypatch.setenv('PHOTO_SCOUT_POI_PROVIDER','google-places')
    async def parse(settings,payload):
        return intent.PhotoIntent(locationQuery='1600 Pennsylvania Avenue NW, Washington DC',useMapCenter=False,
            poiQueries=[],photoStyles=[],radiusMeters=2000,limit=3,preferences='',explanation='Address',clarification=None)
    async def search(query,**kwargs):
        assert query=='1600 Pennsylvania Avenue NW, Washington DC'
        assert kwargs=={'limit':1}
        return [{'displayName':{'text':'Address'},'location':{'latitude':38.8977,'longitude':-77.0365}}]
    monkeypatch.setattr(intent,'parse_intent',parse);monkeypatch.setattr(places,'search_text',search)
    result=asyncio.run(intent.resolve_intent(None,intent.IntentRequest(query='1600 Pennsylvania Avenue NW, Washington DC',lat=40,lon=-96)))
    assert result['poiQueries']==[] and result['locations'][0]['lat']==38.8977

def test_poi_queries_accept_arbitrary_categories_and_names():
    plan=dict(locationQuery=None,useMapCenter=True,photoStyles=[],radiusMeters=1000,limit=3,preferences='',explanation='',clarification=None)
    for query in ['motels','vegan bakeries','industrial turbine museums','Starbucks']:
        assert intent.PhotoIntent(**plan,poiQueries=[query]).poiQueries==[query]

def test_mood_discovery_mapping_preserves_explicit_target():
    from agentic_services.photo_scout.styles import discovery_queries,PHOTO_STYLES,MOOD_PLACE_QUERIES
    assert set(MOOD_PLACE_QUERIES)==set(PHOTO_STYLES)
    assert discovery_queries(['coffee shops'],['vintage'])==['coffee shops']
    assert discovery_queries([],['nature'])==['parks','botanical gardens']
    assert discovery_queries([],['artistic','waterside'])==['public art','waterfront promenades','street murals','lakeside parks']
    assert discovery_queries([],[],['museum'])==['museum']
    assert discovery_queries([],[])==['scenic places and tourist attractions']


def test_multi_query_stops_after_shared_target_with_fair_category_coverage(monkeypatch):
    import json
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_API_KEY','fixture')
    real=httpx.AsyncClient;calls=[]
    def handler(request):
        body=json.loads(request.content);calls.append(body)
        prefix=body['textQuery'];start=20 if body.get('pageToken') else 0
        return httpx.Response(200,json={'places':[
            {'id':prefix+str(i),'displayName':{'text':prefix+str(i)},
             'location':{'latitude':40+i*.00001,'longitude':-96}}
            for i in range(start,start+20)],'nextPageToken':'second' if start==0 else 'third'})
    monkeypatch.setattr(places.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler)))
    rows,status=asyncio.run(places.nearby_places(40,-96,1000,['parks','gardens'],limit=30))
    assert len(rows)==30 and len(calls)==2
    assert sum(r['id'].startswith('google:parks') for r in rows)==15
    assert sum(r['id'].startswith('google:gardens') for r in rows)==15
    assert status['queryPages']=={'parks':1,'gardens':1}
    rows,status=asyncio.run(places.nearby_places(40,-96,1000,['parks','gardens'],limit=50))
    assert len(rows)==50 and status['pagesFetched']==4
    assert sum(r['id'].startswith('google:parks') for r in rows)==25


def test_pagination_continues_after_duplicate_and_outside_radius_pages(monkeypatch):
    import json
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_API_KEY','fixture')
    real=httpx.AsyncClient;calls=[]
    def handler(request):
        body=json.loads(request.content);calls.append(body)
        page=body.get('pageToken','first');prefix=body['textQuery']
        if page=='first':
            rows=[{'id':'shared'+str(i),'displayName':{'text':'Shared'},
                   'location':{'latitude':40 if i<5 else 42,'longitude':-96}} for i in range(20)]
        else:
            start=20 if page=='second' else 40
            rows=[{'id':prefix+str(i),'displayName':{'text':prefix},
                   'location':{'latitude':40,'longitude':-96}} for i in range(start,start+20)]
        return httpx.Response(200,json={'places':rows,**({'nextPageToken':'second' if page=='first' else 'third'} if page!='third' else {})})
    monkeypatch.setattr(places.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler)))
    rows,status=asyncio.run(places.nearby_places(40,-96,1000,['parks','gardens'],limit=50))
    assert len(rows)==50 and len({r['id'] for r in rows})==50
    assert status['pagesFetched']==6
    assert all(r['lat']==40 for r in rows)


def test_optimized_pages_preserve_legacy_round_robin_selected_candidates(monkeypatch):
    import json
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_API_KEY','fixture')
    real=httpx.AsyncClient;calls=[]
    def row(prefix,i):
        return {'id':prefix+str(i),'displayName':{'text':prefix+str(i)},
                'location':{'latitude':40+i*.00001,'longitude':-96}}
    def handler(request):
        body=json.loads(request.content);calls.append(body)
        start={'first':0,'second':20,'third':40}[body.get('pageToken','first')]
        return httpx.Response(200,json={'places':[row(body['textQuery'],i) for i in range(start,start+20)],
            **({'nextPageToken':'second' if start==0 else 'third'} if start<40 else {})})
    monkeypatch.setattr(places.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler)))
    rows,status=asyncio.run(places.nearby_places(40,-96,1000,['parks','gardens'],limit=50))
    # Legacy fetched 3 pages for EACH term then interleaved/truncated the same 50.
    expected=['google:'+prefix+str(i) for i in range(25) for prefix in ['parks','gardens']]
    assert [r['id'] for r in rows]==expected
    assert len(calls)==4 # Two unused third pages eliminated, same selected set/order.
