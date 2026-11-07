"""Durable budgets, run readiness, immutable releases and global kill switch."""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      CREATE TABLE budget_accounts (
        source_id text NOT NULL, budget_day date NOT NULL,
        request_limit bigint NOT NULL CHECK(request_limit>=0), cost_limit bigint NOT NULL
        CHECK(cost_limit>=0),
        requests bigint NOT NULL DEFAULT 0 CHECK(requests>=0),
        cost bigint NOT NULL DEFAULT 0 CHECK(cost>=0), PRIMARY KEY(source_id,budget_day)
      );
      CREATE TABLE scheduled_runs (
        run_id uuid PRIMARY KEY, session_date date NOT NULL, config_hash text NOT NULL,
        status text NOT NULL CHECK(status IN ('running','complete','failed')),
        synthetic boolean NOT NULL, readiness jsonb, started_at timestamptz NOT NULL DEFAULT
        clock_timestamp(),
        completed_at timestamptz, UNIQUE(session_date,config_hash)
      );
      CREATE TABLE releases (
        release_hash text PRIMARY KEY, manifest jsonb NOT NULL,
        model_hash text NOT NULL, dataset_hash text NOT NULL REFERENCES research_evaluations,
        recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
      );
      CREATE TABLE runtime_control (
        singleton boolean PRIMARY KEY CHECK(singleton), killed boolean NOT NULL DEFAULT true,
        active_release text REFERENCES releases
      );
      INSERT INTO runtime_control(singleton,killed) VALUES(true,true);
      CREATE TABLE runtime_events (
        event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        actor text NOT NULL, action text NOT NULL, release_hash text,
        recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
      );
    """)
    for table in ("releases", "runtime_events"):
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
        "DROP TABLE runtime_events; DROP TABLE runtime_control; DROP TABLE releases; "
        "DROP TABLE scheduled_runs; DROP TABLE budget_accounts"
    )
