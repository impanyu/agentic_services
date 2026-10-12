import asyncio
import json
import sqlite3
import time
from types import SimpleNamespace

import pytest
from agentic_services.photo_scout import costs, experience, intent
from agentic_services.photo_scout.program import SearchProgram
from agentic_services.photo_scout.routes import ExploreRequest


@pytest.fixture(autouse=True)
def mode(monkeypatch):
    monkeypatch.setenv('PHOTO_SCOUT_EXPERIENCE_MODE', 'advise')


def settings(tmp_path):
    return SimpleNamespace(database_path=tmp_path/'db', openai_model='gpt-6.1-sol', openai_api_key='fixture')


def payload():
    return ExploreRequest(query='private request never stored', lat=40.8136634, lon=-96.7025764, radius=20000,
        searchProgram=SearchProgram.model_validate({'steps': [
            {'id': 'private_node_name', 'tool': 'search_places', 'queries': ['private POI name']},
        ], 'output': 'private_node_name'}))


def result(cached=0, failed=0):
    return {'scoring': {'checkedImages': 300, 'cachedImages': cached, 'downloadFailedImages': failed,
                        'scoringFailedImages': 0, 'matchedImages': 4},
            'candidatePoiCount': 50, 'imageUrl': 'https://secret.test?token=secret',
            'executionTrace': [{'id': 'private_node_name', 'tool': 'search_places', 'count': 50,
                                'durationSeconds': .1, 'source': {'status': 'ok', 'queries': ['private POI name']}}],
            'timings': {'totalSeconds': 10}}


async def sample(config, cached=0, failed=0, unknown=False):
    @costs.tracked_task(config.database_path, 'search')
    async def run():
        experience.begin(config, payload(), resolved=True)
        costs.record('places-text')
        async def model():
            return SimpleNamespace(usage={'input_tokens': 1000, 'output_tokens': 100})
        await costs.observe('scoring', 'gpt-6-luna', model())
        if unknown:
            async def fail(): raise TimeoutError('private exception detail')
            with pytest.raises(TimeoutError):
                await costs.observe('scoring', 'gpt-6.1-sol', fail())
        return result(cached, failed)
    return await run()


def stored(config):
    with sqlite3.connect(config.database_path) as db:
        return [json.loads(r[0]) for r in db.execute('SELECT record FROM photo_workflow_experience')]


def snapshot(config):
    advice = experience.planner_advice(config)
    return json.JSONDecoder().raw_decode(advice.split('\n', 3)[3])[0] if advice else None


def test_cold_start_then_feed_real_aggregates_without_private_content(tmp_path):
    config = settings(tmp_path)
    assert experience.planner_advice(config) == ''
    for _ in range(4): asyncio.run(sample(config))
    assert experience.planner_advice(config) == ''
    asyncio.run(sample(config))
    advice = experience.planner_advice(config)
    assert 'OPERATIONAL EXPERIENCE' in advice and 'Quality is UNMEASURED' in advice
    group = snapshot(config)['groups'][0]
    assert group['samples'] == 5 and group['completeSamples'] == 5
    assert group['medianUsd'] == pytest.approx(.03215, abs=.0001)
    assert group['p95Seconds'] is None  # Five observations cannot support a useful tail estimate.
    assert group['models'][0]['meanInputTokens'] == 1000
    assert group['tools'][0]['medianItems'] == 50
    text = json.dumps(stored(config)) + advice
    for secret in ('private request', 'private POI', 'private_node_name', 'secret.test', '40.8136634', '-96.7025764'):
        assert secret not in text
    asyncio.run(sample(config))
    assert snapshot(config)['groups'][0]['samples'] == 6


def test_unknown_costs_and_partial_results_do_not_make_cheap_successes(tmp_path):
    config = settings(tmp_path)
    for _ in range(5): asyncio.run(sample(config, unknown=True))
    group = snapshot(config)['groups'][0]
    assert group['medianUsd'] is None and group['completeCostSamples'] == 0
    assert next(m for m in group['models'] if m['model'] == 'gpt-6.1-sol')['failedCalls'] == 5
    for _ in range(5): asyncio.run(sample(config, failed=2))
    group = snapshot(config)['groups'][0]
    assert group['failedOrPartialSamples'] == 5
    assert group['completeSamples'] == 5
    assert group['medianUsd'] is None


def test_cache_workload_revision_and_expiry_are_not_mixed(tmp_path, monkeypatch):
    config = settings(tmp_path)
    for _ in range(5): asyncio.run(sample(config))
    for _ in range(4): asyncio.run(sample(config, cached=290))
    groups = snapshot(config)['groups']
    assert len(groups) == 1 and groups[0]['samples'] == 5 and groups[0]['cache'].startswith('cold/')
    monkeypatch.setenv('PHOTO_SCOUT_SCORING_BATCH_SIZE', '8')
    assert experience.planner_advice(config) == ''
    monkeypatch.delenv('PHOTO_SCOUT_SCORING_BATCH_SIZE')
    monkeypatch.setattr(experience, 'code_revision', lambda: 'new-code')
    assert experience.planner_advice(config) == ''
    monkeypatch.undo()
    with sqlite3.connect(config.database_path) as db:
        db.execute('UPDATE photo_workflow_experience SET created=?', (time.time()-31*86400,))
    assert experience.planner_advice(config) == ''
    asyncio.run(sample(config))
    assert len(stored(config)) == 1  # Retention is enforced on subsequent writes.


