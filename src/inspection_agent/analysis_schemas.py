"""Typed contracts shared by the independent Phase 2 analysis modules."""

from __future__ import annotations

import math
from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import Field, field_validator, model_validator

from .schemas import AssetType, StrictModel


Identifier = Annotated[str, Field(pattern=r"^[A-Z0-9][A-Z0-9-]*$")]
SensorId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]


class EvidenceKind(StrEnum):
    VISION = "VISION"
    SENSOR = "SENSOR"
    KNOWLEDGE = "KNOWLEDGE"


class EvidenceRef(StrictModel):
    evidence_id: str = Field(
        pattern=r"^EVIDENCE-(VISION|SENSOR|KNOWLEDGE)-[A-Z0-9][A-Z0-9-]*$"
    )
    kind: EvidenceKind
    source_id: Identifier
    summary: str = Field(min_length=1)
    observed_at: datetime | None

    @model_validator(mode="after")
    def evidence_prefix_matches_kind(self) -> "EvidenceRef":
        if not self.evidence_id.startswith(f"EVIDENCE-{self.kind.value}-"):
            raise ValueError("evidence_id prefix must match evidence kind")
        if self.observed_at is not None and self.observed_at.tzinfo is None:
            raise ValueError("observed_at must include a timezone")
        return self


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class VisionLabel(StrEnum):
    LEAKAGE_TRACE = "leakage_trace"
    CORROSION = "corrosion"
    CRACK_LIKE_MARK = "crack_like_mark"
    LOOSE_COMPONENT = "loose_component"
    DISCOLORATION = "discoloration"
    FOREIGN_OBJECT = "foreign_object"
    NO_VISIBLE_ANOMALY = "no_visible_anomaly"


class ImageQualityRating(StrEnum):
    GOOD = "good"
    LIMITED = "limited"
    UNUSABLE = "unusable"


class ImageQuality(StrictModel):
    rating: ImageQualityRating
    usable: bool
    notes: list[str] = Field(default_factory=list)


class ImageRegion(StrictModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0)
    height: float = Field(gt=0.0, le=1.0)
    description: str = Field(min_length=1)

    @model_validator(mode="after")
    def region_stays_in_image(self) -> "ImageRegion":
        if self.x + self.width > 1.0 or self.y + self.height > 1.0:
            raise ValueError("normalized region must stay inside the image")
        return self


class VisionFinding(StrictModel):
    finding_id: Identifier
    label: VisionLabel
    observation: str = Field(min_length=1)
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    region: ImageRegion | None
    evidence_id: str = Field(pattern=r"^EVIDENCE-VISION-[A-Z0-9][A-Z0-9-]*$")
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def observed_at_has_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("observed_at must include a timezone")
        return value

    def to_evidence_ref(self) -> EvidenceRef:
        return EvidenceRef(
            evidence_id=self.evidence_id,
            kind=EvidenceKind.VISION,
            source_id=self.finding_id,
            summary=f"{self.label.value}: {self.observation}",
            observed_at=self.observed_at,
        )


class VisionAnalysisResult(StrictModel):
    artifact_id: Identifier
    asset_id: str = Field(pattern=r"^[A-Z]+-[0-9]{3}$")
    image_quality: ImageQuality
    findings: list[VisionFinding]
    negative_findings: list[VisionLabel]
    limitations: list[str] = Field(min_length=1)
    provider: str = Field(min_length=1)
    fixture: bool

    @model_validator(mode="after")
    def fixture_provider_is_explicit(self) -> "VisionAnalysisResult":
        if self.provider == "fixture" and not self.fixture:
            raise ValueError("fixture provider results must set fixture=true")
        return self


class DataQualityReport(StrictModel):
    source: str = Field(min_length=1)
    row_count: int = Field(ge=0)
    timestamp_column: str = Field(min_length=1)
    timestamp_parse_errors: int = Field(ge=0)
    timestamps_strictly_increasing: bool
    duplicate_timestamp_count: int = Field(ge=0)
    missing_columns: list[str]
    missing_counts: dict[str, int]
    missing_rates: dict[str, float]
    non_numeric_counts: dict[str, int]
    expected_sampling_interval_seconds: float = Field(gt=0)
    observed_sampling_interval_seconds: float | None
    sampling_interval_consistent: bool
    irregular_interval_count: int = Field(ge=0)
    start_time: datetime | None
    end_time: datetime | None
    time_span_seconds: float | None = Field(default=None, ge=0)
    is_usable: bool
    warnings: list[str]
    errors: list[str]

    @field_validator("missing_rates")
    @classmethod
    def missing_rates_are_bounded(cls, value: dict[str, float]) -> dict[str, float]:
        if any(rate < 0.0 or rate > 1.0 for rate in value.values()):
            raise ValueError("missing rates must be between zero and one")
        return value


