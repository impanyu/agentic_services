#!/usr/bin/env python3
"""Explicit paid image-edit comparison; independent of production task/history state.

Run with a JSON manifest of cases: id, subject, background, place, scene, pose.
Paths are relative to the manifest. Existing project credentials are used without
being logged. Only run after authorizing API usage. Never automatically retries.
"""
import argparse
import asyncio
import base64
import hashlib
import json
import time
from pathlib import Path

from openai import AsyncOpenAI
from agentic_services.config import Settings
from agentic_services.photo_scout.portraits import portrait_prompt

DEFAULT_VARIANTS = [('gpt-image-2.5-sunburst', 'max'),
                    ('gpt-image-2.5-sunburst', 'high'),
                    ('gpt-image-2.5-flare', 'xhigh')]

def digest(data):
    return hashlib.sha256(data).hexdigest()

def estimate(usage, model):
    details = usage.get('input_tokens_details') or {}
    if not model.startswith('gpt-image-2.5') or not {'image_tokens', 'text_tokens'} <= details.keys():
        return None
    return round((details['image_tokens'] * 8 + details['text_tokens'] * 5
                  + usage['output_tokens'] * 30) / 1_000_000, 6)

async def compare(args):
    cases = json.loads(args.manifest.read_text())
    if not isinstance(cases, list) or not 1 <= len(cases) <= 10:
        raise ValueError('Provide 1–10 cases')
    variants = [tuple(v.split(':', 1)) for v in args.variant] if args.variant else DEFAULT_VARIANTS
    if any(len(v) != 2 for v in variants) or not 1 <= len(variants) <= 6:
        raise ValueError('Use 1–6 model:quality variants')
    settings = Settings.from_environment()
    if not settings.openai_api_key:
        raise RuntimeError('No configured OpenAI credential')
    prepared = []
    for case in cases:
        subject = (args.manifest.parent / case['subject']).read_bytes()
        background = (args.manifest.parent / case['background']).read_bytes()
        prompt = portrait_prompt(case.get('style', 'natural'), case.get('pose', ''),
            place=case['place'], scene=case['scene'], composition=case.get('composition', 'full_body'))
        prepared.append((case, subject, background, prompt))
    args.output.mkdir(parents=True, exist_ok=False)
    slots = asyncio.Semaphore(args.concurrency)
    results = []
    async with AsyncOpenAI(api_key=settings.openai_api_key, max_retries=0, timeout=400) as client:
        async def run(index, data, model, quality):
            async with slots:
                case, subject, background, prompt = data
                label = f'{index}-{model}-{quality}'
                item = {'case': case['id'], 'model': model, 'quality': quality, 'size': '1024x1024',
                    'subjectSha256': digest(subject), 'backgroundSha256': digest(background),
                    'promptSha256': digest(prompt.encode())}
                started = time.monotonic()
                print('Started', label, flush=True)
                try:
                    jpeg = background.startswith(b'\xff\xd8')
                    response = await client.images.edit(model=model, quality=quality,
                        image=[('subject.png', subject, 'image/png'),
                               ('scene.jpg' if jpeg else 'scene.png', background, 'image/jpeg' if jpeg else 'image/png')],
                        prompt=prompt, size='1024x1024', output_format='png', n=1)
                    (args.output / (label + '.png')).write_bytes(base64.b64decode(response.data[0].b64_json, validate=True))
                    usage = response.usage.model_dump() if response.usage else {}
                    item.update(state='complete', usage=usage, estimatedUsd=estimate(usage, model),
                        outputFile=label + '.png', requestId=getattr(response, '_request_id', None))
                except Exception as error:
                    item.update(state='failed', errorType=type(error).__name__,
                        code=getattr(error, 'code', None), requestId=getattr(error, 'request_id', None))
                item['seconds'] = round(time.monotonic() - started, 3)
                results.append(item)
                (args.output / 'results.json').write_text(json.dumps(results, indent=2))
                print(json.dumps(item), flush=True)
        await asyncio.gather(*(run(i, data, model, quality) for i, data in enumerate(prepared)
                               for model, quality in variants))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('output', type=Path, help='New output directory; refusing overwrite')
    parser.add_argument('--variant', action='append', help='model:quality; default max/high/Flare xhigh')
    parser.add_argument('--concurrency', type=int, choices=range(1, 4), default=2)
    options = parser.parse_args()
    asyncio.run(compare(options))
