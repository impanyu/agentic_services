from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from urllib.parse import urlsplit

from .models import (
    AtomicFact,
    ClaimVerificationRequest,
    ClaimVerificationResult,
    Conflict,
    Evidence,
    VerificationProvenance,
    VerificationStatus,
)
from .provider import EvidenceProvider, ProviderResult, normalize_url
from .storage import VerificationStore


class IdempotencyConflictError(Exception):
    pass


class ClaimVerificationService:
    def __init__(self, *, provider: EvidenceProvider, store: VerificationStore) -> None:
        self.provider = provider
        self.store = store

    def verify(
        self,
        request: ClaimVerificationRequest,
        *,
        idempotency_key: str | None = None,
        idempotency_namespace: str = "standard",
        max_tool_calls: int | None = None,
        max_output_tokens: int | None = None,
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
                return stored_result

        observed_at = datetime.now(UTC)
        provider_result = self.provider.analyze(
            request,
            max_tool_calls=max_tool_calls,
            max_output_tokens=max_output_tokens,
        )
        result = self._build_result(request, provider_result, observed_at)
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
    ) -> ClaimVerificationResult:
        analysis = provider_result.analysis
        evidence: list[Evidence] = []
        evidence_ids: set[str] = set()
        consulted_cited_ids: set[str] = set()

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
            consulted = normalize_url(item.url) in provider_result.consulted_urls
            if consulted:
                consulted_cited_ids.add(item.id)
            evidence.append(
                Evidence(
                    **item.model_dump(),
                    retrieved_at=observed_at,
                    consulted=consulted,
                    snapshotted=False,
                )
            )

        atomic_facts: list[AtomicFact] = []
        for fact in analysis.atomic_facts:
            valid_ids = [value for value in fact.evidence_ids if value in evidence_ids]
            has_consulted_evidence = bool(set(valid_ids) & consulted_cited_ids)
            status = fact.status if has_consulted_evidence else VerificationStatus.INSUFFICIENT_EVIDENCE
            explanation = fact.explanation
            if not has_consulted_evidence:
                explanation = f"{explanation} No cited source could be matched to the provider's consulted-source list."
            atomic_facts.append(
                AtomicFact(
                    statement=fact.statement,
                    status=status,
                    confidence=fact.confidence if has_consulted_evidence else min(fact.confidence, 0.49),
                    explanation=explanation,
                    evidence_ids=valid_ids,
                )
            )

        limitations = list(analysis.limitations)
        consulted_count = len(provider_result.consulted_urls)
        cited_count = len(consulted_cited_ids)
        status = analysis.status
        if cited_count < request.minimum_sources:
            status = VerificationStatus.INSUFFICIENT_EVIDENCE
            limitations.append(
                f"Only {cited_count} cited source(s) were matched to consulted sources; {request.minimum_sources} required."
            )

        conflicts = [
            Conflict(
                summary=conflict.summary,
                evidence_ids=[value for value in conflict.evidence_ids if value in evidence_ids],
            )
            for conflict in analysis.conflicts
        ] if request.include_conflicts else []

        return ClaimVerificationResult(
            verification_id=f"cv_{uuid.uuid4().hex}",
            claim=request.claim,
            status=status,
            observed_at=observed_at,
            conclusion=analysis.conclusion,
            atomic_facts=atomic_facts,
            evidence=evidence,
            conflicts=conflicts,
            limitations=list(dict.fromkeys(limitations)),
            provenance=VerificationProvenance(
                provider="openai",
                model=provider_result.model,
                provider_response_id=provider_result.provider_response_id,
                searched_web=consulted_count > 0,
                consulted_source_count=consulted_count,
                cited_source_count=cited_count,
            ),
        )
