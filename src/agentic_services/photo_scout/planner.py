"""Compact model-facing contracts, compiled into the stable execution protocol."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .program import SearchProgram
from .geography import GeographicKind
from .osm_features import OSMFeatureQuery

class Requirement(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expression: str = Field(min_length=1, max_length=400, description='English expression preserving AND/OR/NOT grouping')
    strength: Literal['required','preferred','forbidden']
    route: Literal['places','geography','features','visual']
    stepIds: list[str] = Field(max_length=8, description='Retrieval steps implementing this condition; empty for shared visual requirements')

class Step(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_]{0,39}$')
    visualIntent: str = Field(default='',max_length=1000)

class PlaceStep(Step):
    tool: Literal['search_places']
    queries: list[str] = Field(min_length=1,max_length=4)
    discoveryHints: bool = False

class GeographyStep(Step):
    tool: Literal['search_geography']
    geographicKinds: list[GeographicKind] = Field(min_length=1,max_length=6)
    combination: Literal['all','any'] = 'all'

class FeatureStep(Step):
    tool: Literal['search_features']
    osmFeatures: list[OSMFeatureQuery] = Field(min_length=1,max_length=6)
    combination: Literal['all','any'] = 'all'

class SampleStep(Step):
    tool: Literal['sample_geography','feature_points']
    inputs: list[str] = Field(min_length=1,max_length=1)
    combination: Literal['all','any'] = 'all'

class FilterStep(Step):
    tool: Literal['filter_geography','filter_features']
    inputs: list[str] = Field(min_length=2,max_length=2)
    combination: Literal['all','any'] = 'all'
    exclude: bool = False

class UnionStep(Step):
    tool: Literal['union']
    inputs: list[str] = Field(min_length=2,max_length=6)
    weights: list[int] = Field(default_factory=list,max_length=6)

class IntersectionStep(Step):
    tool: Literal['intersection']
    inputs: list[str] = Field(min_length=2,max_length=6)

class ImageryStep(Step):
    tool: Literal['area_imagery','point_imagery','center_imagery']

class DeliveryStep(Step):
    tool: Literal['collect_images','score_images','rank_results']
    inputs: list[str] = Field(min_length=1,max_length=1)

PlannerStep = PlaceStep | GeographyStep | FeatureStep | SampleStep | FilterStep | UnionStep | IntersectionStep | ImageryStep | DeliveryStep

class PlannerProgram(BaseModel):
    model_config = ConfigDict(extra='forbid')
    steps: list[PlannerStep] = Field(min_length=4,max_length=24)
    output: str = Field(min_length=1,max_length=40)

    def compile(self):
        return SearchProgram.model_validate(self.model_dump())

    @model_validator(mode='after')
    def valid_program(self):
        if not self.compile().complete:raise ValueError('Complete collect -> score -> rank delivery required')
        return self

class SourceCoverage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    subject: str = Field(min_length=1,max_length=200)
    usefulTools: list[Literal['search_places','search_features','search_geography']] = Field(min_length=1,max_length=3)
    stepIds: list[str] = Field(min_length=1,max_length=8)
    reason: str = Field(min_length=1,max_length=400)

class PlannerIntent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    locationQuery: str | None = Field(max_length=200)
    useMapCenter: bool
    radiusMeters: int = Field(ge=100,le=20000)
    photoStyles: list[Literal['nature','urban','vintage','iconic','artistic','waterside','minimal','adventure']] = Field(max_length=8)
    requirements: list[Requirement] = Field(max_length=16)
    scoringIntent: str = Field(max_length=1000)
    preferences: str = Field(max_length=500)
    explanation: str = Field(max_length=400)
    searchProgram: PlannerProgram
    sourceCoverage: list[SourceCoverage] = Field(default_factory=list,max_length=8)

    @model_validator(mode='after')
    def validate_requirements(self):
        steps={s.id:s for s in self.searchProgram.steps}
        retrieval={s.id for s in steps.values() if s.tool in ('search_places','search_features','search_geography')}
        covered={ref for c in self.sourceCoverage for ref in c.stepIds}
        if retrieval-covered:raise ValueError('Every retrieval source requires a sourceCoverage rationale and implementing step IDs')
        for coverage in self.sourceCoverage:
            if any(ref not in steps for ref in coverage.stepIds):raise ValueError('Source coverage references unknown step')
            selected={steps[ref].tool for ref in coverage.stepIds}
            if not set(coverage.usefulTools)<=selected:raise ValueError('Source coverage omits a declared useful retrieval tool; add its branch and combine candidates with the correct scope')
        for r in self.requirements:
            if any(ref not in steps for ref in r.stepIds):raise ValueError('Requirement references unknown step')
            if any(steps[ref].tool in ('collect_images','score_images','rank_results') for ref in r.stepIds):raise ValueError('Requirements reference retrieval steps, not delivery')
            if r.route!='visual' and not r.stepIds:raise ValueError('Retrieval conditions require implementing step IDs')
            if r.route!='visual':
                allowed={'places':{'search_places'},'geography':{'search_geography','filter_geography','sample_geography'},'features':{'search_features','filter_features','feature_points'}}[r.route]
                if not any(steps[ref].tool in allowed for ref in r.stepIds):raise ValueError('Requirement routed to wrong tool')
        if bool(self.locationQuery)==self.useMapCenter:raise ValueError('Choose one geocoded location or map center')
        return self
