#!/usr/bin/env python3
"""Isolated paid BytePlus Seedream multi-reference comparison; no retries.

Credential: BYTEPLUS_API_KEY (environment or an explicitly provided dotenv file).
Uses preset inputs in a manifest; never writes credentials, prompts or image bytes
to logs. Does not alter production configuration or customer history.
"""
import argparse
import asyncio
import base64
import hashlib
import io
import json
import os
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values
from PIL import Image
from agentic_services.photo_scout.portraits import portrait_prompt

MODELS = {'flash': 'dola-seedream-5-0-flash-260915',
          'pro': 'dola-seedream-5-0-pro-260628'}


def payload(case, subject, background, tier):
    prompt = portrait_prompt(case.get('style', 'natural'), case.get('pose', ''),
        place=case['place'], scene=case['scene'],
        composition=case.get('composition', 'full_body'))
    references = []
    for data in (subject, background):
        with Image.open(io.BytesIO(data)) as image:
            mime = Image.MIME[image.format]
        references.append(f'data:{mime};base64,' + base64.b64encode(data).decode())
    return prompt, {'model': MODELS[tier], 'prompt': prompt, 'image': references,
                    'size': '1024x1024', 'response_format': 'b64_json', 'watermark': True}


async def compare(args):
    values = dotenv_values(args.env_file) if args.env_file else {}
    key = os.environ.get('BYTEPLUS_API_KEY') or values.get('BYTEPLUS_API_KEY')
    if not key:
        raise RuntimeError('BYTEPLUS_API_KEY is not configured; no request made')
    cases = json.loads(args.manifest.read_text())
    if not isinstance(cases, list) or not 1 <= len(cases) <= 10:
        raise ValueError('Provide 1–10 cases')
    prepared = []
    for case in cases:
        subject = (args.manifest.parent / case['subject']).read_bytes()
        background = (args.manifest.parent / case['background']).read_bytes()
        for tier in args.tier:
            prompt, body = payload(case, subject, background, tier)
            prepared.append((case, subject, background, tier, prompt, body))
    args.output.mkdir(parents=True, exist_ok=False)
    results = []
    slots = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient(timeout=400, headers={'Authorization': 'Bearer ' + key}) as client:
        async def run(index, data):
            async with slots:
                case, subject, background, tier, prompt, body = data
                digest = lambda value: hashlib.sha256(value).hexdigest()
                item = {'case': case['id'], 'provider': 'byteplus', 'model': MODELS[tier],
                    'size': '1024x1024', 'subjectSha256': digest(subject),
                    'backgroundSha256': digest(background), 'promptSha256': digest(prompt.encode())}
                start = time.monotonic()
                print('Started', case['id'], tier, flush=True)
                try:
                    response = await client.post(
                        'https://ark.ap-southeast.bytepluses.com/api/v3/images/generations', json=body)
                    value = response.json()
                    item['httpStatus'] = response.status_code
                    item['requestId'] = response.headers.get('x-request-id') or value.get('request_id')
                    error = value.get('error') or {}
                    images = value.get('data') or []
                    if response.is_error or error or not images:
                        item.update(state='failed', code=error.get('code') or 'NO_IMAGE')
                    elif not images[0].get('b64_json'):
                        item.update(state='failed', code=(images[0].get('error') or {}).get('code') or 'NO_IMAGE')
                    else:
                        image = Image.open(io.BytesIO(base64.b64decode(images[0]['b64_json'], validate=True)))
                        filename = f'{index}-{tier}.png'
                        image.save(args.output / filename)
                        item.update(state='complete', usage=value.get('usage') or {},
                            dimensions=list(image.size), outputFile=filename,
                            estimatedUsd=0.018 if tier == 'flash' else
                            (0.048 if image.width * image.height <= 2_610_000 else 0.093))
                except Exception as error:
                    item.update(state='failed', errorType=type(error).__name__)
                item['seconds'] = round(time.monotonic() - start, 3)
                results.append(item)
                (args.output / 'results.json').write_text(json.dumps(results, indent=2))
                print(json.dumps(item), flush=True)
        await asyncio.gather(*(run(i, data) for i, data in enumerate(prepared)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--tier', action='append', choices=MODELS, default=None)
    parser.add_argument('--concurrency', type=int, choices=range(1, 4), default=2)
    options = parser.parse_args()
    options.tier = options.tier or ['flash', 'pro']
    asyncio.run(compare(options))
