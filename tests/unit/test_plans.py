import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from tailwatch.sources.sec.plans import (
    PlanDisclosure,
    disclosure_text,
    plan_candidates,
    termination_signal,
    validate_disclosure,
)

RAW = (
    b"<html><script>terminated trading plan</script>"
    b"<p>Officer adopted a 10b5-1 trading plan.</p></html>"
)
NOW = datetime(2026, 5, 1, tzinfo=UTC)


def disclosure(status="events", event="terminated"):
    text = disclosure_text(RAW)
    passage = {"start": 0, "end": len(text), "text": text}
    return PlanDisclosure.model_validate(
        {
            "company_id": uuid4(),
            "accession": "0000000001-26-000001",
            "source_digest": hashlib.sha256(RAW).hexdigest(),
            "form": "10-Q",
            "quarter_start": "2026-01-01",
            "quarter_end": "2026-03-31",
            "available_at": NOW,
            "reviewed_at": NOW,
            "reviewer": "fixture-reviewer",
            "status": status,
            "status_evidence": passage,
            "events": [
                {
                    "event_id": "event-1",
                    "plan_id": "plan-1",
                    "insider_id": uuid4(),
                    "role": "officer",
                    "event_date": "2026-02-01",
                    "event": event,
                    "arrangement": "10b5_1",
                    "evidence": passage,
                }
            ]
            if status == "events"
            else [],
        }
    )


def test_candidates_are_hints_and_ignore_script_content():
    assert plan_candidates(RAW)[0].hint == "adopted"
    negative = b"<p>No director adopted or terminated any 10b5-1 trading plan.</p>"
    assert plan_candidates(negative)[0].hint == "unknown"
    assert plan_candidates(b"<p>Unrelated filing text.</p>") == []
    modified = b"<p>An officer terminated and replaced a 10b5-1 plan.</p>"
    assert plan_candidates(modified)[0].hint == "unknown"


@pytest.mark.parametrize(
    "event,count",
    [
        ("terminated", 1),
        ("expired", 0),
        ("completed", 0),
        ("modified", 0),
        ("adopted", 0),
        ("unknown", None),
    ],
)
def test_event_taxonomy_does_not_count_expiry_as_termination(event, count):
    item = disclosure(event=event)
    assert termination_signal([item], company_id=item.company_id, as_of=NOW).count == count


def test_missing_disclosure_differs_from_explicit_reviewed_zero():
    unknown = disclosure(status="unknown")
    assert (
        termination_signal([], company_id=unknown.company_id, as_of=NOW).status == "missing_source"
    )
    assert termination_signal([unknown], company_id=unknown.company_id, as_of=NOW).count is None
    zero = disclosure(status="no_events")
    assert termination_signal([zero], company_id=zero.company_id, as_of=NOW).count == 0
    assert (
        termination_signal(
            [zero], company_id=zero.company_id, as_of=datetime(2027, 1, 1, tzinfo=UTC)
        ).status
        == "stale"
    )


def test_passage_hash_quarter_and_amendment_validation():
    item = disclosure()
    validate_disclosure(item, RAW)
    with pytest.raises(ValueError, match="raw source"):
        validate_disclosure(item, RAW + b" ")
    with pytest.raises(ValidationError):
        PlanDisclosure.model_validate(item.model_dump() | {"quarter_end": "2026-12-31"})
    with pytest.raises(ValidationError):
        PlanDisclosure.model_validate(item.model_dump() | {"form": "10-Q/A"})
    amended = PlanDisclosure.model_validate(
        item.model_dump()
        | {
            "accession": "0000000001-26-000002",
            "form": "10-Q/A",
            "supersedes_accession": item.accession,
            "events": [],
            "status": "no_events",
            "available_at": "2026-05-02T00:00:00Z",
            "reviewed_at": "2026-05-02T00:00:00Z",
        }
    )
    assert termination_signal([item, amended], company_id=item.company_id, as_of=NOW).count == 1
    assert (
        termination_signal(
            [item, amended], company_id=item.company_id, as_of=datetime(2026, 5, 3, tzinfo=UTC)
        ).count
        == 0
    )
