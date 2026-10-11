import asyncio
import sqlite3
from types import SimpleNamespace
import pytest
from agentic_services.photo_scout import costs


def events(path):
    with sqlite3.connect(path) as db:
        return db.execute('SELECT run,stage,status,quantity,estimated_usd,usage FROM photo_scout_cost_events').fetchall()


def test_actual_tokens_cached_input_and_reasoning_output(tmp_path):
    path=tmp_path/'db'
    @costs.tracked_task(path,'search')
    async def run():
        costs.record('intent','gpt-6.1-sol',{'input_tokens':1000,'input_tokens_details':{'cached_tokens':400},'output_tokens':300})
        result=costs.summary()
        assert result['unknownCostEvents']==0
        assert result['estimatedKnownUsd']==pytest.approx(.00424)
    asyncio.run(run())
    assert len(events(path))==1


def test_identical_requests_shared_but_next_search_is_independent(tmp_path):
    calls=[]
    async def request():
        calls.append(1);await asyncio.sleep(.001);return len(calls)
    @costs.tracked_task(tmp_path/'db','search')
    async def run():
        return await asyncio.gather(*(costs.reuse_request(('places','same',0),request) for _ in range(5)))
    assert asyncio.run(run())==[1]*5
    assert asyncio.run(run())==[2]*5
    assert costs.current.get() is None


def test_parallel_pages_share_one_task_budget(tmp_path,monkeypatch):
    monkeypatch.setenv('PHOTO_SCOUT_PLACES_REQUESTS_PER_TASK','2')
    @costs.tracked_task(tmp_path/'db','search')
    async def run():
        async def page():
            costs.reserve_places_request();costs.record('places-text',status='attempted')
        outcomes=await asyncio.gather(*(page() for _ in range(6)),return_exceptions=True)
        assert sum(isinstance(x,ValueError) for x in outcomes)==4
        assert costs.summary()['placesBudgetReached']
    asyncio.run(run())
    assert len(events(tmp_path/'db'))==2


def test_nested_task_keeps_correlation_and_failed_usage_is_unknown(tmp_path):
    path=tmp_path/'db'
    @costs.tracked_task(path,'search')
    async def nested():
        async def fail():raise RuntimeError('failure')
        with pytest.raises(RuntimeError):await costs.observe('scoring','gpt-6-luna',fail())
    @costs.tracked_task(path,'search')
    async def parent():
        costs.record('places-text');await nested()
        assert costs.summary()['unknownCostEvents']==1
    asyncio.run(parent())
    rows=events(path)
    assert len({r[0] for r in rows})==1
    assert rows[1][4] is None


def test_image_edit_counts_both_inputs_and_output(tmp_path):
    @costs.tracked_task(tmp_path/'db','selfie')
    async def run():
        costs.record('image-generation','gpt-image-2.5-sunburst',{'input_tokens':2000,'input_tokens_details':{'image_tokens':1500,'text_tokens':500},'output_tokens':7024})
        assert costs.summary()['estimatedKnownUsd']==pytest.approx(.22522)
    asyncio.run(run())


def test_places_photos_discovery_and_scoring_do_not_repeat_paid_calls(tmp_path,monkeypatch):
    from agentic_services.photo_scout import place_photos
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_API_KEY','test')
    calls=[]
    async def get_json(client,url,params=None,headers=None):
        calls.append(url)
        if url.endswith('/abc'):
            return {'id':'abc','displayName':{'text':'Public place'},'photos':[
                {'name':'places/abc/photos/p'+str(i),'widthPx':400+i,'heightPx':300,
                 'authorAttributions':[{'displayName':'Photographer '+str(i)}]} for i in range(2)]}
        return {'photoUri':'https://lh3.googleusercontent.com/image'}
    monkeypatch.setattr(place_photos,'get_json',get_json)
    @costs.tracked_task(tmp_path/'db','search')
    async def run():
        discovered=await place_photos.place_photos(None,'abc',limit=2)
        await asyncio.gather(*(place_photos.place_photos(None,'abc',limit=1,
            selector=p['photoReference'].rsplit('/',1)[1]) for p in discovered['photos']))
        assert costs.summary()['estimatedKnownUsd']==pytest.approx(.031)
    asyncio.run(run());assert len(calls)==3
    asyncio.run(run());assert len(calls)==6


def test_route_anchors_share_global_places_photo_cap(tmp_path):
    @costs.tracked_task(tmp_path/'db','search')
    async def run():
        async def anchor():
            try:
                costs.reserve_photo_request('places-details-pro')
                costs.record('places-details-pro')
            except ValueError:return False
            return True
        outcomes=await asyncio.gather(*(anchor() for _ in range(24)))
        assert sum(outcomes)==8
        assert costs.summary()['placesPhotoBudgetReached']
    asyncio.run(run())


def test_cache_writes_use_their_own_price_without_double_counting(tmp_path):
    @costs.tracked_task(tmp_path/'db','search')
    async def run():
        costs.record('intent','gpt-6.1-sol',{'input_tokens':1000,
            'input_tokens_details':{'cached_tokens':400,'cache_write_tokens':300},'output_tokens':300})
        assert costs.summary()['estimatedKnownUsd']==pytest.approx(.00439)
    asyncio.run(run())
