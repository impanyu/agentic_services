"""Bounded disjunction of independently constrained search targets (DNF)."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .geography import GeographicKind
from .osm_features import OSMFeatureQuery


class SearchBranch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    poiQueries: list[str] = Field(default_factory=list, max_length=4)
    geographicKinds: list[GeographicKind] = Field(default_factory=list, max_length=6)
    geographicCombination: Literal['all', 'any'] = 'all'
    osmFeatures: list[OSMFeatureQuery] = Field(default_factory=list, max_length=6)
    featureCombination: Literal['all', 'any'] = 'all'
    visualIntent: str = Field(default='', max_length=1000)

    @model_validator(mode='after')
    def validate_branch(self):
        if any(not q.strip() or len(q)>200 for q in self.poiQueries):
            raise ValueError('Branch POI queries must be nonempty and at most 200 characters')
        self.poiQueries=list(dict.fromkeys(q.strip() for q in self.poiQueries))
        self.geographicKinds=list(dict.fromkeys(self.geographicKinds))
        if not (self.poiQueries or self.geographicKinds or self.osmFeatures):
            raise ValueError('A search branch needs a target or spatial requirement')
        return self


def validate_branch_scope(value):
    if getattr(value,'searchProgram',None) is not None and (value.searchBranches or value.poiQueries or value.geographicKinds or value.osmFeatures or getattr(value,'categories',None)):
        raise ValueError('Put all target/spatial conditions in searchProgram when a program is supplied')
    if value.searchBranches and (value.poiQueries or value.geographicKinds or value.osmFeatures or getattr(value,'categories',None)):
        raise ValueError('Put all target/spatial conditions inside searchBranches; do not mix with top-level conditions')
    return value
