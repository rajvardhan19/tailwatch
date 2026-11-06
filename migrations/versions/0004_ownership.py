"""Versioned extracted ownership documents and exact transaction row identities."""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      CREATE TABLE ownership_documents (
        document_id text PRIMARY KEY,
        accession text NOT NULL,
        source_digest text NOT NULL REFERENCES raw_objects,
        parser_version text NOT NULL,
        first_seen_at timestamptz NOT NULL,
        recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        parsed jsonb NOT NULL CHECK (jsonb_typeof(parsed)='object'),
        UNIQUE (accession,source_digest,parser_version)
      );
      CREATE TABLE ownership_rows (
        document_id text NOT NULL REFERENCES ownership_documents,
        row_id text NOT NULL,
        payload jsonb NOT NULL CHECK (jsonb_typeof(payload)='object'),
        PRIMARY KEY(document_id,row_id)
      );
      CREATE TABLE ownership_parse_attempts (
        attempt_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        run_id uuid NOT NULL REFERENCES sec_runs,
        accession text NOT NULL,
        parser_version text NOT NULL,
        outcome text NOT NULL CHECK (outcome IN ('parsed','quarantined')),
        detail text NOT NULL,
        recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
      );
    """)
    for table in ("ownership_documents", "ownership_rows", "ownership_parse_attempts"):
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
        "DROP TABLE ownership_parse_attempts; DROP TABLE ownership_rows; "
        "DROP TABLE ownership_documents"
    )
