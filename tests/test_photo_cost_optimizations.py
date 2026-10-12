import asyncio
import copy
import json

import httpx

from agentic_services.photo_scout import costs, places
from agentic_services.photo_scout.score_cache import ScoreCache
from agentic_services.photo_scout.routes import ExploreRequest
from agentic_services.photo_scout.program import SearchProgram


def case(name='cafes'):
    program=SearchProgram.model_validate({'steps':[{'id':name,'tool':'search_places','queries':['cafes']}],'output':name})
    request=ExploreRequest(lat=40,lon=-96,searchProgram=program)
    row={'id':'same-view','imageUrl':'https://images.test/same','viewHeadingDegrees':90,
         'eligibleSearchPaths':[{'targets':[name],'filters':[],'stepIds':[name],
                                'targetQueries':[['cafes']],'visualIntents':[],'requirementIndexes':[]}]}
    return request,row


def test_renamed_workflow_reuses_cache_without_mutating_request(tmp_path):
    request,row=case();other,other_row=case('renamed')
    before=copy.deepcopy((request.model_dump(),row,other.model_dump(),other_row))
    cache=ScoreCache(tmp_path/'db');key=cache.key(row,request,'model','instructions')
    assert key==cache.key(other_row,other,'model','instructions')
    cache.put([(key,{'image_id':'same-view','matches_request':False})])
    assert cache.get(cache.key(other_row,other,'model','instructions'))['matches_request'] is False
    assert before==(request.model_dump(),row,other.model_dump(),other_row)


def test_cache_keeps_visual_conditions_eligible_paths_and_operator_semantics(tmp_path):
    request,row=case();cache=ScoreCache(tmp_path/'db')
    original=cache.key(row,request,'model','instructions')
    for mutation in ('query','intent','path','heading','model','prompt'):
        other=copy.deepcopy(request);image=copy.deepcopy(row);model='model';prompt='instructions'
        if mutation=='query':other.searchProgram.steps[0].queries=['luxury villas']
        if mutation=='intent':other.searchProgram.steps[0].visualIntent='visible lake'
        if mutation=='path':image['eligibleSearchPaths'][0]['visualIntents']=['visible lake']
        if mutation=='heading':image['viewHeadingDegrees']=180
        if mutation=='model':model='different'
        if mutation=='prompt':prompt='new criteria'
        assert original!=cache.key(image,other,model,prompt)


def test_equivalent_places_queries_share_calls_but_distinct_categories_remain(tmp_path,monkeypatch):
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_API_KEY','fixture')
    real=httpx.AsyncClient;calls=[]
    def handler(request):
        body=json.loads(request.content);calls.append(body)
        return httpx.Response(200,json={'places':[{'id':body['textQuery'],'displayName':{'text':'Place'},
                                                'location':{'latitude':40,'longitude':-96}}]})
    monkeypatch.setattr(places.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler)))
    @costs.tracked_task(tmp_path/'db','search')
    async def run():
        first,second=await asyncio.gather(
            places.nearby_places(40,-96,1000,['Coffee   Shops',' coffee shops ','bookstores']),
            places.nearby_places(40,-96,1000,['COFFEE SHOPS','bookstores']))
        assert len(first[0])==len(second[0])==2
        assert costs.summary()['estimatedKnownUsd']==.064
        await places.nearby_places(40,-96,2000,['coffee shops'])
        assert costs.summary()['estimatedKnownUsd']==.096
    asyncio.run(run())
    assert len(calls)==3
