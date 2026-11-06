from datetime import UTC, datetime, timedelta, timezone

import pytest

from tailwatch.domain.time import utc


def test_timezone_required_and_normalized() -> None:
    with pytest.raises(ValueError):
        utc(datetime(2026, 1, 1))
    assert utc(datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=-5)))) == datetime(
        2026,
        1,
        1,
        5,
        tzinfo=UTC,
    )
