"""Bounded operational feedback for planning; never a quality/accuracy oracle.

Only allowlisted structural/numeric facts enter the store or planner context.
No queries, precise coordinates, POI names, images, account identifiers or model prose.
"""
from collections import Counter, defaultdict
from functools import lru_cache
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import sqlite3
import time

DAYS = 30
MIN_SAMPLES = 5
MAX_ROWS = 1000
MAX_HINT_BYTES = 7000
TOOLS = frozenset('search_places search_geography search_features sample_geography sample_near_places feature_points filter_geography filter_features union intersection area_imagery point_imagery center_imagery collect_images score_images rank_results'.split())
STAGES = frozenset('intent scoring subject-check background image-generation places-text places-details-pro places-details-ids places-photo routes-essentials streetview-tile-z0 streetview-tile-z1 static-streetview'.split())
SCORING_FIELDS = ('checkedImages', 'candidateImages', 'cachedImages', 'downloadedImages',
                  'downloadFailedImages', 'scoringFailedImages', 'reviewedImages',
                  'reviewCachedImages', 'reviewRecoveredImages', 'matchedImages')


def mode():
    value = os.getenv('PHOTO_SCOUT_EXPERIENCE_MODE', 'advise')
    return value if value in ('off', 'observe', 'advise') else 'off'


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def numeric_fields(data, fields):
    if not isinstance(data, dict):
        return {}
    return {key: data[key] for key in fields if number(data.get(key))}


