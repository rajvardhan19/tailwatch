"""Evidence-constrained drafts. Source text cannot authorize actions."""

from typing import Literal

from pydantic import Field

from tailwatch.sources.policy import Contract, NonEmpty


class EvidenceClaim(Contract):
    evidence_id: NonEmpty
    quote: NonEmpty
    external_display: bool = False


class Draft(Contract):
    claims: tuple[EvidenceClaim, ...] = Field(min_length=1)
    risk: Literal["unknown", "review_required"] = "review_required"
    model_revision: NonEmpty
    prompt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    cost_microusd: int = Field(ge=0, strict=True)


def validate_draft(
    draft: Draft, evidence: dict[str, str], permitted_external: set[str]
) -> dict[str, object]:
    for claim in draft.claims:
        if claim.evidence_id not in evidence or claim.quote not in evidence[claim.evidence_id]:
            raise ValueError("unsupported claim")
        if claim.external_display and claim.evidence_id not in permitted_external:
            raise ValueError("evidence display not permitted")
    # No text-to-tool execution, no approval token, no unrestricted prose/number
    # generation. Quotes remain attributed untrusted source content in the UI.
    return {
        "claims": [c.model_dump() for c in draft.claims],
        "approval": "human_required",
        "model_revision": draft.model_revision,
        "prompt_digest": draft.prompt_digest,
        "cost_microusd": draft.cost_microusd,
    }
