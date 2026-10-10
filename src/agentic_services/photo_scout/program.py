"""Validated discovery programs composed from a small, typed tool registry.

Models write a bounded data-flow program, never executable Python. Tools own
provider I/O and spatial operations; the executor only resolves dependencies.
"""
from __future__ import annotations
import asyncio
import json
import time
from copy import deepcopy
from dataclasses import dataclass, field
from itertools import product
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator
from .geography import GeographicKind,filter_places,geographic_places,GeographicMatcher
from .osm_features import OSMFeatureQuery,matches_features

Tool=Literal['search_places','search_geography','search_features','sample_geography',
             'feature_points','filter_geography','filter_features','union','intersection','area_imagery','point_imagery','collect_images','score_images','rank_results']

class SearchStep(BaseModel):
    model_config=ConfigDict(extra='forbid')
    id:str=Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_]{0,39}$')
    tool:Tool
    inputs:list[str]=Field(default_factory=list,max_length=6)
    queries:list[str]=Field(default_factory=list,max_length=4)
    geographicKinds:list[GeographicKind]=Field(default_factory=list,max_length=6)
    osmFeatures:list[OSMFeatureQuery]=Field(default_factory=list,max_length=6)
    combination:Literal['all','any']='all'
    exclude:bool=False
    discoveryHints:bool=False
    weights:list[int]=Field(default_factory=list,max_length=6)
    visualIntent:str=Field(default='',max_length=1000)

    @model_validator(mode='after')
    def validate_arguments(self):
        if any(not q.strip() or len(q)>200 for q in self.queries):raise ValueError('Invalid Places query')
        self.queries=list(dict.fromkeys(q.strip() for q in self.queries))
        if bool(self.queries)!=(self.tool=='search_places'):raise ValueError('Only search_places accepts nonempty queries')
        if bool(self.geographicKinds)!=(self.tool=='search_geography'):raise ValueError('Only search_geography accepts nonempty geographicKinds')
        if bool(self.osmFeatures)!=(self.tool=='search_features'):raise ValueError('Only search_features accepts nonempty osmFeatures')
        if self.exclude and self.tool not in ('filter_geography','filter_features'):raise ValueError('Exclusion requires a spatial filter')
        if self.discoveryHints and self.tool!='search_places':raise ValueError('Hints apply only to search_places')
        if self.combination!='all' and self.tool not in ('search_geography','search_features','sample_geography','feature_points','filter_geography','filter_features'):raise ValueError('This tool has no combination argument')
        if self.weights and (self.tool!='union' or len(self.weights)!=len(self.inputs) or any(w<1 or w>4 for w in self.weights)):
            raise ValueError('Union weights must match inputs and be between one and four')
        return self

# Input and output types are also the registry contract exposed to the planner.
CONTRACTS={
 'search_places':([], 'places'), 'search_geography':([], 'geography'),
 'search_features':([], 'features'), 'sample_geography':(['geography'],'places'),
 'feature_points':(['features'],'places'), 'filter_geography':(['places','geography'],'places'),
 'filter_features':(['places','features'],'places'), 'union':('places','places'),
 'intersection':('places','places'), 'area_imagery':([],'area'), 'point_imagery':([],'area'),
 'collect_images':(['locations'],'images'),'score_images':(['images'],'assessments'),
 'rank_results':(['assessments'],'report'),
}