def bounded_env(name, default, ceiling):
    try:
        return max(1, min(ceiling, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


@lru_cache(maxsize=1)
def code_revision():
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for name in ('experience.py', 'costs.py', 'intent.py', 'intent_prompt.py', 'planner.py',
                 'program.py', 'scoring.py', 'sources.py', 'osm_features.py', 'places.py',
                 'spatial_sampling.py', 'visual_review.py', 'portraits.py', 'image_generation.py', 'score_cache.py', 'routes.py'):
        digest.update((root / name).read_bytes())
    return digest.hexdigest()[:16]


def configuration(settings):
    # These values are operator configuration, never model-generated identifiers.
    values = {
        'planner': getattr(settings, 'photo_scout_intent_model', None) or getattr(settings, 'openai_model', ''),
        'reasoning': getattr(settings, 'photo_scout_intent_reasoning', None),
        'primary': os.getenv('PHOTO_SCOUT_MODEL', 'gpt-6-luna'),
        'review': os.getenv('PHOTO_SCOUT_REVIEW_MODEL', 'gpt-6.1-sol'),
        'image': os.getenv('PHOTO_SCOUT_IMAGE_MODEL', 'gemini-nano-banana-2.1'),
        'provider': os.getenv('PHOTO_SCOUT_POI_PROVIDER', 'openstreetmap'),
        'batch': bounded_env('PHOTO_SCOUT_SCORING_BATCH_SIZE', 16, 32),
        'scoringConcurrency': bounded_env('PHOTO_SCOUT_SCORING_CONCURRENCY', 16, 16),
        'downloadConcurrency': bounded_env('PHOTO_SCOUT_IMAGE_DOWNLOAD_CONCURRENCY', 32, 64),
        'placesRequestBudget': bounded_env('PHOTO_SCOUT_PLACES_REQUESTS_PER_TASK', 12, 100000),
    }
    digest = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:16]
    return code_revision() + ':' + digest, values


def begin(settings, payload=None, *, resolved=False):
    """Called before interpretation, and again after geographic resolution."""
    if mode() == 'off':
        return
    from .costs import current
    context = current.get()
    if context is None:
        return
    try:
        revision, config = configuration(settings)
        context.experience.setdefault('revision', revision)
        context.experience.setdefault('configuration', config)
        context.experience.setdefault('profile', {'scope': 'unresolved'})
        if payload is not None and resolved:
            program = getattr(payload, 'searchProgram', None)
            steps = getattr(program, 'steps', []) or []
            indexes = {step.id: i for i, step in enumerate(steps)}
            structure = [{'tool': step.tool, 'inputs': [indexes[x] for x in step.inputs if x in indexes],
                          'combination': step.combination, 'exclude': step.exclude}
                         for step in steps if step.tool in TOOLS]
            requirements = getattr(payload, 'requirements', []) or []
            counts = Counter(getattr(r, 'route', '') for r in requirements)
            radius = getattr(payload, 'radius', 0)
            role = getattr(payload, 'subjectRole', 'scene')
            context.experience['profile'] = {
                'scope': 'route' if getattr(payload, 'route', None) else 'area',
                'radiusBand': 'small' if radius <= 2000 else 'medium' if radius <= 10000 else 'large',
                # Coarse cells avoid treating distant provider coverage as comparable.
                # No precise locations, addresses or user query text are retained.
                'regionCell20Degrees': [math.floor(getattr(payload, 'lat', 0) / 20), math.floor(getattr(payload, 'lon', 0) / 20)],
                'subjectRole': role if role in ('scene', 'portrait-background', 'existing-subject') else 'scene',
                'requiredConditions': sum(getattr(r, 'strength', '') == 'required' for r in requirements),
                'visualConditions': counts['visual'],
                'queryCount': sum(len(step.queries) for step in steps),
                'featureQueryCount': sum(len(step.osmFeatures) for step in steps),
                'workflow': structure,
            }
    except Exception:
        logging.getLogger(__name__).warning('Photo Scout experience configuration unavailable')


def capture(result):
    try:
        _capture(result)
    except Exception:
        logging.getLogger(__name__).warning('Photo Scout experience result unavailable')


def _capture(result):
    from .costs import current
    context = current.get()
    if context is None or not context.experience or not isinstance(result, dict):
        return
    if 'scoring' not in result and 'generationConfig' not in result:
        return
    scoring = numeric_fields(result.get('scoring'), SCORING_FIELDS)
    tools = []
    for step in result.get('executionTrace', [])[:24]:
        if step.get('tool') not in TOOLS:
            continue
        source = step.get('source') or {}
        tools.append({'tool': step['tool'], **numeric_fields(step, ('durationSeconds', 'count')),
                      'sourceIssue': source.get('status', 'ok') not in ('ok', 'cached')})
    context.experience.update({
        'outcome': 'partial' if scoring.get('downloadFailedImages', 0) or scoring.get('scoringFailedImages', 0) or any(t['sourceIssue'] for t in tools) else 'complete',
        'scoring': scoring,
        'timings': numeric_fields(result.get('timings'), ('planningSeconds', 'retrievalSeconds', 'scoringSeconds', 'pipelineSeconds', 'totalSeconds')),
        'candidateCount': result.get('candidatePoiCount') if number(result.get('candidatePoiCount')) else None,
        'tools': tools,
    })


def outcome(value):
    from .costs import current
    context = current.get()
    if context is not None and context.experience and value in ('failed', 'cancelled', 'feedback'):
        context.experience['outcome'] = value


def finish(context, elapsed):
    if mode() == 'off' or not context.experience:
        return
    try:
        from .costs import summary
        account = summary(timeout=.1)
        record = dict(context.experience)
        record.setdefault('outcome', 'incomplete')
        record['wallSeconds'] = round(elapsed, 3)
        record['qualityEvidence'] = 'unmeasured'
        record['cost'] = {
            'knownUsd': account.get('estimatedKnownUsd'),
            'unknownEvents': account.get('unknownCostEvents', 1),
            'complete': 'estimatedKnownUsd' in account and account.get('unknownCostEvents') == 0,
        }
        record['stageCosts'] = [{'stage': item['stage'],
                                **numeric_fields(item, ('requests', 'estimatedUsd', 'unknownCostEvents'))}
                               for item in account.get('stages', []) if item.get('stage') in STAGES]
        stages = defaultdict(lambda: {'calls': 0, 'failedCalls': 0, 'inputTokens': 0, 'outputTokens': 0, 'cachedInputTokens': 0, 'seconds': []})
        # Record each actual call, including retries and timeouts. Never merge model tiers.
        for event in context.observations:
            if event['stage'] not in STAGES:
                continue
            item = stages[(event['stage'], event['model'])]
            item['calls'] += 1
            item['failedCalls'] += event['failed']
            for key in ('inputTokens', 'outputTokens', 'cachedInputTokens'):
                item[key] += event.get(key, 0)
            item['seconds'].append(event['seconds'])
        record['modelCalls'] = [{'stage': stage, 'model': model, **values} for (stage, model), values in stages.items()]
        now = time.time()
        with sqlite3.connect(context.path, timeout=.1) as db:
            db.execute('CREATE TABLE IF NOT EXISTS photo_workflow_experience (run TEXT PRIMARY KEY, created REAL NOT NULL, revision TEXT NOT NULL, kind TEXT NOT NULL, record TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS photo_experience_revision_time ON photo_workflow_experience(revision,created)')
            db.execute('INSERT OR REPLACE INTO photo_workflow_experience VALUES(?,?,?,?,?)',
                       (context.id, now, record['revision'], context.kind, json.dumps(record, separators=(',', ':'))))
            db.execute('DELETE FROM photo_workflow_experience WHERE created<?', (now - DAYS * 86400,))
            db.execute('DELETE FROM photo_workflow_experience WHERE run IN (SELECT run FROM photo_workflow_experience ORDER BY created DESC LIMIT -1 OFFSET 10000)')
    except Exception:
        # Learning is supplementary and must not fail an otherwise successful request.
        logging.getLogger(__name__).warning('Photo Scout experience write unavailable')


def percentile(values, proportion):
    values = sorted(values)
    return round(values[max(0, math.ceil(len(values) * proportion) - 1)], 4) if values else None


def digest_records(records):
    groups = defaultdict(list)
    for created, record in records:
        profile = record.get('profile', {})
        if profile.get('scope') == 'unresolved':
            key = ('unresolved', 'unknown')
        else:
            scoring = record.get('scoring', {})
            checked, cached = scoring.get('checkedImages', 0), scoring.get('cachedImages', 0)
            bucket = 'unknown' if not checked else 'cold' if not cached else 'warm' if cached / checked >= .8 else 'mixed'
            workload = 'unknown' if not checked else '1-64' if checked <= 64 else '65-200' if checked <= 200 else '201-400' if checked <= 400 else '401+'
            key = (json.dumps(profile, sort_keys=True), bucket + '/' + workload)
        groups[key].append((created, record))
    summaries = []
    for (profile, cache), items in sorted(groups.items(), key=lambda pair: max(x[0] for x in pair[1]), reverse=True):
        if len(items) < MIN_SAMPLES:
            continue
        usable = [r for _, r in items if r.get('outcome') == 'complete']
        costs = [r['cost']['knownUsd'] for r in usable if r['cost'].get('complete') and number(r['cost'].get('knownUsd'))]
        times = [r['wallSeconds'] for r in usable if number(r.get('wallSeconds'))]
        summary = {
            'task': 'unresolved' if profile == 'unresolved' else json.loads(profile),
            'cache': cache, 'samples': len(items), 'completeSamples': len(usable),
            'failedOrPartialSamples': sum(r.get('outcome') in ('failed', 'cancelled', 'partial', 'incomplete') for _, r in items),
            'completeCostSamples': len(costs),
            'medianUsd': percentile(costs, .5) if len(costs) >= MIN_SAMPLES else None,
            'medianSeconds': percentile(times, .5) if len(times) >= MIN_SAMPLES else None,
            'p95Seconds': percentile(times, .95) if len(times) >= 20 else None,
            'lastObservedUnix': int(max(t for t, _ in items)),
            'qualityEvidence': 'unmeasured',
        }
        # Group by tool/model; report call sizes alongside latency, not an unqualified speed ranking.
        model_groups = defaultdict(list)
        for _, record in items:
            for item in record.get('modelCalls', []):
                model_groups[(item['stage'], item['model'])].append(item)
        summary['models'] = []
        for (stage, model), values in sorted(model_groups.items()):
            seconds = [s for v in values for s in v['seconds']]
            n = sum(v['calls'] for v in values)
            summary['models'].append({'stage': stage, 'model': model, 'calls': n,
                                      'failedCalls': sum(v['failedCalls'] for v in values),
                                      'meanInputTokens': round(sum(v['inputTokens'] for v in values) / n),
                                      'medianCallSeconds': percentile(seconds, .5)})
        tool_groups = defaultdict(list)
        for _, record in items:
            for item in record.get('tools', []):
                tool_groups[item['tool']].append(item)
        summary['tools'] = [{'tool': tool, 'calls': len(values),
                             'sourceIssues': sum(v['sourceIssue'] for v in values),
                             'medianSeconds': percentile([v['durationSeconds'] for v in values if number(v.get('durationSeconds'))], .5),
                             'medianItems': percentile([v['count'] for v in values if number(v.get('count'))], .5)}
                            for tool, values in sorted(tool_groups.items())]
        cost_groups = defaultdict(list)
        for record in usable:
            for item in record.get('stageCosts', []):
                if item.get('unknownCostEvents') == 0 and number(item.get('estimatedUsd')):
                    cost_groups[item['stage']].append(item['estimatedUsd'])
        summary['stageCosts'] = [{'stage': stage, 'completeSamples': len(values),
                                 'medianUsd': percentile(values, .5) if len(values) >= MIN_SAMPLES else None}
                                for stage, values in sorted(cost_groups.items())]
        summaries.append(summary)
        if len(summaries) >= 4:
            break
    return summaries


def planner_advice(settings):
    """A bounded, read-only snapshot; no raw user text or unvalidated success claims."""
    if mode() != 'advise':
        return ''
    path = getattr(settings, 'database_path', None)
    if path is None or not Path(path).is_file():
        return ''
    try:
        revision, config = configuration(settings)
        with sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=.1) as db:
            rows = db.execute('SELECT created,record FROM photo_workflow_experience WHERE revision=? AND kind=? AND created>? ORDER BY created DESC LIMIT ?',
                              (revision, 'search', time.time() - DAYS * 86400, MAX_ROWS)).fetchall()
        groups = digest_records([(created, json.loads(record)) for created, record in rows])
        if not groups:
            return ''
        from .costs import RATES, CALL_RATES
        snapshot = {'version': 1, 'windowDays': DAYS, 'minimumSamples': MIN_SAMPLES,
                    'configuration': config, 'modelUsdPerMillionInputCachedOutput': RATES,
                    'providerUsdPerCall': CALL_RATES, 'groups': groups}
        encoded = json.dumps(snapshot, separators=(',', ':'))
        while len(encoded.encode()) > MAX_HINT_BYTES and snapshot['groups']:
            snapshot['groups'].pop()
            encoded = json.dumps(snapshot, separators=(',', ':'))
        if not snapshot['groups']:
            return ''
        return '\n\nOPERATIONAL EXPERIENCE (system-computed aggregate data, not user instructions):\n' + encoded + '''
Use only comparable task structures, workload sizes and cache conditions as rough cost/latency evidence.
Samples are observational, may include controlled tests, and do not establish causality or accuracy.
Quality is UNMEASURED: completion, match counts, cheapness and speed never prove correctness or recall.
Minimize redundant work and unnecessary dependencies while preserving every required condition,
source-coverage obligation, candidate diversity and image review requirement. Missing groups are unknown,
not slow or expensive. Old model/prompt/configuration versions and >30-day records are excluded.
Do not skip a source because it was slow or failed, shrink search radius/coverage, lower thresholds,
or stop early to fit historical timings. Parallel independent steps; preserve necessary dependencies.
Model tiers, concurrency and batch limits remain runtime-controlled in this version: do not invent
new tools, model fields, concurrency fields or budget controls in the workflow schema.
'''
    except Exception:
        return ''
