"""Type-checked imports and point-in-time reference-data resolution."""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import Connection, text

from tailwatch.db.evidence import EvidenceRepository, Fact
from tailwatch.domain.time import ReplayMode, utc
from tailwatch.entities.history import HISTORY, CompanyHistory, EntityHistory, SecurityHistory


class AmbiguousTicker(ValueError):
    pass


class EntityRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self.evidence = EvidenceRepository(connection)

    def register(self, entity_id: UUID, kind: str) -> None:
        self.connection.execute(
            text("""
          INSERT INTO entities VALUES (:id, :kind) ON CONFLICT DO NOTHING
        """),
            {"id": entity_id, "kind": kind},
        )
        actual = self.connection.execute(
            text("SELECT kind FROM entities WHERE entity_id=:id"), {"id": entity_id}
        ).scalar_one()
        if actual != kind:
            raise ValueError("stable entity ID reused for another kind")

    def append(self, fact: Fact) -> None:
        history = HISTORY.validate_python(fact.payload)
        if fact.logical_key != f"entity:{history.entity_id}":
            raise ValueError("history must use stable entity logical key")
        if fact.valid_from != history.periods[0].start or fact.valid_to is not None:
            raise ValueError("history envelope starts at first period and has no upper bound")
        self.register(history.entity_id, history.kind)
        self.evidence.append(fact)
        if self.connection.execute(
            text("SELECT 1 FROM entity_histories WHERE fact_id=:id"), {"id": fact.fact_id}
        ).first():
            return
        # Foreign keys require explicitly registered company/person/counterparty identities.
        self.connection.execute(
            text("""
          INSERT INTO entity_histories (fact_id, entity_id, kind, company_id, person_id,
                                        counterparty_id)
          VALUES (:fact, :entity, :kind, :company, :person, :counterparty)
          ON CONFLICT (fact_id) DO NOTHING
        """),
            {
                "fact": fact.fact_id,
                "entity": history.entity_id,
                "kind": history.kind,
                "company": getattr(history, "company_id", None),
                "person": getattr(history, "person_id", None),
                "counterparty": getattr(history, "counterparty_id", None),
            },
        )

    def histories(
        self,
        source_id: str,
        cutoff: datetime,
        effective_at: datetime,
        mode: ReplayMode = ReplayMode.OPERATIONAL,
        latency: int | None = None,
    ) -> list[tuple[Fact, EntityHistory]]:
        facts = self.evidence.as_of(
            source_id=source_id,
            cutoff=cutoff,
            effective_at=effective_at,
            mode=mode,
            public_latency_seconds=latency,
        )
        bound = set(
            self.connection.execute(
                text("""
            SELECT h.fact_id FROM entity_histories h JOIN evidence_facts f USING (fact_id)
            WHERE f.source_id=:source
        """),
                {"source": source_id},
            ).scalars()
        )
        return [
            (fact, HISTORY.validate_python(fact.payload)) for fact in facts if fact.fact_id in bound
        ]

    def resolve_ticker(
        self,
        ticker: str,
        exchange: str,
        *,
        source_id: str,
        cutoff: datetime,
        effective_at: datetime,
    ) -> UUID | None:
        matches = []
        for _, history in self.histories(source_id, cutoff, effective_at):
            if isinstance(history, SecurityHistory):
                period = history.at(effective_at)
                if (
                    period
                    and period.active
                    and period.ticker == ticker
                    and period.exchange == exchange
                ):
                    matches.append(history.entity_id)
        if len(matches) > 1:
            raise AmbiguousTicker("multiple securities match ticker, exchange, and date")
        return matches[0] if matches else None

    def universe(
        self, *, source_id: str, cutoff: datetime, effective_at: datetime, sector: str
    ) -> tuple[str, bytes]:
        """Canonical export frozen by hash; includes dated delisting history evidence."""
        histories = self.histories(source_id, cutoff, effective_at)
        securities = {
            h.company_id
            for _, h in histories
            if isinstance(h, SecurityHistory) and (p := h.at(effective_at)) is not None and p.active
        }
        members = sorted(
            str(h.entity_id)
            for _, h in histories
            if isinstance(h, CompanyHistory)
            and h.entity_id in securities
            and (company_period := h.at(effective_at)) is not None
            and company_period.status == "public"
            and company_period.sector == sector
        )
        body = json.dumps(
            {
                "source_id": source_id,
                "cutoff": utc(cutoff).isoformat(),
                "effective_at": utc(effective_at).isoformat(),
                "sector": sector,
                "members": members,
                "evidence_ids": sorted(str(f.fact_id) for f, _ in histories),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(body).hexdigest(), body


def read_history_fixture(path: Path) -> EntityHistory:
    """Accept a complete history JSON document, not an undated current-company CSV."""
    return HISTORY.validate_json(path.read_bytes())