class AnomalyMethod(StrEnum):
    OPERATING_LIMIT = "operating_limit"
    ROLLING_MEDIAN_MAD = "rolling_median_mad"
    MAD_ZERO_FALLBACK = "mad_zero_fallback"


class DetectorParameters(StrictModel):
    window_size: int = Field(default=97, ge=3)
    min_periods: int = Field(default=25, ge=3)
    window_alignment: str = Field(default="centered", pattern=r"^centered$")
    robust_z_constant: float = Field(default=0.6745, gt=0)
    mad_threshold: float = Field(default=3.5, gt=0)
    mad_zero_fallback_score: float = Field(default=4.5, gt=0)
    min_segment_points: int = Field(default=2, ge=1)
    max_gap_intervals: int = Field(default=1, ge=1)
    medium_score_threshold: float = Field(default=8.0, gt=0)
    high_score_threshold: float = Field(default=12.0, gt=0)

    @model_validator(mode="after")
    def validate_window(self) -> "DetectorParameters":
        if self.window_size % 2 == 0:
            raise ValueError("window_size must be odd")
        if self.min_periods > self.window_size:
            raise ValueError("min_periods cannot exceed window_size")
        if self.mad_zero_fallback_score <= self.mad_threshold:
            raise ValueError("MAD zero fallback score must exceed the threshold")
        if self.medium_score_threshold >= self.high_score_threshold:
            raise ValueError("medium severity threshold must be below high threshold")
        return self


class AnomalyPoint(StrictModel):
    sensor_id: SensorId
    timestamp: datetime
    direction: str = Field(pattern=r"^(increase|decrease)$")
    score: float = Field(ge=0)
    methods: list[AnomalyMethod] = Field(min_length=1)

    @field_validator("timestamp")
    @classmethod
    def timestamp_has_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("anomaly point timestamp must include a timezone")
        return value

    @field_validator("score")
    @classmethod
    def score_is_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("anomaly score must be finite")
        return value


class AnomalySegment(StrictModel):
    segment_id: Identifier
    sensor_id: SensorId
    start_time: datetime
    end_time: datetime
    direction: str = Field(pattern=r"^(increase|decrease)$")
    peak_score: float = Field(ge=0)
    severity: Severity
    method: str = Field(min_length=1)
    parameters: dict[str, int | float | str | bool]
    evidence_id: str = Field(pattern=r"^EVIDENCE-SENSOR-[A-Z0-9][A-Z0-9-]*$")
    point_count: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_segment(self) -> "AnomalySegment":
        if self.start_time.tzinfo is None or self.end_time.tzinfo is None:
            raise ValueError("segment timestamps must include a timezone")
        if self.start_time >= self.end_time:
            raise ValueError("segment start_time must precede end_time")
        if not math.isfinite(self.peak_score):
            raise ValueError("peak_score must be finite")
        return self

    def to_evidence_ref(self) -> EvidenceRef:
        return EvidenceRef(
            evidence_id=self.evidence_id,
            kind=EvidenceKind.SENSOR,
            source_id=self.segment_id,
            summary=(
                f"{self.sensor_id} {self.direction} anomaly from "
                f"{self.start_time.isoformat()} to {self.end_time.isoformat()}"
            ),
            observed_at=self.start_time,
        )


class SensorAnalysisResult(StrictModel):
    dataset_id: Identifier
    detector: str = Field(min_length=1)
    parameters: DetectorParameters
    quality: DataQualityReport
    evaluated_sensor_ids: list[SensorId]
    anomaly_points: list[AnomalyPoint]
    segments: list[AnomalySegment]
    warnings: list[str]


class FailureMode(StrictModel):
    mode_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    asset_type: AssetType
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    symptoms: list[str] = Field(min_length=1)
    visual_labels: list[VisionLabel]
    related_sensors: list[SensorId] = Field(min_length=1)
    possible_causes: list[str] = Field(min_length=1)
    recommended_checks: list[str] = Field(min_length=1)
    source: str = Field(min_length=1)


class KnowledgeSection(StrictModel):
    section: str = Field(min_length=1)
    text: str = Field(min_length=1)


class LoadedKnowledgeDocument(StrictModel):
    doc_id: Identifier
    title: str = Field(min_length=1)
    version: str = Field(min_length=1)
    source: str = Field(min_length=1)
    document_type: str = Field(min_length=1)
    asset_types: list[AssetType]
    sections: list[KnowledgeSection] = Field(min_length=1)


