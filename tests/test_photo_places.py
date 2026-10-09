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
