"""Use the recording database's clock for immediate operational replay cutoffs."""

from datetime import UTC, datetime

from sqlalchemy import Connection, text


def database_now(connection: Connection) -> datetime:
    value: datetime = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
    return value.astimezone(UTC)
