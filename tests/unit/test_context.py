import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from tailwatch.context.events import (
    ContextEvent,
    Coverage,
    TradingCalendar,
    eligible_exposure,
)

COMPANY = uuid4()
START = datetime(2026, 3, 6, tzinfo=UTC)
END = datetime(2026, 3, 10, tzinfo=UTC)


def calendar():
    return TradingCalendar.model_validate(
        {
            "exchange": "synthetic",
            "timezone": "America/New_York",
            "version": "fixture-v1",
            "evidence_id": "synthetic-calendar",
            "available_at": START,
            "start": START,
            "end": END,
            "sessions": [
                {
                    "session_date": "2026-03-06",
                    "opens_at": "2026-03-06T14:30:00Z",
                    "closes_at": "2026-03-06T21:00:00Z",
                },
                {
                    "session_date": "2026-03-09",
                    "opens_at": "2026-03-09T13:30:00Z",
                    "closes_at": "2026-03-09T20:00:00Z",
                },
            ],
        }
    )


def coverage():
    return Coverage(
        company_id=COMPANY,
        kind="blackout",
        start=START,
        end=END,
        available_at=START,
        complete=True,
        basis="confirmed",
        evidence_id="synthetic-policy",
    )


def event(kind="blackout", certainty="confirmed"):
    return ContextEvent(
        event_id=uuid4(),
        kind=kind,
        company_id=COMPANY,
        start="2026-03-09T00:00:00-04:00",
        end="2026-03-10T00:00:00-04:00",
        available_at=START,
        certainty=certainty,
        source_digest=hashlib.sha256(b"Synthetic blackout").hexdigest(),
        evidence_text="Synthetic blackout",
        timezone="America/New_York",
    )


def expose(events, policy=None, **kwargs):
    return eligible_exposure(
        company_id=COMPANY,
        start=START,
        end=END,
        as_of=END,
        calendar=calendar(),
        coverage=policy,
        events=events,
        **kwargs,
    )


def test_missing_policy_is_not_zero_blackout_and_earnings_does_not_invent_one():
    assert expose([]).status == "missing_source"
    result = expose([event(kind="earnings")], coverage())
    assert len(result.eligible_dates) == 2 and result.excluded_dates == ()


def test_dst_sessions_and_explicit_blackout_exposure():
    result = expose([event()], coverage())
    assert [str(d) for d in result.eligible_dates] == ["2026-03-06"]
    assert [str(d) for d in result.excluded_dates] == ["2026-03-09"]
    assert result.status == "observed"


def test_proxy_requires_opt_in_and_retains_research_label():
    assert expose([event(certainty="proxy")], coverage()).status == "ambiguous"
    assert (
        expose([event(certainty="proxy")], coverage(), allow_proxy=True).status == "research_proxy"
    )
    assert (
        expose([event(certainty="tentative")], coverage(), allow_proxy=True).status == "ambiguous"
    )


def test_future_knowledge_and_wrong_issuer_cannot_fill_coverage():
    future = Coverage.model_validate(
        coverage().model_dump() | {"available_at": "2026-03-11T00:00:00Z"}
    )
    assert expose([], future).status == "missing_source"
    wrong = Coverage.model_validate(coverage().model_dump() | {"company_id": uuid4()})
    assert expose([], wrong).status == "missing_source"


def test_unknown_calendar_range_and_overlapping_sessions_fail():
    result = eligible_exposure(
        company_id=COMPANY,
        start=START,
        end=datetime(2026, 3, 11, tzinfo=UTC),
        as_of=datetime(2026, 3, 12, tzinfo=UTC),
        calendar=calendar(),
        coverage=coverage(),
        events=[],
    )
    assert result.status == "insufficient_history"
    broken = calendar().model_dump()
    broken["sessions"] = [broken["sessions"][0]] * 2
    with pytest.raises(ValidationError):
        TradingCalendar.model_validate(broken)