class SearchProgram(BaseModel):
    model_config=ConfigDict(extra='forbid')
    steps:list[SearchStep]=Field(min_length=1,max_length=24)
    output:str=Field(min_length=1,max_length=40)

    @model_validator(mode='after')
    def validate_program(self):
        types={};by_id={};source_count=0
        for s in self.steps:
            if s.id in types:raise ValueError('Duplicate step ID')
            if any(ref not in types for ref in s.inputs):raise ValueError('Inputs must reference earlier steps; cycles and forward references are invalid')
            expected,out=CONTRACTS[s.tool]
            actual=[types[x] for x in s.inputs]
            if isinstance(expected,str):
                if len(actual)<2 or any(t!=expected for t in actual):raise ValueError('Set operations need at least two place sets')
            elif not (actual==expected or expected==['locations'] and len(actual)==1 and actual[0] in ('places','area')):raise ValueError(f'Invalid input types for {s.tool}')
            if s.tool.startswith('search_'):source_count+=1
            types[s.id]=out;by_id[s.id]=s
        if source_count>8:raise ValueError('At most eight source searches per program')
        if types.get(self.output) not in ('places','area','report'):raise ValueError('Output must be places, area imagery, or a final report')
        reached=set()
        def visit(ref):
            if ref in reached:return
            reached.add(ref)
            for dependency in by_id[ref].inputs:visit(dependency)
        visit(self.output)
        if len(reached)!=len(self.steps):raise ValueError('Unused steps are invalid')
        if types[self.output]=='area' and len(self.steps)!=1:raise ValueError('Area imagery is a standalone plan')
        pipeline=[s for s in self.steps if s.tool in ('collect_images','score_images','rank_results')]
        if pipeline and (types[self.output]!='report' or [s.tool for s in pipeline]!=['collect_images','score_images','rank_results']):
            raise ValueError('Image delivery requires exactly one collect -> score -> rank chain ending in a report')
        return self

    @property
    def complete(self):return self.steps[-1].tool=='rank_results'

    def retrieval(self):
        if not self.complete:return self
        output=next(s.inputs[0] for s in self.steps if s.tool=='collect_images')
        by_id={s.id:s for s in self.steps};seen=set()
        def visit(ref):
            if ref in seen:return
            seen.add(ref)
            for dependency in by_id[ref].inputs:visit(dependency)
        visit(output)
        return SearchProgram(steps=[s for s in self.steps if s.id in seen],output=output)

    def with_delivery(self):
        if self.complete:return self
        used={s.id for s in self.steps}
        def label(base):
            while base in used:base+='x'
            used.add(base);return base
        images,scored,report=label('images'),label('scored'),label('report')
        return SearchProgram(steps=self.steps+[
            SearchStep(id=images,tool='collect_images',inputs=[self.output]),
            SearchStep(id=scored,tool='score_images',inputs=[images]),
            SearchStep(id=report,tool='rank_results',inputs=[scored])],output=report)


@dataclass
class Places:
    rows:list[dict]
    # Each ID has alternative successful paths through the program. A path is
    # the conjunction of its source targets, spatial filters and visual intents.
    paths:dict[str,list[dict]]=field(default_factory=dict)

@dataclass
class Geography:
    features:list[dict]
    paths:list[dict]
    kinds:list[str]
    combination:str="all"

@dataclass
class Features:
    groups:list[list[dict]]
    queries:list[OSMFeatureQuery]
    combination:str="all"

@dataclass
class Area:
    point_only: bool = False


def unique_paths(paths):
    result=[];seen=set()
    for p in paths:
        key=json.dumps(p,sort_keys=True)
        if key not in seen:seen.add(key);result.append(deepcopy(p))
    if len(result)>64:raise ValueError('Too many alternative candidate paths')
    return result


def append_condition(value,step,condition):
    return Places(value.rows,{key:unique_paths([{**path,
        'filters':path['filters']+[condition],
        'visualIntents':path['visualIntents']+([step.visualIntent] if step.visualIntent else [])} for path in paths])
        for key,paths in value.paths.items()})


