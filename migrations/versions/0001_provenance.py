"""Immutable provenance and atomic source publication.

SQL is intentionally self-contained; historical migrations never import live models.
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE sources (
      source_id text PRIMARY KEY CHECK (source_id ~ '^[a-z][a-z0-9_-]*$')
    );
    CREATE TABLE raw_objects (
      digest text PRIMARY KEY CHECK (digest ~ '^[0-9a-f]{64}$'),
      byte_length bigint NOT NULL CHECK (byte_length >= 0)
    );
    CREATE TABLE source_snapshots (
      snapshot_id uuid PRIMARY KEY,
      source_id text NOT NULL REFERENCES sources,
      created_at timestamptz NOT NULL,
      watermark timestamptz NOT NULL,
      expected_records integer NOT NULL CHECK (expected_records >= 0),
      manifest_hash text NOT NULL CHECK (manifest_hash ~ '^[0-9a-f]{64}$'),
      UNIQUE (snapshot_id, source_id),
      CHECK (watermark <= created_at)
    );
    CREATE TABLE evidence_facts (
      fact_id uuid PRIMARY KEY,
      source_id text NOT NULL REFERENCES sources,
      snapshot_id uuid NOT NULL,
      logical_key text NOT NULL CHECK (length(logical_key) > 0),
      revision integer NOT NULL CHECK (revision > 0),
      supersedes uuid UNIQUE REFERENCES evidence_facts(fact_id),
      digest text NOT NULL REFERENCES raw_objects,
      evidence_locator text NOT NULL CHECK (length(evidence_locator) > 0),
      parser_version text NOT NULL CHECK (length(parser_version) > 0),
      occurred_at timestamptz,
      source_published_at timestamptz,
      ingested_at timestamptz NOT NULL,
      processed_at timestamptz NOT NULL,
      approved_at timestamptz,
      valid_from timestamptz NOT NULL,
      valid_to timestamptz,
      payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
      FOREIGN KEY (snapshot_id, source_id)
        REFERENCES source_snapshots(snapshot_id, source_id),
      UNIQUE (source_id, logical_key, revision),
      UNIQUE (snapshot_id, logical_key),
      CHECK (valid_to IS NULL OR valid_to > valid_from),
      CHECK (processed_at >= ingested_at),
      CHECK (source_published_at IS NULL OR source_published_at <= ingested_at),
      CHECK (approved_at IS NULL OR approved_at >= processed_at),
      CHECK ((revision = 1 AND supersedes IS NULL)
          OR (revision > 1 AND supersedes IS NOT NULL))
    );
    CREATE TABLE parse_failures (
      snapshot_id uuid NOT NULL REFERENCES source_snapshots,
      source_key text NOT NULL,
      reason text NOT NULL CHECK (length(reason) > 0),
      PRIMARY KEY (snapshot_id, source_key)
    );
    CREATE TABLE snapshot_publications (
      snapshot_id uuid PRIMARY KEY REFERENCES source_snapshots,
      published_at timestamptz NOT NULL,
      recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
    );
    CREATE INDEX fact_replay ON evidence_facts(source_id, logical_key, revision DESC);

    CREATE FUNCTION reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      RAISE EXCEPTION 'append-only table %', TG_TABLE_NAME;
    END $$;

    CREATE FUNCTION guard_snapshot_write() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE previous evidence_facts; snapshot source_snapshots;
    BEGIN
      SELECT * INTO snapshot FROM source_snapshots
        WHERE snapshot_id = NEW.snapshot_id FOR UPDATE;
      IF EXISTS (SELECT 1 FROM snapshot_publications WHERE snapshot_id = NEW.snapshot_id) THEN
        RAISE EXCEPTION 'snapshot already published';
      END IF;
      IF TG_TABLE_NAME = 'evidence_facts' THEN
        IF NEW.ingested_at < snapshot.created_at THEN
          RAISE EXCEPTION 'ingestion predates snapshot creation';
        END IF;
        IF NEW.supersedes IS NOT NULL THEN
          SELECT * INTO previous FROM evidence_facts WHERE fact_id = NEW.supersedes;
          IF previous.source_id <> NEW.source_id OR previous.logical_key <> NEW.logical_key
             OR previous.revision + 1 <> NEW.revision
             OR NEW.processed_at < greatest(previous.processed_at, previous.approved_at)
             OR (previous.source_published_at IS NOT NULL AND
                 NEW.source_published_at < previous.source_published_at) THEN
            RAISE EXCEPTION 'invalid revision chain';
          END IF;
        END IF;
      END IF;
      RETURN NEW;
    END $$;

    CREATE FUNCTION guard_publication() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE snapshot source_snapshots; actual_count integer;
    BEGIN
      -- Imported/backdated source timestamps must never backdate system knowledge.
      NEW.recorded_at := clock_timestamp();
      SELECT * INTO snapshot FROM source_snapshots
        WHERE snapshot_id = NEW.snapshot_id FOR UPDATE;
      SELECT count(*) INTO actual_count FROM evidence_facts
        WHERE snapshot_id = NEW.snapshot_id;
      IF actual_count <> snapshot.expected_records OR EXISTS (
        SELECT 1 FROM parse_failures WHERE snapshot_id = NEW.snapshot_id
      ) THEN RAISE EXCEPTION 'incomplete or failed snapshot'; END IF;
      IF NEW.published_at < snapshot.created_at OR EXISTS (
        SELECT 1 FROM evidence_facts WHERE snapshot_id = NEW.snapshot_id
        AND greatest(processed_at, approved_at) > NEW.published_at
      ) THEN RAISE EXCEPTION 'publication predates available evidence'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER facts_staging BEFORE INSERT ON evidence_facts
      FOR EACH ROW EXECUTE FUNCTION guard_snapshot_write();
    CREATE TRIGGER failures_staging BEFORE INSERT ON parse_failures
      FOR EACH ROW EXECUTE FUNCTION guard_snapshot_write();
    CREATE TRIGGER publish_complete BEFORE INSERT ON snapshot_publications
      FOR EACH ROW EXECUTE FUNCTION guard_publication();
    """)
    for table in (
        "sources",
        "raw_objects",
        "source_snapshots",
        "evidence_facts",
        "parse_failures",
        "snapshot_publications",
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
    for table in (
        "snapshot_publications",
        "parse_failures",
        "evidence_facts",
        "source_snapshots",
        "raw_objects",
        "sources",
    ):
        op.execute(f"DROP TABLE {table} CASCADE")
    for function in ("guard_publication", "guard_snapshot_write", "reject_mutation"):
        op.execute(f"DROP FUNCTION {function}()")
