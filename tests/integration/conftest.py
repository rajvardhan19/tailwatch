import os
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine


@pytest.fixture(scope="session")
def database() -> Iterator[Engine]:
    url = os.environ["TAILWATCH_TEST_DATABASE_URL"]
    engine = create_engine(url)
    if not (engine.url.database or "").endswith("_test"):
        raise ValueError("integration database name must end with _test")
    with patch.dict(os.environ, {"TAILWATCH_DATABASE_URL": url}):
        command.upgrade(Config("alembic.ini"), "head")
    yield engine
    engine.dispose()


@pytest.fixture
def connection(database: Engine) -> Iterator[Connection]:
    with database.connect() as conn:
        transaction = conn.begin()
        try:
            yield conn
        finally:
            transaction.rollback()