def test_observe_off_and_database_failure_are_non_intrusive(tmp_path, monkeypatch):
    config = settings(tmp_path)
    monkeypatch.setenv('PHOTO_SCOUT_EXPERIENCE_MODE', 'observe')
    for _ in range(5): asyncio.run(sample(config))
    assert len(stored(config)) == 5 and experience.planner_advice(config) == ''
    monkeypatch.setenv('PHOTO_SCOUT_EXPERIENCE_MODE', 'off')
    asyncio.run(sample(config))
    assert len(stored(config)) == 5
    monkeypatch.setenv('PHOTO_SCOUT_EXPERIENCE_MODE', 'advise')
    with sqlite3.connect(config.database_path) as db:
        db.execute('UPDATE photo_workflow_experience SET record=?', ('not-json',))
    assert experience.planner_advice(config) == ''
    config.database_path = tmp_path/'directory'; config.database_path.mkdir()
    assert asyncio.run(sample(config))['candidatePoiCount'] == 50


def test_nested_workers_record_once_and_cancellation_is_a_failure(tmp_path):
    config = settings(tmp_path)
    @costs.tracked_task(config.database_path, 'search')
    async def outer():
        await sample(config)
        # Worker returns a bool, but the inner search retained its actual result.
        return True
    asyncio.run(outer())
    assert len(stored(config)) == 1 and stored(config)[0]['outcome'] == 'complete'
    @costs.tracked_task(config.database_path, 'search')
    async def cancel():
        experience.begin(config)
        async def operation(): raise asyncio.CancelledError()
        await costs.observe('intent', 'gpt-6.1-sol', operation())
    with pytest.raises(asyncio.CancelledError): asyncio.run(cancel())
    cancelled = stored(config)[1]
    assert cancelled['outcome'] == 'cancelled'
    assert cancelled['cost']['complete'] is False
    assert costs.current.get() is None


def test_parser_receives_advice_but_preserves_user_payload_and_schema(tmp_path, monkeypatch):
    config = settings(tmp_path)
    for _ in range(5): asyncio.run(sample(config))
    seen = []
    class StopBeforePaidCall(Exception): pass
    async def fake_stream(client, request, trace):
        seen.append(request)
        raise StopBeforePaidCall()
    monkeypatch.setattr(intent, 'stream_plan', fake_stream)
    request = intent.IntentRequest(query='Find a lake', lat=40, lon=-96)
    with pytest.raises(StopBeforePaidCall): asyncio.run(intent.parse_intent(config, request))
    assert 'OPERATIONAL EXPERIENCE' in seen[0]['instructions']
    assert json.loads(seen[0]['input'])['query'] == 'Find a lake'
    assert seen[0]['text_format'] is intent.PlannerIntent
    assert seen[0]['model'] == config.openai_model


def test_hint_is_bounded_and_tail_estimate_requires_twenty_samples(tmp_path):
    config = settings(tmp_path)
    for _ in range(20): asyncio.run(sample(config))
    value = snapshot(config)
    assert value['groups'][0]['p95Seconds'] is not None
    encoded = json.dumps(value, separators=(',', ':')).encode()
    assert len(encoded) <= experience.MAX_HINT_BYTES


def test_malformed_telemetry_never_breaks_business_result(tmp_path):
    config = settings(tmp_path)
    malformed = {'scoring': {}, 'executionTrace': None}
    @costs.tracked_task(config.database_path, 'search')
    async def run():
        experience.begin(config)
        return malformed
    assert asyncio.run(run()) is malformed
    assert stored(config)[0]['outcome'] == 'incomplete'


def test_locked_experience_database_fails_open_with_short_deadline(tmp_path):
    config = settings(tmp_path)
    for _ in range(5): asyncio.run(sample(config))
    with sqlite3.connect(config.database_path) as db:
        db.execute('BEGIN EXCLUSIVE')
        started = time.monotonic()
        assert experience.planner_advice(config) == ''
        assert time.monotonic() - started < 1


def test_source_failure_is_partial_and_workloads_are_separate(tmp_path):
    config = settings(tmp_path)
    @costs.tracked_task(config.database_path, 'search')
    async def run():
        experience.begin(config, payload(), resolved=True)
        value = result()
        value['executionTrace'][0]['source']['status'] = 'unavailable'
        return value
    asyncio.run(run())
    record = stored(config)[0]
    assert record['outcome'] == 'partial'
    records = [(i, record) for i in range(5)]
    smaller = json.loads(json.dumps(record))
    smaller['scoring']['checkedImages'] = 30
    records += [(i, smaller) for i in range(5)]
    groups = experience.digest_records(records)
    assert len(groups) == 2
    assert all(g['medianUsd'] is None and g['medianSeconds'] is None for g in groups)
