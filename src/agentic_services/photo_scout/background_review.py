"""Reuse validated background-only decisions for byte-identical model inputs.

Stores model assessments, never source pixels, uploaded portraits or prompts.
Any image, framing label, instruction, model or schema change creates a new key.
"""
import hashlib,json
from .costs import current,observe
from .score_cache import ScoreCache

async def review_background(client,model,response_type,*,image_count,**request):
    context=current.get();cache=None;key=None
    if context is not None:
        cache=ScoreCache(context.path)
        identity={'namespace':'portrait-background-v1','model':model,'schema':response_type.model_json_schema(),'request':request}
        key=hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        saved=cache.get(key)
        try:
            result=response_type.model_validate(saved) if saved else None
        except ValueError:result=None
        if result is not None and result.index<image_count and result.distortion!='severe':
            context.counts['background-review-reused']=context.counts.get('background-review-reused',0)+1
            return result
    response=await observe('background',model,client.responses.parse(model=model,text_format=response_type,**request))
    result=response.output_parsed
    if cache is not None and isinstance(result,response_type) and result.index<image_count and result.distortion!='severe':
        cache.put([(key,result.model_dump())])
    return result
