"""Provider benchmark checks without credentials or paid network requests."""
import base64
import importlib.util
import io
from pathlib import Path

import pytest
from PIL import Image


def module(name):
    path = Path(__file__).parents[1] / 'scripts' / f'benchmark-photo-scout-{name}.py'
    spec = importlib.util.spec_from_file_location(f'benchmark_{name}', path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def image(format, color):
    output = io.BytesIO()
    Image.new('RGB', (16, 16), color).save(output, format=format)
    return output.getvalue()


@pytest.mark.parametrize('provider', ['vertex', 'seedream'])
def test_references_keep_subject_then_scene_with_same_production_prompt(provider):
    benchmark = module(provider)
    subject, background = image('PNG', 'red'), image('JPEG', 'blue')
    case = {'place': 'Test plaza', 'scene': {'core_landmarks': ['Fixed sculpture']},
            'pose': 'Standing naturally'}
    args = [case, subject, background] + (['flash'] if provider == 'seedream' else [])
    prompt, body = benchmark.payload(*args)
    other = module('seedream' if provider == 'vertex' else 'vertex')
    other_args = [case, subject, background] + ([] if provider == 'seedream' else ['flash'])
    assert prompt == other.payload(*other_args)[0]
    if provider == 'vertex':
        references = body['contents'][0]['parts'][1:]
        assert references[0]['inlineData']['mimeType'] == 'image/png'
        assert references[1]['inlineData']['mimeType'] == 'image/jpeg'
        encoded = [p['inlineData']['data'] for p in references]
        assert body['generationConfig']['imageConfig']['imageSize'] == '1K'
    else:
        encoded = [p.split(',', 1)[1] for p in body['image']]
        assert body['model'] == benchmark.MODELS['flash']
        assert 'sequential_image_generation' not in body
        assert body['response_format'] == 'b64_json'
    assert [base64.b64decode(p) for p in encoded] == [subject, background]


def test_vertex_estimate_counts_both_image_inputs_and_thinking():
    benchmark = module('vertex')
    usage = {'promptTokenCount': 4383, 'thoughtsTokenCount': 916,
             'candidatesTokensDetails': [{'modality': 'IMAGE', 'tokenCount': 1120}]}
    assert benchmark.estimate(usage, 'gemini-nano-banana-2.1') == 0.047045
    assert benchmark.estimate(usage, 'unknown-model') is None
    assert benchmark.estimate({}, 'gemini-nano-banana-2.1') is None
