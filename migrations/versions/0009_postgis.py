"""Initialize PostGIS on separately created databases, not only image defaults."""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")


def downgrade() -> None:
    # Extensions can predate Tailwatch or be shared with other schemas. Preserve it.
    pass
