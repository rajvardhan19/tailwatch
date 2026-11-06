import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tailwatch.entities.aircraft_control import (
    CompanyCandidate,
    ControlReview,
    KnownReview,
    LinkEvidence,
    candidates,
    resolve_control,
)
from tailwatch.sources.registry import parse_master

START = datetime(2026, 1, 1, tzinfo=UTC)
LATER = START + timedelta(days=1)
TEXT = b"Synthetic Company controls synthetic airframe for the stated interval."
RAW = (
    b"N-NUMBER,SERIAL NUMBER,MFR MDL CODE,TYPE REGISTRANT,NAME,STATUS CODE,"
    b"FRACT OWNER,MODE S CODE HEX\n123AB,SYN-1,1234567,3,SYNTHETIC COMPANY,V,,ABC123\n"
)
ARCHIVE = parse_master(RAW, source_id="fixture", observed_at=START, available_at=START)
COMPANY, AIRFRAME = uuid4(), uuid4()


def evidence(**changes):
    return LinkEvidence.model_validate(
        dict(
            start=START,
            digest=hashlib.sha256(TEXT).hexdigest(),
            locator="paragraph 1",
            quote=TEXT.decode(),
            available_at=START,
            kind="controller_document",
        )
        | changes
    )


def review(**changes):
    return ControlReview.model_validate(
        dict(
            start=START,
            airframe_id=AIRFRAME,
            company_id=COMPANY,
            role="controller",
            decision="approved",
            registry_digest=ARCHIVE.digest,
            registry_row=2,
            airframe_candidate_key="faa:1234567:SYN-1",
            reviewed_at=LATER,
            reviewer="synthetic-reviewer",
            rationale="Synthetic direct document checked",
            evidence=[evidence()],
            privacy="cleared",
        )
        | changes
    )


def resolve(items, at=LATER, cutoff=LATER, role="controller", archive=ARCHIVE):
    return resolve_control(
        items,
        archive,
        airframe_id=AIRFRAME,
        role=role,
        cutoff=cutoff,
        effective_at=at,
        max_registry_age=timedelta(days=2),
    )


def known(r=None, revision=1):
    return KnownReview(review=r or review(), known_at=LATER, revision=revision)


def test_future_approval_cannot_enter_past_replay_and_roles_are_separate():
    assert resolve([known()], at=START, cutoff=START).state == "unresolved"
    assert resolve([known()]).company_id == COMPANY
    assert resolve([known()], role="operator").state == "unresolved"
    assert (
        resolve([known()], at=LATER + timedelta(days=1), cutoff=LATER + timedelta(days=1)).state
        == "unsupported"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"privacy": "withheld"},
        {"privacy": "unknown"},
        {"evidence": [evidence(kind="name")]},
        {"evidence": [evidence(kind="home_base")]},
        {"evidence": [evidence(shared=True)]},
        {"evidence": [evidence(intermediary=True)]},
        {"evidence": [evidence(available_at=LATER + timedelta(seconds=1))]},
        {"evidence": [evidence(end=LATER)]},
    ],
)
def test_approval_requires_direct_nonshared_role_evidence_and_privacy_clearance(changes):
    with pytest.raises(ValueError):
        review(**changes)


def test_rejection_and_corrected_interval_do_not_resurrect_old_approval():
    rejected = known(review(decision="rejected"), revision=2)
    assert resolve([known(), rejected]).state == "unresolved"
    corrected = known(review(start=LATER, end=LATER + timedelta(hours=1)), revision=2)
    assert resolve([known(), corrected], at=START).state == "unresolved"
    assert (
        resolve(
            [known(), corrected], at=LATER + timedelta(hours=1), cutoff=LATER + timedelta(hours=1)
        ).state
        == "unresolved"
    )


def test_conflicting_companies_and_changed_registry_fail_closed():
    assert resolve([known(), known(review(company_id=uuid4()))]).state == "ambiguous"
    assert resolve([known(), known(review(rationale="different"))]).state == "ambiguous"
    changed = parse_master(
        RAW.replace(b"SYN-1", b"SYN-2"), source_id="fixture", observed_at=START, available_at=START
    )
    assert resolve([known()], archive=changed).state == "unresolved"


def test_candidates_never_approve_and_exclude_shared_intermediaries_and_future_names():
    companies = [
        CompanyCandidate(
            company_id=COMPANY, name="Synthetic Company", start=START, available_at=START
        ),
        CompanyCandidate(
            company_id=uuid4(),
            name="Other",
            start=START,
            available_at=START,
            evidence=[
                evidence(kind="address", intermediary=True),
                evidence(kind="home_base", shared=True),
            ],
        ),
        CompanyCandidate(
            company_id=uuid4(), name="Synthetic Company", start=START, available_at=LATER
        ),
    ]
    result = candidates(
        ARCHIVE, 2, companies, cutoff=START, effective_at=START, max_registry_age=timedelta(days=2)
    )
    assert len(result) == 1
    assert result[0].company_id == COMPANY
    assert result[0].status == "possible"
    assert result[0].reasons == ("registry_name_match",)


def test_supported_home_base_evidence_keeps_alternative_candidates():
    companies = [
        CompanyCandidate(
            company_id=uuid4(),
            name="Other",
            start=START,
            available_at=START,
            evidence=[evidence(kind="home_base")],
        )
        for _ in range(2)
    ]
    result = candidates(
        ARCHIVE, 2, companies, cutoff=START, effective_at=START, max_registry_age=timedelta(days=2)
    )
    assert len(result) == 2
    assert all(c.status == "possible" for c in result)
