import os

import psycopg
import pytest

pytestmark = pytest.mark.integration


def test_postgis_and_database_constraints(database) -> None:
    # Explicit integration runs must fail, not silently skip, without a database.
    url = os.environ["TAILWATCH_TEST_DATABASE_URL"].replace("postgresql+psycopg:", "postgresql:")
    with psycopg.connect(url) as connection:
        row = connection.execute("SELECT PostGIS_Version()").fetchone()
        assert row and row[0]
        connection.execute("CREATE TEMP TABLE contract_probe (value integer CHECK (value >= 0))")
        with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
            connection.execute("INSERT INTO contract_probe VALUES (-1)")