class Tools:
    """Small independently callable tools; no tool chooses the search strategy."""
    def __init__(self,parameters,providers,database_path,poi_provider,pipeline=None):
        self.p=parameters;self.providers=providers;self.database=database_path;self.poi_provider=poi_provider
        self.memo={};self.statuses={}
        self.geographic_matchers={};self.position_matches={}
        self.pipeline=pipeline;self.execution=None

    async def provider(self,fn,*args,**kwargs):
        key=(id(fn),json.dumps([args,kwargs],sort_keys=True,default=lambda v:v.model_dump() if isinstance(v,BaseModel) else str(v)))
        if key not in self.memo:self.memo[key]=asyncio.create_task(fn(*args,**kwargs))
        return await self.memo[key]

    def empty_path(self,s,targets=None,filters=None):
        return {'targets':targets or [],'filters':filters or [],'visualIntents':[s.visualIntent] if s.visualIntent else []}

    async def search_places(self,s,inputs):
        if self.poi_provider!='google-places':raise ValueError('Free-text search_places requires Google Places')
        p=self.p
        rows,status=await self.provider(self.providers.places,p.lat,p.lon,p.radius,s.queries,limit=60)
        self.statuses[s.id]=status
        if status.get('status')!='ok':
            if s.discoveryHints:return Places([],{})
            raise ValueError('Places search is temporarily unavailable')
        return Places(rows,{r['id']:[self.empty_path(s,targets=[] if s.discoveryHints else [s.id])] for r in rows})

    async def search_geography(self,s,inputs):
        p=self.p
        fs,paths,status=await self.provider(self.providers.geography,p.lat,p.lon,p.radius,s.geographicKinds,self.database)
        self.statuses[s.id]=status
        if status.get('status')!='ok':raise ValueError('Geographic search is temporarily unavailable')
        return Geography(fs,paths,s.geographicKinds,s.combination)

    async def search_features(self,s,inputs):
        p=self.p
        groups,status=await self.provider(self.providers.osm_features,p.lat,p.lon,p.radius,s.osmFeatures,self.database)
        self.statuses[s.id]=status
        if status.get('status')!='ok':raise ValueError('Mapped-feature search is temporarily unavailable')
        return Features(groups,s.osmFeatures,s.combination)

    async def sample_geography(self,s,inputs):
        region=inputs[0];p=self.p
        rows=geographic_places(p.lat,p.lon,p.radius,region.features,region.paths,region.kinds,limit=50,combination="any" if region.combination=="any" else s.combination)
        return Places(rows,{r['id']:[self.empty_path(s,filters=[s.id])] for r in rows})

    async def feature_points(self,s,inputs):
        features=inputs[0]
        rows=list({p['id']:p for group in features.groups for p in group
            if matches_features(p,features.groups,features.queries,combination="any" if features.combination=="any" else s.combination)}.values())
        return Places(rows,{r['id']:[self.empty_path(s,filters=[s.id])] for r in rows})

    def matches(self,row,s,source):
        if isinstance(source,Geography):
            combination="any" if source.combination=="any" else s.combination
            key=(id(source),combination)
            if key not in self.geographic_matchers:
                self.geographic_matchers[key]=GeographicMatcher(source.features,source.kinds,self.p.lat,self.p.lon,combination)
            position=(key,row['lat'],row['lon'])
            if position not in self.position_matches:
                self.position_matches[position]=self.geographic_matchers[key].matches(row['lat'],row['lon'])
            fit=self.position_matches[position]
        else:fit=matches_features(row,source.groups,source.queries,combination="any" if source.combination=="any" else s.combination)
        return not fit if s.exclude else fit

    async def filter_geography(self,s,inputs):
        value,region=inputs
        rows=[r for r in value.rows if self.matches(r,s,region)]
        return append_condition(Places(rows,{r['id']:value.paths[r['id']] for r in rows}),s,s.id)

    async def filter_features(self,s,inputs):return await self.filter_geography(s,inputs)

    async def union(self,s,inputs):
        by_id={};paths={};rows=[];seen=set()
        for value in inputs:
            for row in value.rows:
                by_id.setdefault(row['id'],row)
                paths.setdefault(row['id'],[]).extend(value.paths[row['id']])
        weights=s.weights or [1]*len(inputs)
        rounds=max((len(v.rows)+w-1)//w for v,w in zip(inputs,weights))
        for i in range(rounds):
            for value,w in zip(inputs,weights):
                for row in value.rows[i*w:(i+1)*w]:
                    if row['id'] not in seen:rows.append(by_id[row['id']]);seen.add(row['id'])
        return Places(rows,{r['id']:unique_paths(paths[r['id']]) for r in rows})

    async def intersection(self,s,inputs):
        shared=set.intersection(*(set(v.paths) for v in inputs))
        rows=[r for r in inputs[0].rows if r['id'] in shared];paths={}
        for row in rows:
            alternatives=[]
            lists=[v.paths[row['id']] for v in inputs]
            if __import__('math').prod(len(x) for x in lists)>64:raise ValueError('Too many intersection paths')
            for group in product(*lists):
                alternatives.append({key:list(dict.fromkeys(item for path in group for item in path[key]))
                    for key in ('targets','filters','visualIntents')})
            paths[row['id']]=unique_paths(alternatives)
        return Places(rows,paths)

    async def area_imagery(self,s,inputs):return Area()

    async def point_imagery(self,s,inputs):return Area(point_only=True)

    async def collect_images(self,s,inputs):
        if self.pipeline is None:raise ValueError('Image tools require the full discovery execution context')
        locations=inputs[0]
        if self.pipeline.selected_ids is not None:
            if not isinstance(locations,Places) or not set(self.pipeline.selected_ids).issubset(locations.paths):
                raise ValueError('Selected places changed; search again')
            locations=Places([p for p in locations.rows if p['id'] in self.pipeline.selected_ids],locations.paths)
        pois=locations.rows[:50] if isinstance(locations,Places) else []
        rows,statuses=await self.pipeline.collect(self.p.lat,self.p.lon,self.p.radius,pois,
            **({'visual_exploration':True, **({'point_only':True} if locations.point_only else {}),
                **({'photo_styles':self.p.photoStyles} if self.p.photoStyles else {})} if isinstance(locations,Area) else {}))
        eligible=self.execution.filter_images(rows,locations)
        for provider,status in statuses.items():
            status['geographicallyExcludedImages']=sum(r['provider']==provider for r in rows)-sum(r['provider']==provider for r in eligible)
            status['sampledImages']=sum(r['provider']==provider for r in eligible)
        if isinstance(locations,Area):
            pois=list({p['id']:p for row in eligible for p in row.get('poiCandidates',[]) if not p['id'].startswith('address:')}.values())
        return Images(eligible,statuses,pois,isinstance(locations,Area))

    async def score_images(self,s,inputs):
        images=inputs[0]
        await self.pipeline.before_score(images)
        payload=self.pipeline.payload
        hints=list(dict.fromkeys(step.visualIntent.strip() for step in self.execution.program.steps
            if step.tool in ('collect_images','score_images','rank_results') and step.visualIntent.strip()))
        if hints:
            requirements=list(dict.fromkeys([payload.scoringIntent or payload.preferences,*hints]))
            payload=payload.model_copy(update={'scoringIntent':' AND '.join('('+r+')' for r in requirements if r)})
        output=await self.pipeline.score(payload,images.rows,images.statuses)
        return Assessments(output,images)

    async def rank_results(self,s,inputs):
        assessed=inputs[0]
        result=self.pipeline.rank(self.pipeline.payload,assessed.output,assessed.images.statuses)
        return Report(result,assessed.images)


@dataclass
class PipelineHooks:
    payload: object
    collect: object
    score: object
    rank: object
    before_score: object
    selected_ids: list[str] | None = None

@dataclass
class Images:
    rows:list
    statuses:dict
    places:list
    area:bool

@dataclass
class Assessments:
    output:object
    images:Images

@dataclass
class Report:
    result:dict
    images:Images


@dataclass
class ProgramExecution:
    program:SearchProgram
    tools:Tools
    values:dict
    output:Places|Area|Report
    trace:list[dict]

    def filter_images(self,rows,locations=None):
        locations=self.output if locations is None else locations
        if isinstance(locations,Area):return rows
        by_id={s.id:s for s in self.program.steps};retained=[]
        for row in rows:
            identities={p['id'] for p in row.get('poiCandidates',[])}
            if row.get('poi'):identities.add(row['poi']['id'])
            eligible=[]
            # Sample-only candidates may use other geolocated sources without a
            # POI identity; explicit targets require association with their POI.
            for candidate in locations.rows[:50]:
                for path in locations.paths[candidate['id']]:
                    if path['targets'] and candidate['id'] not in identities:continue
                    if not all(self.tools.matches(row,by_id[f],self.values[by_id[f].inputs[-1]]) for f in path['filters']):continue
                    public={**path,'targetQueries':[by_id[t].queries for t in path['targets']]}
                    eligible.append(public)
            if eligible:retained.append({**row,'eligibleSearchPaths':unique_paths(eligible)})
        return retained


async def execute_program(program,parameters,providers,database_path,poi_provider,pipeline=None):
    tools=Tools(parameters,providers,database_path,poi_provider,pipeline);tasks={};values={};trace=[]
    execution=ProgramExecution(program,tools,values,None,trace)
    tools.execution=execution
    steps_by_id={s.id:s for s in program.steps}
    async def run(step):
        inputs=await asyncio.gather(*(tasks[x] for x in step.inputs))
        started=time.monotonic()
        value=await getattr(tools,step.tool)(step,inputs)
        if isinstance(value,Places):
            intents=([step.visualIntent] if step.visualIntent else [])+[steps_by_id[ref].visualIntent for ref,v in zip(step.inputs,inputs) if isinstance(v,(Geography,Features)) and steps_by_id[ref].visualIntent]
            for paths in value.paths.values():
                for path in paths:
                    for intent in intents:
                        if intent not in path['visualIntents']:path['visualIntents'].append(intent)
        values[step.id]=value
        trace.append({'id':step.id,'tool':step.tool,'inputs':step.inputs,'durationSeconds':round(time.monotonic()-started,3),
            'count':len(value.rows) if isinstance(value,Places) else len(value.features) if isinstance(value,Geography) else sum(len(g) for g in value.groups) if isinstance(value,Features) else len(value.rows) if isinstance(value,Images) else len(value.output.assessments) if isinstance(value,Assessments) else len(value.result.get('spots',[])) if isinstance(value,Report) else 0,
            'source':tools.statuses.get(step.id)})
        return value
    try:
        for step in program.steps:tasks[step.id]=asyncio.create_task(run(step))
        output=await tasks[program.output]
    finally:
        for task in list(tasks.values())+list(tools.memo.values()):
            if not task.done():task.cancel()
        await asyncio.gather(*tasks.values(),*tools.memo.values(),return_exceptions=True)
    order={s.id:i for i,s in enumerate(program.steps)}
    execution.output=output;execution.trace=sorted(trace,key=lambda s:order[s['id']])
    return execution
