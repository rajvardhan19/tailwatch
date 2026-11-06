from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from tailwatch.entities.history import HISTORY, OfficeHistory, SecurityHistory
from tailwatch.entities.repository import read_history_fixture


def test_renaming_and_delisting_have_half_open_boundaries() -> None:
    history = read_history_fixture(Path("tests/fixtures/entities/company.json"))
    assert history.at(datetime(2025, 1, 1, tzinfo=UTC)) is None
    assert history.at(datetime(2026, 1, 9, tzinfo=UTC)).status == "public"
    assert history.at(datetime(2026, 1, 10, tzinfo=UTC)).status == "delisted"
    overlapping = history.model_dump()
    overlapping["periods"][0]["end"] = "2026-01-11T00:00:00Z"
    with pytest.raises(ValidationError, match="non-overlapping"):
        HISTORY.validate_python(overlapping)


def test_currency_and_geographic_coordinates_are_required() -> None:
    with pytest.raises(ValidationError, match="currency"):
        SecurityHistory(
            entity_id=uuid4(),
            company_id=uuid4(),
            periods=[
                {
                    "start": "2026-01-01T00:00:00Z",
                    "ticker": "SYN",
                    "exchange": "XNAS",
                    "active": True,
                }
            ],
        )
    with pytest.raises(ValidationError):
        OfficeHistory(
            entity_id=uuid4(),
            company_id=uuid4(),
            periods=[
                {
                    "start": "2026-01-01T00:00:00Z",
                    "kind": "hq",
                    "latitude": 200,
                    "longitude": 0,
                    "address": "Synthetic address",
                }
            ],
        )
