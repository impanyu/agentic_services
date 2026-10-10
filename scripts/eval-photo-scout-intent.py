"""Live semantic evaluation of the deployed intent parser; never executes provider tools.

Uses the existing configured API key without printing it. Inputs are synthetic.
Checks meaningful tool/semantic invariants, saves raw plans for human review.
Passing these checks is not a guarantee of full intent equivalence or map quality.
"""
import argparse
import asyncio
from collections import Counter
from dataclasses import replace
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import statistics
import time

from agentic_services.config import Settings
from agentic_services.photo_scout.intent import INSTRUCTIONS,IntentRequest,parse_intent


def checks(plan,expected):
    steps=plan['searchProgram']['steps'] if plan.get('searchProgram') else []
    tools=[s['tool'] for s in steps]
    queries=' | '.join(q.lower() for s in steps for q in s['queries'])
    geo=[k for s in steps for k in s['geographicKinds']]
    visual=' '.join([plan['scoringIntent'],plan['preferences'],*(s['visualIntent'] for s in steps)]).lower()
    failures=[]
    def check(ok,name):
        if not ok:failures.append(name)
    check(tools[-3:]==['collect_images','score_images','rank_results'],'complete_delivery')
    check(plan['clarification'] is None,'no_followup')
    for token in expected.get('queryRequired',[]):check(token.lower() in queries,'query_missing:'+token)
    for group in expected.get('queryAny',[]):check(any(t.lower() in queries for t in group),'query_subject_missing:'+str(group))
    subjects=queries+' '+json.dumps([f for st in steps for f in st['osmFeatures']],ensure_ascii=False).lower()
    for group in expected.get('subjectAny',[]):check(any(t.lower() in subjects for t in group),'mapped_subject_missing:'+str(group))
    features=[f for st in steps for f in st['osmFeatures']]
    for key,value in expected.get('tagsRequired',[]):
        check(any(t['key']==key and t['value']==value and t['required'] for f in features for t in f['filters']),'mapped_tag_missing:'+key)
    if expected.get('pedestrianBridge'):
        check(any(any(t['key']=='bridge' and t['value']=='yes' for t in f['filters']) and any(t['key']=='highway' and t['value'] in ('footway','path','pedestrian','steps') and t['required'] for t in f['filters']) for f in features),'footbridge_broadened_to_any_bridge')
    for key,minimum in expected.get('numericRequired',[]):
        check(any(t['key']==key and t['minimum']==minimum for f in features for t in f['numericFilters']),'mapped_numeric_missing:'+key)
    for mood in expected.get('moodsForbidden',[]):check(mood not in plan['photoStyles'],'invented_mood:'+mood)
    for token in expected.get('queryForbidden',[]):check(token.lower() not in queries,'query_leaks:'+token)
    for group in expected.get('visualRequired',[]):check(any(t.lower() in visual for t in group),'visual_missing:'+str(group))
    for tool in expected.get('toolsRequired',[]):check(tool in tools,'tool_missing:'+tool)
    for tool in expected.get('toolsForbidden',[]):check(tool not in tools,'unexpected_tool:'+tool)
    if 'moods' in expected:check(set(plan['photoStyles'])==set(expected['moods']),'mood_override')
    if 'geo' in expected:check(set(geo)==set(expected['geo']),'geography_role')
    if 'mapCenter' in expected:check(plan['useMapCenter']==expected['mapCenter'],'location_role')
    if 'locationContains' in expected:check(expected['locationContains'].lower() in (plan['locationQuery'] or '').lower(),'proper_location')
    if 'radius' in expected:check(abs(plan['radiusMeters']-expected['radius'])<=1,'radius_units')
    if 'geoCombination' in expected:
        check(any(s['tool']=='search_geography' and s['combination']==expected['geoCombination'] for s in steps),'geo_combination')
    if expected.get('crossBranch'):
        by_id={s['id']:s for s in steps}
        def ancestors(s):
            nodes=[s]
            for ref in s['inputs']:nodes+=ancestors(by_id[ref])
            return nodes
        branches=[]
        for s in steps:
            if s['tool']=='filter_geography':
                upstream=ancestors(s)
                branches.append((' '.join(q.lower() for n in upstream for q in n['queries']),{k for n in upstream for k in n['geographicKinds']}))
        check(any(('cafe' in q or 'coffee' in q) and g=={'lake'} for q,g in branches),'cafe_lake_branch')
        check(any('restaurant' in q and g=={'forest'} for q,g in branches),'restaurant_forest_branch')
        check('union' in tools,'branch_union')
    if expected.get('excludedGeography'):
        check(any(s['tool']=='filter_geography' and s['exclude'] for s in steps) or any(t in visual for t in ['not near','away from','not beside','exclude','avoid']),'negation_lost')
    return failures


async def main(args):
    settings=Settings.from_environment()
    if args.model:settings=replace(settings,openai_model=args.model)
    cases=json.loads(Path(args.cases).read_text())
    if args.case_ids:cases=[c for c in cases if c['id'] in args.case_ids.split(',')]
    slots=asyncio.Semaphore(args.concurrency)
    results=[];started=time.monotonic()
    async def one(case,repeat):
        async with slots:
            stamp=time.monotonic()
            request={'query':case['text'],'lat':41.8827,'lon':-87.6233,'radius':5000,'photoStyles':['waterside'],**case.get('input',{})}
            entry={'case':case['id'],'repeat':repeat,'input':request,'expected':case['expected']}
            try:
                plan=await parse_intent(settings,IntentRequest.model_validate(request))
                entry.update(plan=plan.model_dump(),failures=checks(plan.model_dump(),case['expected']))
            except Exception as error:
                entry.update(failures=['runtime:'+type(error).__name__])
                if hasattr(error,'errors'):entry['validation']=[{'loc':e['loc'],'type':e['type'],'message':e['msg']} for e in error.errors()]
            entry['seconds']=round(time.monotonic()-stamp,3)
            results.append(entry)
            print(json.dumps({'case':entry['case'],'repeat':repeat,'failures':entry['failures'],'seconds':entry['seconds']}),flush=True)
    await asyncio.gather(*(one(c,r) for c in cases for r in range(1,args.repeats+1)))
    durations=sorted(r['seconds'] for r in results)
    summary={'model':settings.openai_model,'promptSha256':hashlib.sha256(INSTRUCTIONS.encode()).hexdigest(),
        'generatedAt':datetime.now(timezone.utc).isoformat(),'cases':len(cases),'runs':len(results),
        'passed':sum(not r['failures'] for r in results),'failed':sum(bool(r['failures']) for r in results),
        'medianSeconds':round(statistics.median(durations),3),'p95Seconds':durations[min(len(durations)-1,int(len(durations)*.95))],
        'wallSeconds':round(time.monotonic()-started,3),'failureTypes':dict(Counter(f for r in results for f in r['failures'])),
        'scope':'Synthetic planner-only checks plus saved plans for human review; not provider execution, geocoding or visual scoring.'}
    Path(args.output).write_text(json.dumps({'summary':summary,'results':sorted(results,key=lambda r:(r['case'],r['repeat']))},ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--cases',required=True);p.add_argument('--output',required=True)
    p.add_argument('--repeats',type=int,default=2);p.add_argument('--concurrency',type=int,default=4)
    p.add_argument('--model');p.add_argument('--case-ids')
    asyncio.run(main(p.parse_args()))
