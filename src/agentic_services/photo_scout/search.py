"""Typed, deterministic location search tool shared by all Photo Scout callers.

The text model extracts parameters; it never chooses merge semantics. Spatial
constraints filter locations, while photographic requirements remain available
to the downstream image evaluator.
"""
from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .geography import GeographicKind, PROXIMITY, filter_places, geographic_places
from .sources import distance, google_query_points
from .osm_features import OSMFeatureQuery, fetch_features, matches_features
from .styles import PHOTO_STYLES, discovery_queries, mapped_categories


class SearchParameters(BaseModel):
    model_config=ConfigDict(extra='forbid')
    lat: float = Field(ge=-85, le=85, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)
    radius: int = Field(default=5000, ge=100, le=20000)
    poiQueries: list[str] = Field(default_factory=list, max_length=4)
    geographicKinds: list[GeographicKind] = Field(default_factory=list, max_length=6)
    osmFeatures: list[OSMFeatureQuery] = Field(default_factory=list,max_length=6)
    photoStyles: list[str] | None = Field(default=None, max_length=8)
    categories: list[str] | None = Field(default=None, max_length=8)
    scoringIntent: str = Field(default='', max_length=1000)
    preferences: str = Field(default='', max_length=500)

    @model_validator(mode='after')
    def validate_parameters(self):
        if self.photoStyles and any(s not in PHOTO_STYLES for s in self.photoStyles):
            raise ValueError('Unknown photo style')
        if self.photoStyles and self.categories:
            raise ValueError('Use photoStyles or categories, not both')
        if any(not q.strip() or len(q)>200 for q in self.poiQueries):
            raise ValueError('POI queries must be nonempty and at most 200 characters')
        self.poiQueries=list(dict.fromkeys(q.strip() for q in self.poiQueries))
        self.geographicKinds=list(dict.fromkeys(self.geographicKinds))
        return self


class SearchPlan(BaseModel):
    parameters: SearchParameters
    placesQueries: list[str]
    placesRole: Literal['target', 'discovery-hints', 'not-requested']
    geographicKinds: list[GeographicKind]
    mergeStrategy: Literal['places-only', 'spatial-intersection', 'spatial-union', 'area-imagery', 'feature-search']
    rawPlacesLimit: int
    candidateLimit: int = 50
    dedupDistanceMeters: int = 50
    geographicProximityMeters: dict[str, int]
    geographicCombination: Literal['all'] = 'all'
    geographicSamplingSpacingMeters: int | None = None
    spatialSampleShare: float = 0


GEOGRAPHIC_QUERIES = {
    'lake':['lakeside parks','lake viewpoints'],
    'sea':['beaches','coastal viewpoints'],
    'river':['riverfront parks','riverwalks'],
    'peak':['mountain peaks','mountain viewpoints'],
    'forest':['forest trails','forest parks'],
    'waterside':['waterfront promenades','waterside parks'],
}


def compile_search(parameters: SearchParameters) -> SearchPlan:
    kinds=parameters.geographicKinds or (['waterside'] if 'waterside' in (parameters.photoStyles or []) else [])
    explicit=bool(parameters.poiQueries or parameters.categories)
    queries=discovery_queries(parameters.poiQueries,parameters.photoStyles,parameters.categories)
    if kinds and not explicit:
        queries=list(dict.fromkeys(q for k in kinds for q in GEOGRAPHIC_QUERIES[k]))[:4]
    strategy=('places-only' if explicit else 'area-imagery') if not kinds else 'spatial-intersection' if explicit else 'spatial-union'
    if parameters.osmFeatures:
        strategy='feature-search'
        if not explicit:queries=[]
    if strategy=='area-imagery':queries=[]
    return SearchPlan(parameters=parameters,placesQueries=queries,placesRole='not-requested' if not queries else 'target' if explicit else 'discovery-hints',
        geographicKinds=kinds,mergeStrategy=strategy,rawPlacesLimit=60 if kinds or parameters.osmFeatures else 50,
        geographicProximityMeters={k:PROXIMITY[k] for k in kinds},
        geographicSamplingSpacingMeters=75 if kinds else None,spatialSampleShare=.8 if strategy=='spatial-union' else 0)


@dataclass
class SearchProviders:
    places: Callable
    osm_places: Callable
    geography: Callable
    osm_features: Callable = fetch_features


@dataclass
class SearchResult:
    places: list[dict]
    features: list[dict]
    status: dict
    plan: SearchPlan

    @property
    def imagery_targets(self):
        if self.plan.mergeStrategy=='area-imagery':
            p=self.plan.parameters
            return [{'lat':lat,'lon':lon,'kind':'area-sample'} for lat,lon in google_query_points(p.lat,p.lon,p.radius)]
        return [{'lat':p['lat'],'lon':p['lon'],'kind':'poi','poiId':p['id'],'name':p.get('name','')} for p in self.places]


class SearchUnavailable(ValueError):
    pass


