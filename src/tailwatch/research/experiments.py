import hashlib
from datetime import timedelta
from typing import Literal

from pydantic import Field, model_validator

from tailwatch.entities.history import Period
from tailwatch.sources.objects import ObjectStore
from tailwatch.sources.policy import Contract, NonEmpty


class Partition(Period):
    @model_validator(mode="after")
    def bounded(self) -> "Partition":
        if self.end is None:
            raise ValueError("partition must end")
        return self


class Experiment(Contract):
    name: NonEmpty
    universe_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    feature_version: NonEmpty
    train: Partition
    calibration: Partition
    threshold: Partition
    test: Partition
    minimum_alerts: int = Field(ge=1)
    minimum_deals: int = Field(ge=1)
    alert_budget: int = Field(ge=1)
    precision_target: float = Field(gt=0, le=1, allow_inf_nan=False)
    lift_target: float = Field(gt=1, allow_inf_nan=False)
    cooldown_days: Literal[180] = 180
    seed: int
    bootstrap_draws: int = Field(ge=200)
    synthetic: bool
    rationale: NonEmpty

    @model_validator(mode="after")
    def embargoes(self) -> "Experiment":
        periods = [self.train, self.calibration, self.threshold, self.test]
        for left, right in zip(periods, periods[1:], strict=False):
            assert left.end is not None
            if left.end + timedelta(days=180) > right.start:
                raise ValueError("partitions overlap label windows")
        return self

    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()

    def register(self, store: ObjectStore) -> str:
        return store.put(self.model_dump_json().encode())
