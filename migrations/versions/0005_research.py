"""Immutable experiment registration and single-use sealed-test access."""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      CREATE TABLE research_experiments (
        experiment_hash text PRIMARY KEY, manifest jsonb NOT NULL,
        registered_at timestamptz NOT NULL DEFAULT clock_timestamp()
      );
      CREATE TABLE research_seals (
        experiment_hash text PRIMARY KEY REFERENCES research_experiments,
        dataset_hash text NOT NULL UNIQUE, model_hash text NOT NULL,
        threshold double precision NOT NULL CHECK(threshold>=0 AND threshold<=1),
        baseline_precision double precision NOT NULL, eligible_deals integer NOT NULL,
        sealed_at timestamptz NOT NULL DEFAULT clock_timestamp()
      );
      CREATE TABLE sealed_test_access (
        dataset_hash text PRIMARY KEY,
        experiment_hash text NOT NULL REFERENCES research_experiments,
        model_hash text NOT NULL, accessed_at timestamptz NOT NULL DEFAULT clock_timestamp()
      );
      CREATE TABLE research_evaluations (
        dataset_hash text PRIMARY KEY REFERENCES sealed_test_access,
        result jsonb NOT NULL, recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
      );
    """)
    for table in (
        "research_experiments",
        "research_seals",
        "sealed_test_access",
        "research_evaluations",
    ):
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
        "DROP TABLE research_evaluations; DROP TABLE sealed_test_access; "
        "DROP TABLE research_seals; DROP TABLE research_experiments"
    )
