from datetime import UTC, datetime, timedelta

import pytest

from tailwatch.sources.registry import lookup, parse_master

START = datetime(2026, 1, 1, tzinfo=UTC)
HEADER = (
    "N-NUMBER,SERIAL NUMBER,MFR MDL CODE,TYPE REGISTRANT,NAME,"
    "STATUS CODE,FRACT OWNER,MODE S CODE HEX\n"
)
ROW = "123AB,SYNTHETIC-1,1234567,3,SYNTHETIC CORP,V,,ABC123\n"


def archive(rows=ROW, at=START, available=None):
    return parse_master(
        (HEADER + rows).encode(), source_id="fixture", observed_at=at, available_at=available or at
    )


def resolve(archives, at=START, cutoff=None, alias="N123AB", kind="tail"):
    return lookup(
        archives,
        source_id="fixture",
        alias=alias,
        alias_kind=kind,
        effective_at=at,
        cutoff=cutoff or at,
        max_age=timedelta(days=2),
    )


def test_reassignment_is_not_backdated_and_sparse_history_stays_unknown():
    old = archive()
    later = START + timedelta(days=365)
    new = archive(ROW.replace("SYNTHETIC-1", "SYNTHETIC-2"), at=later)
    assert resolve([old, new]).registration.serial == "SYNTHETIC-1"
    assert resolve([old, new], at=later).registration.serial == "SYNTHETIC-2"
    assert resolve([new]).state == "missing_source"
    assert resolve([old, new], at=START + timedelta(days=30)).state == "stale"
    assert resolve([old], at=START + timedelta(days=2)).state == "stale"


def test_latest_absence_does_not_resurrect_old_registration():
    later = START + timedelta(days=1)
    assert resolve([archive(), archive("", at=later)], at=later).state == "absent_or_quarantined"
    hidden = archive(at=START, available=later)
    assert resolve([hidden]).state == "missing_source"


@pytest.mark.parametrize(
    "old,new",
    [
        (",3,", ",1,"),
        (",V,,", ",V,Y,"),
        (",V,,", ",V,?,"),
        ("SYNTHETIC CORP", ""),
        (",V,", ",7,"),
        ("SYNTHETIC-1", ""),
        ("ABC123", ""),
        (",3,", ",?,"),
    ],
)
def test_unsupported_registrations_never_become_control_candidates(old, new):
    record = archive(ROW.replace(old, new)).records[0]
    assert not record.eligible_for_control_review


def test_blank_fractional_flag_is_documented_nonfractional_not_missing_column():
    assert archive().records[0].eligible_for_control_review
    with pytest.raises(ValueError, match="columns"):
        parse_master(
            (HEADER.replace("FRACT OWNER,", "") + ROW).encode(),
            source_id="fixture",
            observed_at=START,
            available_at=START,
        )


@pytest.mark.parametrize(
    "second",
    [ROW, ROW.replace("123AB", "124AB"), ROW.replace("123AB", "124AB").replace("ABC123", "ABC124")],
)
def test_duplicate_alias_or_airframe_quarantines_all_implicated_rows(second):
    result = archive(ROW + second)
    assert result.records == ()
    assert len(result.quarantined) == result.row_count == 2


def test_malformed_rows_and_unknown_fields_are_explicit():
    result = archive(ROW + "broken\n" + ROW.replace("123AB", "00000"))
    assert len(result.records) == 1
    assert len(result.quarantined) == 2
    assert result.row_count == 3
    assert archive(ROW.replace("SYNTHETIC CORP", "")).records[0].disclosure == "unknown"


def test_same_timestamp_conflicts_fail_closed_and_input_requires_aware_time():
    assert (
        resolve([archive(), archive(ROW.replace("SYNTHETIC-1", "SYNTHETIC-2"))]).state
        == "ambiguous"
    )
    assert resolve([archive()], alias="abc123", kind="icao24").state == "observed"
    with pytest.raises(ValueError):
        resolve([archive()], at=datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        resolve([archive()], at=START + timedelta(days=1), cutoff=START)


def test_trailing_empty_column_and_quoted_commas():
    raw = (
        HEADER.rstrip()
        + ",\r\n"
        + ROW.replace("SYNTHETIC CORP", '"SYNTHETIC, CORP"').rstrip()
        + ",\r\n"
    )
    result = parse_master(raw.encode(), source_id="fixture", observed_at=START, available_at=START)
    assert result.records[0].registrant_name == "SYNTHETIC, CORP"
