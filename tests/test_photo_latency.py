import asyncio
import base64
import json
import sqlite3
from types import SimpleNamespace

from agentic_services.photo_scout import costs, portraits
from agentic_services.photo_scout.score_cache import ScoreCache


def test_bulk_assessments_skip_expired_and_corrupt_records(tmp_path):
    cache=ScoreCache(tmp_path/'scores.db')
    cache.put([('valid',{'score':80}),('expired',{'score':20}),('corrupt',{'score':40})])
    with sqlite3.connect(cache.path) as db:
        db.execute("UPDATE photo_scout_score_cache SET expires=0 WHERE cache_key='expired'")
        db.execute("UPDATE photo_scout_score_cache SET assessment='broken' WHERE cache_key='corrupt'")
    assert cache.get_many(['valid','valid','expired','corrupt','absent'])=={'valid':{'score':80}}
    assert cache.get_many([])=={}


def test_background_views_download_together_without_reordering(monkeypatch):
    calls=[]
    async def run():
        ready=asyncio.Event()
        async def image(ref):
            calls.append(ref)
            if len(calls)==3:ready.set()
            await ready.wait()
            return 'data:image/png;base64,'+base64.b64encode(ref.encode()).decode()
        monkeypatch.setattr(portraits,'image_data',image)
        class Responses:
            async def parse(self,**kwargs):
                images=[c for c in kwargs['input'][0]['content'] if c['type']=='input_image']
                assert len(images)==3
                return SimpleNamespace(output_parsed=portraits.BackgroundChoice(index=1,distortion='minimal',reason='natural'),usage=None)
        return await asyncio.wait_for(portraits.prepare_background(SimpleNamespace(responses=Responses()),'google-streetview://test/45/0/120','vision'),1)
    raw,ref,preparation=asyncio.run(run())
    assert ref=='google-streetview://test/45/0/60'
    assert raw==ref.encode()
    assert preparation['comparedFovDegrees']==[90,60,45]


def test_model_latency_survives_parallel_calls_and_failure(tmp_path):
    @costs.tracked_task(tmp_path/'db','search')
    async def run():
        async def operation(failed=False):
            await asyncio.sleep(0)
            if failed:raise ValueError('test')
            return SimpleNamespace(usage={'input_tokens':1,'output_tokens':1})
        await asyncio.gather(costs.observe('scoring','gpt-6-luna',operation()),
                             costs.observe('scoring','gpt-6-luna',operation(True)),return_exceptions=True)
        latency=costs.summary()['modelLatencySeconds']['scoring']
        assert latency['calls']==2
        assert latency['sum']>=latency['max']>=0
    asyncio.run(run())
