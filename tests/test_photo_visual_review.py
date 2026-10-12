import asyncio
import json
from types import SimpleNamespace

import pytest

from agentic_services.photo_scout.visual_review import review_candidates
from agentic_services.photo_scout.scoring import ImageAssessment, VisualBatch


def rows():
    poi={'id':'target','name':'Mapped target','lat':-.001,'lon':0}
    return [{'id':str(h),'lat':0,'lon':0,'provider':'google-street-view',
             'imageUrl':f'google-streetview://test/{h}/0/120',
             'viewHeadingDegrees':h,'poi':poi,'poiCandidates':[poi]} for h in range(0,360,45)]


def assessment(id,match=False):
    return ImageAssessment(image_id=id,score=60 if match else None,matches_request=match,
        match_reason='Visible requested form.' if match else 'Required form not identified.',
        recommend=False,name='Visible form' if match else '',visible_evidence='A visible structure.',
        photo_tip='',uncertainty='',confidence='medium')


def test_review_selects_target_facing_view_only_for_complete_negative_panorama():
    items=rows();checks=[assessment(r['id']) for r in items]
    assert [r['viewHeadingDegrees'] for r in review_candidates(items,checks)]==[180]
    assert not review_candidates(items,checks[:-1])
    assert not review_candidates(items[:-1],checks)
    checks[0]=assessment(items[0]['id'],True)
    assert not review_candidates(items,checks)
    for r in items:r['poi']['lat']=-.01
    assert not review_candidates(items,[assessment(r['id']) for r in items])


@pytest.mark.parametrize('review_result',[True,False,'failure'])
def test_review_keeps_primary_evidence_caches_separately_and_fails_closed(tmp_path,monkeypatch,review_result):
    import agentic_services.photo_scout.scoring as visual
    from agentic_services.photo_scout.routes import ExploreRequest
    calls=[];downloads=[]
    monkeypatch.setenv('PHOTO_SCOUT_MODEL','primary')
    monkeypatch.setenv('PHOTO_SCOUT_REVIEW_MODEL','review')
    async def parse(**kw):
        calls.append(kw['model'])
        items=[json.loads(c['text'])['image'] for c in kw['input'][0]['content'][1:] if c['type']=='input_text']
        if kw['model']=='review':
            assert len(items)==1 and items[0]['viewHeadingDegrees']==180
            if review_result=='failure':raise RuntimeError('Unavailable')
        return SimpleNamespace(output_parsed=VisualBatch(assessments=[assessment(r['id'],kw['model']=='review' and review_result is True) for r in items]),usage=None)
    class Client:
        def __init__(self,**kw):self.responses=SimpleNamespace(parse=parse)
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
    async def image(url):downloads.append(url);return 'data:image/jpeg;base64,fixture'
    monkeypatch.setattr(visual,'AsyncOpenAI',Client);monkeypatch.setattr(visual,'image_data',image)
    settings=SimpleNamespace(openai_api_key='fixture',database_path=tmp_path/'db')
    p=ExploreRequest(lat=0,lon=0)
    first=asyncio.run(visual.explore(settings,p,rows(),{}))
    assert calls==['primary','review'] and len(downloads)==8
    assert first['scoring']['checkedImages']==8 and first['scoring']['downloadedImages']==8
    assert first['scoring']['matchedImages']==int(review_result is True)
    assert first['scoring']['reviewedImages']==int(review_result!='failure')
    if review_result!='failure':
        item=next(a for a in first['imageAssessments'] if a['image_id']=='180')
        assert item['review']['original']['matches_request'] is False
        second=asyncio.run(visual.explore(settings,p,rows(),{}))
        assert calls==['primary','review'] and len(downloads)==8
        assert second['scoring']['cachedImages']==8 and second['scoring']['reviewCachedImages']==1
        assert second['scoring']['reviewRecoveredImages']==int(review_result is True)


def test_metadata_outside_requested_anchor_radius_is_rejected(monkeypatch):
    import httpx
    from agentic_services.photo_scout.sources import google_streetview
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_API_KEY','fixture')
    def handler(request):
        return httpx.Response(200,json={'status':'OK','pano_id':'far-away','location':{'lat':.0035,'lng':0}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await google_streetview(client,0,0,20000,[{'id':'house','lat':0,'lon':0}])
    assert asyncio.run(run())==[]
