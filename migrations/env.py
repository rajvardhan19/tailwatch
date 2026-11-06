"""Migrations require an explicit PostgreSQL URL, never a fallback database."""

import os

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

url = os.environ["TAILWATCH_DATABASE_URL"]
if not url.startswith("postgresql+psycopg://"):
    raise ValueError("TAILWATCH_DATABASE_URL must use PostgreSQL with psycopg")

if context.is_offline_mode():
    context.configure(url=url, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with create_engine(url, poolclass=NullPool).connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