class KnowledgeChunk(StrictModel):
    chunk_id: Identifier
    doc_id: Identifier
    title: str = Field(min_length=1)
    section: str = Field(min_length=1)
    source: str = Field(min_length=1)
    text: str = Field(min_length=1)
    asset_types: list[AssetType]
    index_version: str = Field(min_length=1)
    evidence_id: str = Field(pattern=r"^EVIDENCE-KNOWLEDGE-[A-Z0-9][A-Z0-9-]*$")

    def to_evidence_ref(self) -> EvidenceRef:
        return EvidenceRef(
            evidence_id=self.evidence_id,
            kind=EvidenceKind.KNOWLEDGE,
            source_id=self.chunk_id,
            summary=f"{self.title} / {self.section}: {self.text[:240]}",
            observed_at=None,
        )


class KnowledgeIndexMetadata(StrictModel):
    schema_version: str = "1.0"
    index_version: str = Field(min_length=1)
    embedding_provider: str = Field(min_length=1)
    embedding_version: str = Field(min_length=1)
    embedding_dimension: int = Field(gt=0)
    chunk_count: int = Field(ge=0)
    chunks: list[KnowledgeChunk]

    @model_validator(mode="after")
    def chunk_count_matches(self) -> "KnowledgeIndexMetadata":
        if self.chunk_count != len(self.chunks):
            raise ValueError("chunk_count must match metadata chunks")
        if len({chunk.chunk_id for chunk in self.chunks}) != len(self.chunks):
            raise ValueError("chunk IDs must be unique")
        return self


class RetrievedKnowledgeChunk(StrictModel):
    chunk_id: Identifier
    doc_id: Identifier
    title: str = Field(min_length=1)
    section: str = Field(min_length=1)
    score: float
    excerpt: str = Field(min_length=1)
    source: str = Field(min_length=1)
    index_version: str = Field(min_length=1)
    evidence_id: str = Field(pattern=r"^EVIDENCE-KNOWLEDGE-[A-Z0-9][A-Z0-9-]*$")


class RetrievalQuery(StrictModel):
    query_id: Identifier
    scenario_id: str = Field(pattern=r"^SCENARIO-[0-9]{3}$")
    query: str = Field(min_length=1)
    expected_relevant_chunk_ids: list[Identifier] = Field(min_length=1)


class MetricScores(StrictModel):
    precision: float = Field(ge=0.0, le=1.0)
    recall: float = Field(ge=0.0, le=1.0)
    f1: float = Field(ge=0.0, le=1.0)
    true_positives: int = Field(ge=0)
    false_positives: int = Field(ge=0)
    false_negatives: int = Field(ge=0)


class ScenarioDetectorEvaluation(StrictModel):
    scenario_id: str = Field(pattern=r"^SCENARIO-[0-9]{3}$")
    point_metrics: MetricScores
    segment_metrics: MetricScores
    predicted_point_count: int = Field(ge=0)
    expected_point_count: int = Field(ge=0)
    predicted_segment_count: int = Field(ge=0)
    expected_segment_count: int = Field(ge=0)


class DetectorEvaluationReport(StrictModel):
    detector: str
    parameters: DetectorParameters
    scenarios: list[ScenarioDetectorEvaluation]
    overall_point_metrics: MetricScores
    overall_segment_metrics: MetricScores


class RetrievalQueryResult(StrictModel):
    query_id: Identifier
    scenario_id: str = Field(pattern=r"^SCENARIO-[0-9]{3}$")
    expected_relevant_chunk_ids: list[Identifier]
    retrieved_chunk_ids: list[Identifier]
    recall_at_1: float = Field(ge=0.0, le=1.0)
    recall_at_3: float = Field(ge=0.0, le=1.0)
    reciprocal_rank: float = Field(ge=0.0, le=1.0)


class RetrievalEvaluationReport(StrictModel):
    query_count: int = Field(gt=0)
    recall_at_1: float = Field(ge=0.0, le=1.0)
    recall_at_3: float = Field(ge=0.0, le=1.0)
    mrr: float = Field(ge=0.0, le=1.0)
    queries: list[RetrievalQueryResult]


class Phase2ScenarioAnalysis(StrictModel):
    scenario_id: str = Field(pattern=r"^SCENARIO-[0-9]{3}$")
    asset_id: str = Field(pattern=r"^[A-Z]+-[0-9]{3}$")
    vision: VisionAnalysisResult
    sensor: SensorAnalysisResult
    failure_modes: list[FailureMode]
    retrieval_query: str = Field(min_length=1)
    knowledge: list[RetrievedKnowledgeChunk]
    evidence: list[EvidenceRef]
