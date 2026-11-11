"""Recipient verification outbox, idempotent retry bookkeeping and delivery audit."""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      ALTER TABLE notification_outbox
        ADD COLUMN first_attempt_at timestamptz,
        ADD COLUMN payload_digest text CHECK(payload_digest ~ '^[0-9a-f]{64}$');
      CREATE INDEX outbox_state ON notification_outbox(state,updated_at);
      CREATE TABLE verification_messages (
        message_id uuid PRIMARY KEY,
        subscription_id uuid NOT NULL REFERENCES subscriptions,
        state text NOT NULL CHECK(state IN ('pending','sending','sent','cancelled','uncertain')),
        provider_id text, payload_digest text CHECK(payload_digest ~ '^[0-9a-f]{64}$'),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        first_attempt_at timestamptz, consumed_at timestamptz,
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
      );
      CREATE INDEX verification_state ON verification_messages(state,updated_at);
      CREATE INDEX verification_subscription ON verification_messages(subscription_id,created_at);
      CREATE TABLE delivery_events (
        event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        delivery_id uuid REFERENCES notification_outbox,
        message_id uuid REFERENCES verification_messages,
        actor text NOT NULL CHECK(length(actor)>0), action text NOT NULL,
        detail jsonb NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(detail)='object'),
        recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CHECK((delivery_id IS NULL) <> (message_id IS NULL))
      );
    """)
    op.execute(
        "CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON delivery_events "
        "FOR EACH ROW EXECUTE FUNCTION reject_mutation()"
    )
    op.execute(
        "CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON delivery_events "
        "FOR EACH STATEMENT EXECUTE FUNCTION reject_mutation()"
    )


def downgrade() -> None:
    op.execute("""
      DROP TABLE delivery_events; DROP TABLE verification_messages;
      DROP INDEX outbox_state;
      ALTER TABLE notification_outbox DROP COLUMN payload_digest, DROP COLUMN first_attempt_at;
    """)
