"""Image provider transport; no retries or silent provider fallback."""
import base64
import io
import os
import re
from types import SimpleNamespace

import httpx
from PIL import Image

DEFAULT_MODEL = 'gemini-nano-banana-2.1'
METADATA = 'http://metadata.google.internal/computeMetadata/v1/'


class ImageProviderError(RuntimeError):
    def __init__(self, code, status_code=None):
        super().__init__('Image generation provider did not return a usable image')
        self.code = code
        self.status_code = status_code


def configuration(model):
    if model.startswith('gemini-'):
        return {'provider': 'google', 'model': model, 'size': '1024x1024', 'resolution': '1K'}
    return {'model': model, 'quality': 'high', 'size': '1024x1024'}


async def google_response(model, subject, background, background_mime, prompt):
    if model != DEFAULT_MODEL:
        raise ImageProviderError('unsupported_image_model')
    parts = [{'text': prompt}]
    subjects=subject if isinstance(subject,list) else [subject]
    if not 1<=len(subjects)<=9:raise ImageProviderError('invalid_subject_count')
    for raw, mime in [(photo,'image/png') for photo in subjects]+[(background,background_mime)]:
        parts.append({'inlineData': {'mimeType': mime, 'data': base64.b64encode(raw).decode()}})
    body = {'contents': [{'role': 'user', 'parts': parts}],
            'generationConfig': {'responseModalities': ['TEXT', 'IMAGE'],
                                 'imageConfig': {'aspectRatio': '1:1', 'imageSize': '1K'}}}
    # Separate Gemini credentials from Maps keys. On GCE use the attached service
    # account, with short-lived metadata tokens; never persist a bearer token.
    async with httpx.AsyncClient(timeout=400, follow_redirects=False) as client:
        key = os.getenv('GEMINI_API_KEY')
        if key:
            endpoint = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
            headers = {'x-goog-api-key': key}
        else:
            metadata_headers = {'Metadata-Flavor': 'Google'}
            project = os.getenv('PHOTO_SCOUT_VERTEX_PROJECT') or os.getenv('GOOGLE_CLOUD_PROJECT')
            if not project:
                response = await client.get(METADATA + 'project/project-id', headers=metadata_headers, timeout=5)
                if response.is_error: raise ImageProviderError('google_credentials_unavailable')
                project = response.text.strip()
            if not re.fullmatch(r'[a-z][a-z0-9-]{4,62}', project):
                raise ImageProviderError('invalid_google_project')
            response = await client.get(METADATA + 'instance/service-accounts/default/token', headers=metadata_headers, timeout=5)
            if response.is_error: raise ImageProviderError('google_credentials_unavailable')
            token = response.json().get('access_token')
            if not token: raise ImageProviderError('google_credentials_unavailable')
            endpoint = f'https://aiplatform.googleapis.com/v1/projects/{project}/locations/global/publishers/google/models/{model}:generateContent'
            headers = {'Authorization': 'Bearer ' + token}
        response = await client.post(endpoint, headers=headers, json=body)
        if response.is_error:
            # Never retain raw provider errors: they can echo request contents.
            raise ImageProviderError('google_image_request_failed', response.status_code)
        value = response.json()
        return SimpleNamespace(usage=value.get('usageMetadata') or {}, value=value)


def google_image(response):
    # Ignore thought images and require a completed candidate. Usage is accounted
    # before decoding, including refusals/text-only replies and malformed images.
    images = [part['inlineData'] for candidate in response.value.get('candidates', [])
              if candidate.get('finishReason') == 'STOP'
              for part in (candidate.get('content') or {}).get('parts', [])
              if not part.get('thought') and (part.get('inlineData') or {}).get('mimeType') in ('image/png', 'image/jpeg', 'image/webp')]
    if len(images) != 1: raise ImageProviderError('google_no_single_image')
    encoded = images[0].get('data', '')
    if len(encoded) > 34_000_000: raise ImageProviderError('google_image_too_large')
    try:
        raw = base64.b64decode(encoded, validate=True)
        with Image.open(io.BytesIO(raw)) as image:
            if image.size != (1024, 1024): raise ImageProviderError('google_unexpected_image_size')
            image.load()
            if image.format == 'PNG': return raw
            output = io.BytesIO()
            image.convert('RGB').save(output, format='PNG')
            return output.getvalue()
    except ImageProviderError:
        raise
    except Exception:
        raise ImageProviderError('google_invalid_image') from None
