from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Literal, Protocol
from urllib.parse import urlsplit

from .models import (
    AtomicFact,
    ClaimVerificationRequest,
    ClaimVerificationResult,
    Conflict,
    Evidence,
    ProviderSource,
    SnapshotStatus,
    VerificationProvenance,
    VerificationStatus,
)
from .provider import EvidenceProvider, ProviderResult, normalize_url
from .snapshot import SnapshotCapture, WebSnapshotter
from .storage import VerificationStore


class IdempotencyConflictError(Exception):
    pass


class Snapshotter(Protocol):
    def capture_many(self, urls: list[str]) -> list[SnapshotCapture]: ...


class ClaimVerificationService:
    def __init__(
        self,
        *,
        provider: EvidenceProvider,
        store: VerificationStore,
        snapshotter: Snapshotter | None = None,
    ) -> None:
        self.provider = provider
        self.store = store
        self.snapshotter = snapshotter or WebSnapshotter()

    def verify(
        self,
        request: ClaimVerificationRequest,
        *,
        idempotency_key: str | None = None,
        idempotency_namespace: str = "standard",
        max_tool_calls: int | None = None,
        max_output_tokens: int | None = None,
        snapshot_mode: Literal["none", "cited", "all_sources"] = "cited",
        max_snapshots: int = 3,
    ) -> ClaimVerificationResult:
        request_json = request.model_dump_json(by_alias=True, exclude_none=True)
        request_hash = hashlib.sha256(request_json.encode("utf-8")).hexdigest()

        scoped_idempotency_key = (
            f"{idempotency_namespace}:{idempotency_key}" if idempotency_key else None
        )
        if scoped_idempotency_key:
            stored = self.store.get_by_idempotency_key(scoped_idempotency_key)
            if stored:
                stored_hash, stored_result = stored
                if stored_hash != request_hash:
                    raise IdempotencyConflictError(
                        "Idempotency-Key was already used for a different request"
                    )
                return stored_result.model_copy(
                    update={
                        "provenance": stored_result.provenance.model_copy(
                            update={"cache_hit": True}
                        )
                    }
                )

        observed_at = datetime.now(UTC)
        provider_result = self.provider.analyze(
            request,
            max_tool_calls=max_tool_calls,
            max_output_tokens=max_output_tokens,
        )
        result = self._build_result(
            request,
            provider_result,
            observed_at,
            snapshot_mode=snapshot_mode,
            max_snapshots=max_snapshots,
        )
        self.store.save(
            result=result,
            request_hash=request_hash,
            request_json=request_json,
            idempotency_key=scoped_idempotency_key,
        )
        return result

    def _build_result(
        self,
        request: ClaimVerificationRequest,
        provider_result: ProviderResult,
        observed_at: datetime,
        *,
        snapshot_mode: Literal["none", "cited", "all_sources"],
        max_snapshots: int,
    ) -> ClaimVerificationResult:
        analysis = provider_result.analysis
        referenced_evidence_ids = {
            evidence_id
            for fact in analysis.atomic_facts
            for evidence_id in fact.evidence_ids
        }
        if request.include_conflicts:
            referenced_evidence_ids.update(
                evidence_id
                for conflict in analysis.conflicts
                for evidence_id in conflict.evidence_ids
            )

        provider_sources = [source.model_copy(deep=True) for source in provider_result.provider_sources]
        sources_by_url = {normalize_url(source.url): source for source in provider_sources}
        evidence: list[Evidence] = []
        evidence_ids: set[str] = set()
        cited_matched_ids: set[str] = set()

        for item in analysis.evidence[: request.max_sources]:
            if item.id in evidence_ids:
                continue
            hostname = (urlsplit(item.url).hostname or "").lower().rstrip(".")
            if any(
                hostname == domain or hostname.endswith(f".{domain}")
                for domain in request.blocked_domains
            ):
                continue
            evidence_ids.add(item.id)
            source = sources_by_url.get(normalize_url(item.url))
            matched = source is not None
            cited = item.id in referenced_evidence_ids
            if source:
                if item.id not in source.evidence_ids:
                    source.evidence_ids.append(item.id)
                source.cited = source.cited or cited
            if matched and cited:
                cited_matched_ids.add(item.id)
            evidence.append(
                Evidence(
                    **item.model_dump(),
                    retrieved_at=observed_at,
                    provider_source_matched=matched,
                    cited=cited,
                    consulted=matched,
                    snapshotted=False,
                )
            )

        atomic_facts: list[AtomicFact] = []
        for fact in analysis.atomic_facts:
            valid_ids = [value for value in fact.evidence_ids if value in evidence_ids]
            has_matched_citation = bool(set(valid_ids) & cited_matched_ids)
            status = fact.status if has_matched_citation else VerificationStatus.INSUFFICIENT_EVIDENCE
            explanation = fact.explanation
            if not has_matched_citation:
                explanation = (
                    f"{explanation} No cited evidence URL could be matched to the "
                    "provider's web-search source metadata."
                )
            atomic_facts.append(
                AtomicFact(
                    statement=fact.statement,
                    status=status,
                    confidence=(
                        fact.confidence if has_matched_citation else min(fact.confidence, 0.49)
                    ),
                    explanation=explanation,
                    evidence_ids=valid_ids,
                )
            )

        conflicts = [
            Conflict(
                summary=conflict.summary,
                evidence_ids=[value for value in conflict.evidence_ids if value in evidence_ids],
            )
            for conflict in analysis.conflicts
        ] if request.include_conflicts else []

        snapshot_urls: list[str] = []
        if snapshot_mode == "cited":
            snapshot_urls = [item.url for item in evidence if item.cited and item.provider_source_matched]
        elif snapshot_mode == "all_sources":
            snapshot_urls = [
                source.url
                for source in provider_sources
                if not self._domain_blocked(source.url, request.blocked_domains)
            ]
        snapshot_urls = list(dict.fromkeys(snapshot_urls))[:max_snapshots]

        captures = self.snapshotter.capture_many(snapshot_urls) if snapshot_urls else []
        snapshots = []
        snapshots_by_url = {}
        for capture in captures:
            self.store.save_snapshot(capture.metadata, capture.content)
            snapshots.append(capture.metadata)
            snapshots_by_url[normalize_url(capture.metadata.requested_url)] = capture.metadata
            if capture.metadata.final_url:
                snapshots_by_url[normalize_url(capture.metadata.final_url)] = capture.metadata

        for source in provider_sources:
            snapshot = snapshots_by_url.get(normalize_url(source.url))
            if snapshot:
                source.snapshot_id = snapshot.snapshot_id
        for item in evidence:
            snapshot = snapshots_by_url.get(normalize_url(item.url))
            if snapshot:
                item.snapshot_id = snapshot.snapshot_id
                item.snapshotted = snapshot.status is SnapshotStatus.CAPTURED

        limitations = list(analysis.limitations)
        failed_snapshots = sum(item.status is not SnapshotStatus.CAPTURED for item in snapshots)
        if failed_snapshots:
            limitations.append(
                f"{failed_snapshots} requested source snapshot(s) could not be captured; "
                "their failure records are included."
            )

        provider_source_count = len(provider_sources)
        matched_count = sum(item.provider_source_matched for item in evidence)
        cited_urls = {
            normalize_url(item.url)
            for item in evidence
            if item.cited and item.provider_source_matched
        }
        cited_count = len(cited_urls)
        status = analysis.status
        if cited_count < request.minimum_sources:
            status = VerificationStatus.INSUFFICIENT_EVIDENCE
            limitations.append(
                f"Only {cited_count} cited source(s) were matched to provider source metadata; "
                f"{request.minimum_sources} required."
            )

        return ClaimVerificationResult(
            verification_id=f"cv_{uuid.uuid4().hex}",
            claim=request.claim,
            status=status,
            observed_at=observed_at,
            conclusion=analysis.conclusion,
            atomic_facts=atomic_facts,
            provider_sources=provider_sources,
            evidence=evidence,
            snapshots=snapshots,
            conflicts=conflicts,
            limitations=list(dict.fromkeys(limitations)),
            provenance=VerificationProvenance(
                provider="openai",
                model=provider_result.model,
                provider_response_id=provider_result.provider_response_id,
                searched_web=provider_source_count > 0,
                consulted_source_count=provider_source_count,
                cited_source_count=cited_count,
                provider_source_count=provider_source_count,
                matched_evidence_count=matched_count,
                snapshotted_source_count=sum(
                    item.status is SnapshotStatus.CAPTURED for item in snapshots
                ),
                web_search_call_count=provider_result.web_search_call_count,
                input_tokens=provider_result.input_tokens,
                cached_input_tokens=provider_result.cached_input_tokens,
                output_tokens=provider_result.output_tokens,
            ),
        )

    @staticmethod
    def _domain_blocked(url: str, blocked_domains: list[str]) -> bool:
        hostname = (urlsplit(url).hostname or "").lower().rstrip(".")
        return any(
            hostname == domain or hostname.endswith(f".{domain}")
            for domain in blocked_domains
        )
