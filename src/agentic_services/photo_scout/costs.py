"""Per-task paid request accounting; no prompts, images or credentials retained."""
import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
import json
import logging
import os
import sqlite3
import time
import uuid


@dataclass
class TaskSpend:
    path: object
    kind: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    counts: dict = field(default_factory=dict)
    requests: dict = field(default_factory=dict)


current = ContextVar('photo_scout_task_spend', default=None)
# USD per million tokens (Standard), and per provider call, before free tiers.
RATES = {'gpt-6-astra':(10,1,50), 'gpt-6.1-sol':(2,.1,10), 'gpt-6-luna':(.1,.01,.5)}
WRITE_RATES = {'gpt-6-astra':12.5,'gpt-6.1-sol':2.5,'gpt-6-luna':.125}
CALL_RATES = {'places-text':.032, 'places-details-pro':.017, 'places-details-ids':0, 'places-photo':.007, 'routes-essentials':.005,
              'streetview-tile-z0':.002, 'streetview-tile-z1':.002, 'static-streetview':.007}


def tracked_task(path, kind):
    def decorate(fn):
        @wraps(fn)
        async def run(*args, **kwargs):
            if current.get() is not None:
                return await fn(*args, **kwargs)
            token = current.set(TaskSpend(path, kind))
            try:
                return await fn(*args, **kwargs)
            finally:
                current.reset(token)
        return run
    return decorate


def record(stage, model='', usage=None, status='success', quantity=1):
    context = current.get()
    if context is None:
        return
    if hasattr(usage, 'model_dump'):
        usage = usage.model_dump()
    usage = usage if isinstance(usage, dict) else {}
    input_tokens = usage.get('input_tokens', 0)
    output_tokens = usage.get('output_tokens', 0)
    details = usage.get('input_tokens_details') or {}
    cached = details.get('cached_tokens', 0)
    estimate = None
    if model in RATES and usage:
        a,b,c = RATES[model]
        writes = details.get('cache_write_tokens', 0)
        estimate = ((input_tokens-cached-writes)*a+writes*WRITE_RATES[model]+cached*b+output_tokens*c)/1_000_000
    elif model.startswith('gpt-image-2.5') and usage:
        # Direct Images API has no cached-input discount. Image and text inputs
        # have distinct prices; do not pretend unclassified input is free.
        if 'image_tokens' in details and 'text_tokens' in details:
            estimate = (details['image_tokens']*8+details['text_tokens']*5+output_tokens*30)/1_000_000
    elif stage in CALL_RATES:
        estimate = quantity*CALL_RATES[stage]
    try:
        with sqlite3.connect(context.path, timeout=15) as db:
            db.execute('CREATE TABLE IF NOT EXISTS photo_scout_cost_events '
                       '(id TEXT PRIMARY KEY, run TEXT, kind TEXT, created REAL, stage TEXT, model TEXT, '
                       'status TEXT, quantity INTEGER, usage TEXT, estimated_usd REAL)')
            db.execute('INSERT INTO photo_scout_cost_events VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (uuid.uuid4().hex, context.id, context.kind, time.time(), stage, model,
                        status, quantity, json.dumps(usage), estimate))
    except (sqlite3.Error, OSError):
        logging.getLogger(__name__).warning('Photo Scout cost accounting write failed')


async def observe(stage, model, operation):
    try:
        response = await operation
    except Exception:
        record(stage, model, status='failed-usage-unknown')
        raise
    record(stage, model, getattr(response, 'usage', None))
    return response


def reserve_places_request():
    context = current.get()
    if context is None:
        return
    limit = max(1, int(os.getenv('PHOTO_SCOUT_PLACES_REQUESTS_PER_TASK', '12')))
    count = context.counts.get('places-text', 0)
    if count >= limit:
        context.counts['places-budget-reached'] = True
        raise ValueError('Places request budget reached for this task')
    context.counts['places-text'] = count+1


def reserve_photo_request(stage):
    context=current.get()
    if context is None:return
    limits={'places-details-pro':8,'places-details-ids':8,'places-photo':16}
    count=context.counts.get(stage,0)
    if count>=limits[stage]:
        context.counts['places-photo-budget-reached']=True
        raise ValueError('Places photo request budget reached for this task')
    context.counts[stage]=count+1


async def reuse_request(key, operation):
    """Coalesce identical calls only within one task; never leak previous inputs."""
    context = current.get()
    if context is None:
        return await operation()
    if key not in context.requests:
        context.requests[key] = asyncio.create_task(operation())
    return await context.requests[key]


def summary():
    context = current.get()
    if context is None:
        return {}
    try:
        with sqlite3.connect(context.path, timeout=15) as db:
            rows=db.execute('SELECT stage,sum(quantity),sum(estimated_usd),sum(estimated_usd IS NULL) '
                            'FROM photo_scout_cost_events WHERE run=? GROUP BY stage',(context.id,)).fetchall()
        return {'runId':context.id,'basis':'Standard API list prices before free tiers; not a billing statement',
                'estimatedKnownUsd':round(sum(r[2] or 0 for r in rows),6),
                'unknownCostEvents':sum(r[3] for r in rows),
                'placesBudgetReached':bool(context.counts.get('places-budget-reached')),
                'placesPhotoBudgetReached':bool(context.counts.get('places-photo-budget-reached')),
                'stages':[{'stage':s,'requests':n,'estimatedUsd':None if unknown else round(cost or 0,6),
                           'unknownCostEvents':unknown} for s,n,cost,unknown in rows]}
    except sqlite3.Error:
        return {'runId':context.id,'available':False}
