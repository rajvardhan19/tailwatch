"""Immutable resumable pipeline checkpoints and attempt events."""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      CREATE TABLE pipeline_checkpoints (
        run_id uuid NOT NULL REFERENCES scheduled_runs, stage text NOT NULL,
        ordinal integer NOT NULL CHECK(ordinal>=0), artifact_hash text NOT NULL,
        recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY(run_id,stage), UNIQUE(run_id,ordinal)
      );
      CREATE TABLE pipeline_events (
        event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        run_id uuid NOT NULL REFERENCES scheduled_runs, kind text NOT NULL,
        detail jsonb NOT NULL, recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
      );
    """)
    for table in ("pipeline_checkpoints", "pipeline_events"):
        op.execute(
            f"CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION reject_mutation()"
        )


def downgrade() -> None:
    op.execute("DROP TABLE pipeline_events; DROP TABLE pipeline_checkpoints")
