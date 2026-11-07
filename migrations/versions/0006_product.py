"""Tenant-scoped append-only alert episodes, keys and subscriptions."""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      CREATE TABLE tenants (tenant_id uuid PRIMARY KEY, name text NOT NULL);
      CREATE TABLE api_keys (
        key_hash text PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants,
        scope text NOT NULL CHECK(scope IN ('customer','analyst')), revoked boolean NOT NULL
        DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      );
      CREATE TABLE alert_episodes (
        episode_id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants,
        visitor uuid NOT NULL, host uuid NOT NULL, pair_key text NOT NULL,
        model_hash text NOT NULL, feature_run_hash text NOT NULL, synthetic boolean NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(), CHECK(visitor<>host)
      );
      CREATE INDEX alert_pair_lookup ON alert_episodes(tenant_id,pair_key,created_at);
      CREATE TABLE alert_events (
        episode_id uuid NOT NULL REFERENCES alert_episodes, sequence integer NOT NULL
        CHECK(sequence>0),
        kind text NOT NULL CHECK(kind IN
        ('draft','review','approved','vetoed','superseded','outcome')),
        actor text NOT NULL, body jsonb NOT NULL, previous_hash text NOT NULL, event_hash text
        NOT NULL,
        recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(episode_id,sequence)
      );
      CREATE TABLE watchlist (
        tenant_id uuid NOT NULL REFERENCES tenants, company_id uuid NOT NULL REFERENCES entities,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(tenant_id,company_id)
      );
      CREATE TABLE subscriptions (
        subscription_id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants,
        email text NOT NULL, verification_hash text NOT NULL, verified boolean NOT NULL DEFAULT
        false,
        active boolean NOT NULL DEFAULT true, unsubscribe_hash text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      );
      CREATE TABLE notification_outbox (
        delivery_id uuid PRIMARY KEY, episode_id uuid NOT NULL REFERENCES alert_episodes,
        approved_sequence integer NOT NULL, subscription_id uuid NOT NULL REFERENCES subscriptions,
        state text NOT NULL CHECK(state IN ('pending','sending','sent','cancelled','uncertain')),
        provider_id text, updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(episode_id,approved_sequence,subscription_id),
        FOREIGN KEY(episode_id,approved_sequence) REFERENCES alert_events
      );
    """)
    for table in ("alert_episodes", "alert_events"):
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
        "DROP TABLE notification_outbox; DROP TABLE subscriptions; DROP TABLE watchlist; "
        "DROP TABLE alert_events; DROP TABLE alert_episodes; "
        "DROP TABLE api_keys; DROP TABLE tenants"
    )
