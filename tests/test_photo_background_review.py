import asyncio
from types import SimpleNamespace
from agentic_services.photo_scout.background_review import review_background
from agentic_services.photo_scout.portraits import BackgroundChoice
from agentic_services.photo_scout.costs import TaskSpend,current

def test_identical_inputs_reuse_only_valid_scene_assessment(tmp_path):
    calls=[]
    async def parse(**request):
        calls.append(request);return SimpleNamespace(output_parsed=BackgroundChoice(index=0,distortion='minimal',reason='Straight architecture'),usage=None)
    client=SimpleNamespace(responses=SimpleNamespace(parse=parse));context=TaskSpend(tmp_path/'db','selfie');token=current.set(context)
    async def run():
        args={'image_count':1,'store':False,'instructions':'Preserve fixed landmarks','input':[{'image':'identical-pixels','fov':90}]}
        first=await review_background(client,'vision',BackgroundChoice,**args)
        second=await review_background(client,'vision',BackgroundChoice,**args)
        assert first.model_dump()==second.model_dump() and len(calls)==1
        for change in [{'input':[{'image':'changed-pixels','fov':90}]},{'input':[{'image':'identical-pixels','fov':45}]},{'instructions':'New scene protection'}]:
            await review_background(client,'vision',BackgroundChoice,**{**args,**change})
        await review_background(client,'new-model',BackgroundChoice,**args)
        assert len(calls)==5 and context.counts['background-review-reused']==1
    try:asyncio.run(run())
    finally:current.reset(token)

def test_severe_or_invalid_index_is_not_reused(tmp_path):
    calls=[]
    async def parse(**request):
        calls.append(request);return SimpleNamespace(output_parsed=BackgroundChoice(index=0,distortion='severe',reason='Stitching seams'),usage=None)
    client=SimpleNamespace(responses=SimpleNamespace(parse=parse));token=current.set(TaskSpend(tmp_path/'db','selfie'))
    async def run():
        for _ in range(2):await review_background(client,'vision',BackgroundChoice,image_count=1,instructions='test',input=[])
        assert len(calls)==2
    try:asyncio.run(run())
    finally:current.reset(token)