def merge_candidates(named,generated,plan):
    # Two different businesses may stand only a few meters apart. Deduplicate
    # named results by ID; proximity removes redundant sampled viewpoints only.
    nearby=lambda a,b:distance((a['lat'],a['lon']),(b['lat'],b['lon']))<plan.dedupDistanceMeters
    points=[]
    for p in generated:
        if not any(nearby(p,q) for q in named+points):points.append(p)
    result=[];seen=set()
    # Feature-only exploration reserves four out of five slots for spatial
    # samples; named POIs supplement rather than consume half the coverage.
    if plan.mergeStrategy=='spatial-union':
        groups=[]
        for i in range(max(len(named),math.ceil(len(points)/4))):
            groups.append(([named[i]] if i<len(named) else [])+points[i*4:i*4+4])
    else:groups=zip_longest(named,points)
    for pair in groups:
        for p in pair:
            if p and p['id'] not in seen:
                result.append(p);seen.add(p['id'])
                if len(result)>=plan.candidateLimit:return result
    return result


async def search_locations(parameters: SearchParameters, *, database_path: Path,
                           providers: SearchProviders | None=None,
                           poi_provider: str='google-places') -> SearchResult:
    """Invoke with resolved coordinates and parsed parameters; no model calls."""
    if providers is None:
        from .places import nearby_places
        from .sources import nearby_pois
        from .geography import fetch_region
        providers=SearchProviders(nearby_places,nearby_pois,fetch_region)
    plan=compile_search(parameters)
    if plan.mergeStrategy=='area-imagery':
        return SearchResult([],[],{'status':'ok','provider':poi_provider,'count':0,'role':'area-imagery',
            'searchPlan':plan.model_dump(),'searchCounts':{'rawNamedPlaces':0,'spatiallyMatchedNamedPlaces':0,
            'generatedGeographicPlaces':0,'returnedCandidates':0}},plan)
    async def named():
        if not plan.placesQueries:return [],{'status':'not_requested','provider':poi_provider}
        if poi_provider=='google-places':
            return await providers.places(parameters.lat,parameters.lon,parameters.radius,plan.placesQueries,limit=plan.rawPlacesLimit)
        if parameters.poiQueries:
            raise SearchUnavailable('Free-text POI search requires Google Places')
        categories=mapped_categories(parameters.photoStyles) if parameters.photoStyles else parameters.categories
        return await providers.osm_places(parameters.lat,parameters.lon,parameters.radius,categories) if categories is not None else await providers.osm_places(parameters.lat,parameters.lon,parameters.radius)
    (named_places,named_status),(features,paths,geo_status),(feature_groups,feature_status)=await asyncio.gather(named(),
        providers.geography(parameters.lat,parameters.lon,parameters.radius,plan.geographicKinds,database_path),
        providers.osm_features(parameters.lat,parameters.lon,parameters.radius,parameters.osmFeatures,database_path))
    raw_count=len(named_places)
    if plan.geographicKinds:
        if geo_status['status']!='ok':
            raise SearchUnavailable('Geographic search is temporarily unavailable; please try again later')
        named_places=filter_places(named_places,features,plan.geographicKinds,parameters.lat,parameters.lon)
    if parameters.osmFeatures:
        if feature_status['status']!='ok':raise SearchUnavailable('OSM feature search is temporarily unavailable; please try again later')
        named_places=[p for p in named_places if matches_features(p,feature_groups,parameters.osmFeatures)]
    generated=geographic_places(parameters.lat,parameters.lon,parameters.radius,features,paths,plan.geographicKinds,limit=plan.candidateLimit) if plan.mergeStrategy=='spatial-union' else []
    geographic_generated=len(generated)
    if parameters.osmFeatures and not (parameters.poiQueries or parameters.categories):
        generated=[p for group in feature_groups for p in group if matches_features(p,feature_groups,parameters.osmFeatures)]
        if plan.geographicKinds:generated=filter_places(generated,features,plan.geographicKinds,parameters.lat,parameters.lon)
    if named_status['status'] not in ('ok','not_requested') and not generated:
        raise SearchUnavailable('Nearby place search is temporarily unavailable; please try again later')
    places=merge_candidates(named_places,generated,plan)
    audit={'rawNamedPlaces':raw_count,'spatiallyMatchedNamedPlaces':len(named_places),
           'generatedGeographicPlaces':geographic_generated,'returnedCandidates':len(places)}
    status={**named_status,'status':'ok','namedSourceStatus':named_status['status'],'count':len(places),
            'searchPlan':plan.model_dump(),'searchCounts':audit}
    if parameters.osmFeatures:
        if not plan.placesQueries:status['provider']='openstreetmap-features'
        status['osmFeatureSearch']=feature_status
        status['searchCounts']['osmFeatureCandidates']=sum(len(g) for g in feature_groups)
    if plan.geographicKinds:status['geographicSearch']=geo_status
    return SearchResult(places,features,status,plan)
