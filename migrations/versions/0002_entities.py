"""Stable entity identities and immutable evidence bindings."""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      CREATE TABLE entities (
        entity_id uuid PRIMARY KEY,
        kind text NOT NULL CHECK (kind IN
          ('company','security','office','person','insider_role','relationship')),
        UNIQUE(entity_id, kind)
      );
      CREATE TABLE entity_histories (
        fact_id uuid PRIMARY KEY REFERENCES evidence_facts,
        entity_id uuid NOT NULL,
        kind text NOT NULL,
        company_id uuid,
        company_kind text NOT NULL DEFAULT 'company' CHECK (company_kind = 'company'),
        person_id uuid,
        person_kind text NOT NULL DEFAULT 'person' CHECK (person_kind = 'person'),
        counterparty_id uuid,
        FOREIGN KEY(entity_id, kind) REFERENCES entities(entity_id, kind),
        FOREIGN KEY(company_id, company_kind) REFERENCES entities(entity_id, kind),
        FOREIGN KEY(person_id, person_kind) REFERENCES entities(entity_id, kind),
        FOREIGN KEY(counterparty_id, company_kind) REFERENCES entities(entity_id, kind),
        CHECK ((kind='company' AND company_id IS NULL) OR
               (kind<>'company' AND company_id IS NOT NULL))
      );
      CREATE FUNCTION guard_entity_binding() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE fact evidence_facts;
      BEGIN
        SELECT * INTO STRICT fact FROM evidence_facts WHERE fact_id=NEW.fact_id;
        PERFORM 1 FROM source_snapshots WHERE snapshot_id=fact.snapshot_id FOR UPDATE;
        IF EXISTS (SELECT 1 FROM snapshot_publications WHERE snapshot_id=fact.snapshot_id) THEN
          RAISE EXCEPTION 'cannot bind entity after snapshot publication';
        END IF;
        IF fact.logical_key <> 'entity:' || NEW.entity_id::text
           OR fact.payload->>'kind' IS DISTINCT FROM NEW.kind
           OR fact.payload->>'entity_id' IS DISTINCT FROM NEW.entity_id::text
           OR fact.payload->>'company_id' IS DISTINCT FROM NEW.company_id::text
           OR fact.payload->>'person_id' IS DISTINCT FROM NEW.person_id::text
           OR fact.payload->>'counterparty_id' IS DISTINCT FROM NEW.counterparty_id::text THEN
          RAISE EXCEPTION 'entity binding disagrees with evidence';
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER bind_entity BEFORE INSERT ON entity_histories
        FOR EACH ROW EXECUTE FUNCTION guard_entity_binding();
    """)
    for table in ("entities", "entity_histories"):
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
        "DROP TABLE entity_histories; DROP TABLE entities; DROP FUNCTION guard_entity_binding()"
    )
