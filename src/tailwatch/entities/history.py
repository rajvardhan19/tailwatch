"""Complete, non-overlapping histories; corrections version the whole history."""

from datetime import datetime
from typing import Annotated, Any, Generic, Literal, TypeAlias, TypeVar, cast
from uuid import UUID

from pydantic import AwareDatetime, Field, TypeAdapter, model_validator

from tailwatch.domain.time import utc
from tailwatch.sources.policy import Contract, NonEmpty


class Period(Contract):
    start: AwareDatetime
    end: AwareDatetime | None = None

    @model_validator(mode="after")
    def ordered(self) -> "Period":
        if self.end is not None and self.end <= self.start:
            raise ValueError("period must be nonempty")
        return self


class CompanyPeriod(Period):
    name: NonEmpty
    sector: NonEmpty
    status: Literal["public", "private", "delisted"]
    fiscal_year_end: str = Field(pattern=r"^\d{2}-\d{2}$")

    @model_validator(mode="after")
    def fiscal_date(self) -> "CompanyPeriod":
        datetime.strptime(f"2000-{self.fiscal_year_end}", "%Y-%m-%d")
        return self


class SecurityPeriod(Period):
    ticker: NonEmpty
    exchange: NonEmpty
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    active: bool


class OfficePeriod(Period):
    kind: Literal["hq", "rd", "plant", "other"]
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    address: NonEmpty


class RolePeriod(Period):
    role: Literal["ceo", "cfo", "director", "officer", "other"]


P = TypeVar("P", bound=Period)


class History(Contract, Generic[P]):
    entity_id: UUID
    periods: tuple[P, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def nonoverlapping(self) -> "History[P]":
        for previous, current in zip(self.periods, self.periods[1:], strict=False):
            if previous.end is None or previous.end > current.start:
                raise ValueError("periods must be sorted and non-overlapping")
        return self

    def at(self, when: datetime) -> P | None:
        moment = utc(when)
        return next(
            (p for p in self.periods if p.start <= moment and (p.end is None or moment < p.end)),
            None,
        )


class CompanyHistory(History[CompanyPeriod]):
    kind: Literal["company"] = "company"
    cik: str | None = Field(default=None, pattern=r"^\d{10}$")


class SecurityHistory(History[SecurityPeriod]):
    kind: Literal["security"] = "security"
    company_id: UUID


class OfficeHistory(History[OfficePeriod]):
    kind: Literal["office"] = "office"
    company_id: UUID


class InsiderHistory(History[RolePeriod]):
    # One identity per person/company role history; person_id links other issuers.
    kind: Literal["insider_role"] = "insider_role"
    company_id: UUID
    person_id: UUID


class RelationshipPeriod(Period):
    relationship: Literal["subsidiary", "supplier", "customer", "other"]


class RelationshipHistory(History[RelationshipPeriod]):
    kind: Literal["relationship"] = "relationship"
    company_id: UUID
    counterparty_id: UUID

    @model_validator(mode="after")
    def not_self(self) -> "RelationshipHistory":
        if self.company_id == self.counterparty_id:
            raise ValueError("relationship cannot point to itself")
        return self


EntityHistory: TypeAlias = Annotated[
    CompanyHistory | SecurityHistory | OfficeHistory | InsiderHistory | RelationshipHistory,
    Field(discriminator="kind"),
]
# Pydantic accepts Annotated unions at runtime; its constructor overload is narrower.
HISTORY: TypeAdapter[EntityHistory] = TypeAdapter(cast(Any, EntityHistory))
