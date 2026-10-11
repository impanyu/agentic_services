#!/usr/bin/env python3
"""Paid, isolated Vertex image-edit benchmark; uses gcloud OAuth without logging it.

Uses the same manifest and production prompt as benchmark-photo-scout-images.py.
No automatic retries, no production configuration or history changes.
"""
import argparse
import asyncio
import base64
import hashlib
import io
import json
import re
import subprocess
import time
from pathlib import Path

import httpx
from PIL import Image
from agentic_services.photo_scout.portraits import portrait_prompt


def estimate(usage, model):
    if model != 'gemini-nano-banana-2.1' or 'promptTokenCount' not in usage:
        return None
    details = usage.get('candidatesTokensDetails')
    if not details:
        return None
    image = sum(d['tokenCount'] for d in details if d.get('modality') == 'IMAGE')
    text = sum(d['tokenCount'] for d in details if d.get('modality') == 'TEXT')
    return round((usage['promptTokenCount'] * 1.5 + image * 30
                  + (text + usage.get('thoughtsTokenCount', 0)) * 7.5) / 1_000_000, 6)


def payload(case, subject, background):
    prompt = portrait_prompt(case.get('style', 'natural'), case.get('pose', ''),
        place=case['place'], scene=case['scene'],
        composition=case.get('composition', 'full_body'))
    parts = [{'text': prompt}]
    for data in (subject, background):
        with Image.open(io.BytesIO(data)) as image:
            mime = Image.MIME[image.format]
        parts.append({'inlineData': {'mimeType': mime,
                                    'data': base64.b64encode(data).decode()}})
    return prompt, {'contents': [{'role': 'user', 'parts': parts}],
        'generationConfig': {'responseModalities': ['TEXT', 'IMAGE'],
                             'imageConfig': {'aspectRatio': '1:1', 'imageSize': '1K'}}}


async def compare(args):
    if not re.fullmatch(r'[a-z][a-z0-9-]{4,62}', args.project):
        raise ValueError('Invalid Google Cloud project ID')
    if not re.fullmatch(r'[a-z0-9.-]+', args.model):
        raise ValueError('Invalid model ID')
    cases = json.loads(args.manifest.read_text())
    if not isinstance(cases, list) or not 1 <= len(cases) <= 10:
        raise ValueError('Provide 1–10 cases')
    prepared = []
    for case in cases:
        subject = (args.manifest.parent / case['subject']).read_bytes()
        background = (args.manifest.parent / case['background']).read_bytes()
        prompt, body = payload(case, subject, background)
        prepared.append((case, subject, background, prompt, body))
    token_result = subprocess.run(['gcloud', 'auth', 'print-access-token'],
        capture_output=True, text=True, check=False)
    if token_result.returncode or not token_result.stdout.strip():
        raise RuntimeError('gcloud OAuth credential unavailable; authenticate gcloud first')
    token = token_result.stdout.strip()
    args.output.mkdir(parents=True, exist_ok=False)
    endpoint = (f'https://aiplatform.googleapis.com/v1/projects/{args.project}'
                f'/locations/global/publishers/google/models/{args.model}:generateContent')
    results = []
    slots = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient(timeout=400, headers={
            'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}) as client:
        async def run(index, data):
            async with slots:
                case, subject, background, prompt, body = data
                digest = lambda value: hashlib.sha256(value).hexdigest()
                item = {'case': case['id'], 'provider': 'vertex', 'model': args.model,
                    'size': '1K', 'subjectSha256': digest(subject),
                    'backgroundSha256': digest(background), 'promptSha256': digest(prompt.encode())}
                start = time.monotonic()
                print('Started', case['id'], flush=True)
                try:
                    response = await client.post(endpoint, json=body)
                    value = response.json()
                    item['httpStatus'] = response.status_code
                    item['requestId'] = response.headers.get('x-request-id') or value.get('responseId')
                    if response.is_error:
                        error = value.get('error') or {}
                        item.update(state='failed', code=error.get('status'),
                                    errorCode=error.get('code'))
                    else:
                        candidates = value.get('candidates') or []
                        item['finishReasons'] = [c.get('finishReason') for c in candidates]
                        item['usage'] = value.get('usageMetadata') or {}
                        item['estimatedUsd'] = estimate(item['usage'], args.model)
                        item['promptFeedback'] = value.get('promptFeedback') or {}
                        images = [p['inlineData'] for c in candidates
                            for p in (c.get('content') or {}).get('parts', [])
                            if (p.get('inlineData') or {}).get('mimeType', '').startswith('image/')]
                        if not images:
                            item.update(state='failed', code='NO_IMAGE')
                        else:
                            image = Image.open(io.BytesIO(base64.b64decode(images[0]['data'], validate=True)))
                            filename = f'{index}-{args.model}.png'
                            image.save(args.output / filename)
                            item.update(state='complete', outputFile=filename,
                                        dimensions=list(image.size))
                except Exception as error:
                    # Exception text can contain request material. Store only its class.
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
    parser.add_argument('--project', required=True)
    parser.add_argument('--model', default='gemini-nano-banana-2.1')
    parser.add_argument('--concurrency', type=int, choices=range(1, 4), default=2)
    asyncio.run(compare(parser.parse_args()))
