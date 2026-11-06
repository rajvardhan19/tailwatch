"""Durable index worklists, observed downloads, and append-only attempt history."""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      CREATE TABLE sec_runs (
        run_id uuid PRIMARY KEY,
        source_id text NOT NULL REFERENCES sources,
        index_url text NOT NULL,
        index_digest text NOT NULL REFERENCES raw_objects,
        manifest jsonb NOT NULL CHECK (jsonb_typeof(manifest)='array'),
        selection jsonb NOT NULL CHECK (jsonb_typeof(selection)='object'),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      );
      CREATE TABLE sec_fetches (
        fetch_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        url text NOT NULL,
        status integer NOT NULL CHECK (status BETWEEN 100 AND 599),
        digest text NOT NULL REFERENCES raw_objects,
        requested_at timestamptz NOT NULL,
        received_at timestamptz NOT NULL CHECK (received_at >= requested_at)
      );
      CREATE TABLE sec_downloads (
        run_id uuid NOT NULL REFERENCES sec_runs,
        accession text NOT NULL CHECK (accession ~ '^\\d{10}-\\d{2}-\\d{6}$'),
        digest text NOT NULL REFERENCES raw_objects,
        accepted_at timestamptz NOT NULL,
        first_seen_at timestamptz NOT NULL,
        PRIMARY KEY (run_id, accession)
      );
      CREATE TABLE sec_attempts (
        attempt_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        run_id uuid NOT NULL REFERENCES sec_runs,
        accession text NOT NULL,
        outcome text NOT NULL CHECK (outcome IN ('downloaded','failed','quarantined')),
        detail text NOT NULL,
        recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
      );
    """)
    for table in ("sec_runs", "sec_fetches", "sec_downloads", "sec_attempts"):
        op.execute(
            f"CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION reject_mutation()"
        )


def downgrade() -> None:
    op.execute(
        "DROP TABLE sec_attempts; DROP TABLE sec_downloads; DROP TABLE sec_fetches; "
        "DROP TABLE sec_runs"
    )
