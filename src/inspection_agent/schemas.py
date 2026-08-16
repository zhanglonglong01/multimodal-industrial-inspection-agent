"""Pydantic contracts for Phase 1 assets and reproducible demo data."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AssetType(StrEnum):
    PUMP = "pump"
    MOTOR = "motor"
    COMPRESSOR = "compressor"


class AssetStatus(StrEnum):
    ACTIVE = "active"


class Criticality(StrEnum):
    MEDIUM = "medium"
    HIGH = "high"


class AnomalyDirection(StrEnum):
    INCREASE = "increase"
    DECREASE = "decrease"


class SensorDefinition(StrictModel):
    sensor_name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    display_name: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    operating_min: float
    operating_max: float

    @model_validator(mode="after")
    def validate_range(self) -> "SensorDefinition":
        if self.operating_min >= self.operating_max:
            raise ValueError("operating_min must be lower than operating_max")
        return self


class Asset(StrictModel):
    schema_version: str = "1.0"
    asset_id: str = Field(pattern=r"^[A-Z]+-[0-9]{3}$")
    name: str = Field(min_length=1)
    asset_type: AssetType
    site: str = Field(min_length=1)
    status: AssetStatus = AssetStatus.ACTIVE
    criticality: Criticality
    description: str = Field(min_length=1)
    sensors: list[SensorDefinition] = Field(min_length=1)

    @field_validator("sensors")
    @classmethod
    def unique_sensors(
        cls, sensors: list[SensorDefinition]
    ) -> list[SensorDefinition]:
        names = [item.sensor_name for item in sensors]
        if len(names) != len(set(names)):
            raise ValueError("sensor names must be unique per asset")
        return sensors


class GroundTruthAnomaly(StrictModel):
    sensor_name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    start_time: datetime
    end_time: datetime
    direction: AnomalyDirection
    failure_mode: str = Field(min_length=1)
    injection: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_time_window(self) -> "GroundTruthAnomaly":
        if self.start_time.tzinfo is None or self.end_time.tzinfo is None:
            raise ValueError("anomaly timestamps must include a timezone")
        if self.start_time >= self.end_time:
            raise ValueError("anomaly start_time must be earlier than end_time")
        return self


class SensorDataManifest(StrictModel):
    dataset_id: str = Field(pattern=r"^DATASET-SCENARIO-[0-9]{3}$")
    format: str = Field(pattern=r"^csv$")
    path: str
    sha256: Sha256
    random_seed: int = Field(ge=0)
    timestamp_column: str = "timestamp"
    sensor_columns: list[str] = Field(min_length=1)
    sample_interval_seconds: int = Field(gt=0)
    row_count: int = Field(gt=0)
    start_time: datetime
    end_time: datetime

    @field_validator("path")
    @classmethod
    def relative_safe_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("path must be a safe relative POSIX path")
        return value

    @field_validator("sensor_columns")
    @classmethod
    def unique_columns(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("sensor_columns must be unique")
        return value

    @model_validator(mode="after")
    def validate_series_window(self) -> "SensorDataManifest":
        if self.start_time.tzinfo is None or self.end_time.tzinfo is None:
            raise ValueError("series timestamps must include a timezone")
        if self.start_time >= self.end_time:
            raise ValueError("series start_time must be earlier than end_time")
        return self


class ScenarioGroundTruth(StrictModel):
    window_semantics: str = "[start_time, end_time)"
    is_normal: bool
    expected_failure_mode: str | None
    sensor_anomalies: list[GroundTruthAnomaly]

    @model_validator(mode="after")
    def validate_normality(self) -> "ScenarioGroundTruth":
        if self.is_normal:
            if self.expected_failure_mode is not None or self.sensor_anomalies:
                raise ValueError("normal scenario cannot contain a failure mode or anomalies")
        elif self.expected_failure_mode is None or not self.sensor_anomalies:
            raise ValueError("fault scenario requires a failure mode and anomalies")
        return self


class ScenarioManifest(StrictModel):
    schema_version: str = "1.0"
    scenario_id: str = Field(pattern=r"^SCENARIO-[0-9]{3}$")
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    synthetic: bool = True
    asset_id: str = Field(pattern=r"^[A-Z]+-[0-9]{3}$")
    image_fixture_id: str = Field(pattern=r"^IMAGE-SCENARIO-[0-9]{3}$")
    knowledge_document_ids: list[str] = Field(min_length=1)
    sensor_data: SensorDataManifest
    ground_truth: ScenarioGroundTruth


class ImageFixture(StrictModel):
    fixture_id: str = Field(pattern=r"^IMAGE-SCENARIO-[0-9]{3}$")
    scenario_id: str = Field(pattern=r"^SCENARIO-[0-9]{3}$")
    asset_id: str = Field(pattern=r"^[A-Z]+-[0-9]{3}$")
    path: str
    media_type: str = "image/png"
    sha256: Sha256
    synthetic: bool = True
    fixture_only: bool = True
    visual_labels: list[str]
    description: str = Field(min_length=1)

    @field_validator("path")
    @classmethod
    def relative_safe_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("path must be a safe relative POSIX path")
        return value


class ImageFixtureManifest(StrictModel):
    schema_version: str = "1.0"
    synthetic: bool = True
    fixtures: list[ImageFixture]


class KnowledgeDocument(StrictModel):
    document_id: str = Field(pattern=r"^KNOW-[A-Z0-9-]+$")
    title: str = Field(min_length=1)
    document_type: str = Field(min_length=1)
    version: str = Field(min_length=1)
    path: str
    synthetic: bool = True
    asset_types: list[AssetType]

    @field_validator("path")
    @classmethod
    def relative_safe_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("path must be a safe relative POSIX path")
        return value


class KnowledgeManifest(StrictModel):
    schema_version: str = "1.0"
    documents: list[KnowledgeDocument]


class SensorDatasetMetadata(StrictModel):
    schema_version: str = "1.0"
    dataset_id: str = Field(pattern=r"^DATASET-SCENARIO-[0-9]{3}$")
    scenario_id: str = Field(pattern=r"^SCENARIO-[0-9]{3}$")
    asset_id: str = Field(pattern=r"^[A-Z]+-[0-9]{3}$")
    relative_path: str
    sha256: Sha256
    random_seed: int = Field(ge=0)
    row_count: int = Field(gt=0)
    sample_interval_seconds: int = Field(gt=0)
    start_time: datetime
    end_time: datetime
    sensor_columns: list[str] = Field(min_length=1)


class SeedResult(StrictModel):
    database_path: str
    asset_ids: list[str]
    scenario_ids: list[str]
    dataset_hashes: dict[str, Sha256]
    image_fixture_count: int
    knowledge_document_count: int


class DemoValidationResult(StrictModel):
    valid: bool
    scenario_ids: list[str]
    checks: list[str]
