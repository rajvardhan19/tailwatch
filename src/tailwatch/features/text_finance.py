"""Cached, version-bound semantic similarity and point-in-time ratios."""

import math
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from tailwatch.domain.time import Availability, utc
from tailwatch.features.signals import Signal
from tailwatch.sources.policy import Contract, NonEmpty


class Embedding(Contract):
    document_id: NonEmpty
    chunk_id: NonEmpty
    revision: NonEmpty
    published_at: AwareDatetime
    available_at: AwareDatetime
    expires_at: AwareDatetime
    vector: tuple[float, ...] = Field(min_length=1)
    evidence: NonEmpty

    @model_validator(mode="after")
    def finite(self) -> "Embedding":
        if (
            any(not math.isfinite(v) for v in self.vector)
            or self.available_at < self.published_at
            or self.expires_at <= self.published_at
        ):
            raise ValueError("invalid cached embedding")
        return self


def semantic_similarity(
    visitor: list[Embedding], host: list[Embedding], cutoff: datetime
) -> Signal:
    cutoff = utc(cutoff)
    groups = [
        [e for e in group if e.available_at <= cutoff and e.published_at <= cutoff < e.expires_at]
        for group in (visitor, host)
    ]
    if any(not group for group in groups):
        return Signal(status=Availability.MISSING_SOURCE)
    all_vectors = groups[0] + groups[1]
    if (
        len({e.revision for e in all_vectors}) != 1
        or len({len(e.vector) for e in all_vectors}) != 1
    ):
        return Signal(
            status=Availability.AMBIGUOUS, reason="embedding_revision_or_dimension_mismatch"
        )
    means = []
    for group in groups:
        documents: dict[str, dict[str, Embedding]] = {}
        for e in group:
            chunks = documents.setdefault(e.document_id, {})
            if e.chunk_id in chunks and chunks[e.chunk_id] != e:
                return Signal(status=Availability.AMBIGUOUS)
            chunks[e.chunk_id] = e
        doc_means = [
            [
                sum(e.vector[i] for e in chunks.values()) / len(chunks)
                for i in range(len(group[0].vector))
            ]
            for chunks in documents.values()
        ]
        mean = [sum(d[i] for d in doc_means) / len(doc_means) for i in range(len(group[0].vector))]
        norm = math.sqrt(sum(v * v for v in mean))
        if norm == 0:
            return Signal(status=Availability.AMBIGUOUS, reason="zero_vector")
        means.append([v / norm for v in mean])
    cosine = sum(a * b for a, b in zip(*means, strict=True))
    return Signal(
        status=Availability.OBSERVED,
        value=(max(-1, min(1, cosine)) + 1) / 2,
        evidence=tuple(sorted({e.evidence for e in all_vectors})),
    )


class FinancialValue(Contract):
    company_id: UUID
    metric: NonEmpty
    effective_at: AwareDatetime
    available_at: AwareDatetime
    revision: int = Field(ge=1)
    value: Decimal = Field(allow_inf_nan=False)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    evidence: NonEmpty


def financial_ratio(
    numerator: list[FinancialValue], denominator: list[FinancialValue], cutoff: datetime
) -> Signal:
    cutoff = utc(cutoff)
    selected = []
    for values in (numerator, denominator):
        known = [v for v in values if v.available_at <= cutoff and v.effective_at <= cutoff]
        if not known:
            return Signal(status=Availability.MISSING_SOURCE)
        if len({(v.company_id, v.metric) for v in known}) != 1:
            return Signal(status=Availability.AMBIGUOUS)
        latest = max((v.effective_at, v.revision) for v in known)
        matches = [v for v in known if (v.effective_at, v.revision) == latest]
        if any(v != matches[0] for v in matches):
            return Signal(status=Availability.AMBIGUOUS)
        selected.append(matches[0])
    a, b = selected
    if a.currency != b.currency or b.value <= 0 or a.value < 0:
        return Signal(status=Availability.AMBIGUOUS, reason="invalid_currency_or_denominator")
    value = float(a.value / b.value)
    if not math.isfinite(value):
        return Signal(status=Availability.AMBIGUOUS)
    return Signal(status=Availability.OBSERVED, value=value, evidence=(a.evidence, b.evidence))
