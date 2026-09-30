from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator


def to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class SourcePolicy(StrEnum):
    OFFICIAL_ONLY = "official_only"
    AUTHORITATIVE = "authoritative"
    OPEN_WEB = "open_web"


class VerificationStatus(StrEnum):
    CONFIRMED = "confirmed"
    PARTIALLY_CONFIRMED = "partially_confirmed"
    CONTRADICTED = "contradicted"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    AMBIGUOUS = "ambiguous"


class EvidenceRelationship(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CONTEXT = "context"


def _validate_domain(value: str) -> str:
    normalized = value.strip().lower().rstrip(".")
    parsed = urlparse(f"//{normalized}")
    if not normalized or parsed.hostname != normalized or "/" in normalized:
        raise ValueError("domains must be hostnames without scheme or path")
    return normalized


class ClaimVerificationRequest(ApiModel):
    claim: Annotated[str, Field(min_length=3, max_length=4000)]
    as_of: date | None = None
    jurisdiction: Annotated[str | None, Field(max_length=100)] = None
    freshness_hours: Annotated[int | None, Field(ge=1, le=8760)] = 168
    source_policy: SourcePolicy = SourcePolicy.AUTHORITATIVE
    minimum_sources: Annotated[int, Field(ge=1, le=10)] = 2
    max_sources: Annotated[int, Field(ge=1, le=20)] = 8
    allowed_domains: Annotated[list[str], Field(max_length=100)] = []
    blocked_domains: Annotated[list[str], Field(max_length=100)] = []
    include_conflicts: bool = True
    language: Annotated[str, Field(min_length=2, max_length=35)] = "auto"

    @model_validator(mode="after")
    def validate_constraints(self) -> "ClaimVerificationRequest":
        if self.minimum_sources > self.max_sources:
            raise ValueError("minimumSources cannot exceed maxSources")
        self.allowed_domains = [_validate_domain(value) for value in self.allowed_domains]
        self.blocked_domains = [_validate_domain(value) for value in self.blocked_domains]
        overlap = set(self.allowed_domains) & set(self.blocked_domains)
        if overlap:
            raise ValueError(f"domains cannot be both allowed and blocked: {sorted(overlap)}")
        if self.source_policy is SourcePolicy.OFFICIAL_ONLY and not self.allowed_domains:
            raise ValueError("allowedDomains is required when sourcePolicy is official_only")
        return self


class ProviderEvidence(ApiModel):
    id: str
    url: str
    title: str
    publisher: str
    published_at: str | None
    excerpt: str
    relationship: EvidenceRelationship
    source_type: str
    quality_reason: str


class ProviderAtomicFact(ApiModel):
    statement: str
    status: VerificationStatus
    confidence: Annotated[float, Field(ge=0, le=1)]
    explanation: str
    evidence_ids: list[str]


class ProviderConflict(ApiModel):
    summary: str
    evidence_ids: list[str]


class ProviderAnalysis(ApiModel):
    status: VerificationStatus
    conclusion: str
    atomic_facts: list[ProviderAtomicFact]
    evidence: list[ProviderEvidence]
    conflicts: list[ProviderConflict]
    limitations: list[str]


class Evidence(ApiModel):
    id: str
    url: str
    title: str
    publisher: str
    published_at: str | None
    retrieved_at: datetime
    excerpt: str
    relationship: EvidenceRelationship
    source_type: str
    quality_reason: str
    consulted: bool
    snapshotted: bool = False


class AtomicFact(ApiModel):
    statement: str
    status: VerificationStatus
    confidence: Annotated[float, Field(ge=0, le=1)]
    explanation: str
    evidence_ids: list[str]


class Conflict(ApiModel):
    summary: str
    evidence_ids: list[str]


class VerificationProvenance(ApiModel):
    provider: str
    model: str
    provider_response_id: str
    searched_web: bool
    consulted_source_count: int
    cited_source_count: int


class ClaimVerificationResult(ApiModel):
    verification_id: str
    claim: str
    status: VerificationStatus
    observed_at: datetime
    conclusion: str
    atomic_facts: list[AtomicFact]
    evidence: list[Evidence]
    conflicts: list[Conflict]
    limitations: list[str]
    provenance: VerificationProvenance


class Capability(ApiModel):
    id: str
    summary: str
    status: str
    method: str
    path: str


class CapabilitiesResponse(ApiModel):
    service: str
    version: str
    capabilities: list[Capability]
