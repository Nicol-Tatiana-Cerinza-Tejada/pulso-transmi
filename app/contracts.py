from __future__ import annotations

import math
import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
GIT_COMMIT = re.compile(r"^[0-9a-fA-F]{7,40}$")


def _aware(value: datetime, field: str) -> datetime:
    if value.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone offset")
    return value


class ModelTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1, max_length=64)
    trained_at: datetime | None = None
    training_data_end: datetime | None = None
    git_commit: str | None = Field(default=None, min_length=7, max_length=40)

    @field_validator("version")
    @classmethod
    def valid_version(cls, value: str) -> str:
        if not SAFE_VERSION.fullmatch(value):
            raise ValueError("version contains unsupported characters")
        return value

    @field_validator("git_commit")
    @classmethod
    def valid_commit(cls, value: str | None) -> str | None:
        if value is not None and not GIT_COMMIT.fullmatch(value):
            raise ValueError("git_commit must contain 7 to 40 hexadecimal characters")
        return value.lower() if value else value

    @field_validator("trained_at", "training_data_end")
    @classmethod
    def aware_dates(cls, value: datetime | None, info):
        return _aware(value, info.field_name) if value else value


class PredictionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    station_id: str = Field(pattern=r"^[0-9]{5}$")
    target_at: datetime
    value: float = Field(ge=0, le=100_000)

    @field_validator("target_at")
    @classmethod
    def aware_target(cls, value: datetime) -> datetime:
        return _aware(value, "target_at")

    @field_validator("value")
    @classmethod
    def finite_value(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("value must be finite")
        return value


class SubmissionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(pattern=r"^1\.0$")
    cycle_id: str = Field(pattern=r"^cyc_[A-Za-z0-9_-]{1,80}$")
    client_run_id: str = Field(min_length=1, max_length=128)
    data_cutoff: datetime
    model: ModelTrace
    predictions: list[PredictionInput] = Field(min_length=1, max_length=100)

    @field_validator("data_cutoff")
    @classmethod
    def aware_cutoff(cls, value: datetime) -> datetime:
        return _aware(value, "data_cutoff")
