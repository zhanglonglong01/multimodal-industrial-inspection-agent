"""Phase 4 DTOs for the application, HTTP API, and dashboard."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from .analysis_schemas import Severity
from .schemas import Asset, StrictModel
from .workflow_schemas import ApprovalDecision, RiskLevel


class RunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ArtifactRecord(StrictModel):
    artifact_id: str = Field(pattern=r"^ARTIFACT-[A-Z0-9-]+$")
    asset_id: str
    media_type: str
    extension: str
    relative_path: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    fixture: bool
    created_at: datetime


class InspectionRecord(StrictModel):
    inspection_id: str = Field(pattern=r"^INSPECTION-[A-Z0-9-]+$")
    asset_id: str
    scenario_id: str
    sensor_dataset_id: str
    image_artifact_id: str
    vision_fixture_id: str
    synthetic: bool
    created_at: datetime


class RunRecord(StrictModel):
    run_id: str = Field(pattern=r"^RUN-[A-Z0-9-]+$")
    inspection_id: str
    status: RunStatus
    current_stage: str
    approval_id: str | None = None
    work_order_id: str | None = None
    state: dict[str, Any]
    interrupt_payload: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class ErrorResponse(StrictModel):
    code: str
    message: str
    details: Any = None
    request_id: str


class HealthResponse(StrictModel):
    status: str
    request_id: str


class ReadyCheck(StrictModel):
    name: str
    ready: bool
    detail: str


class ReadyResponse(StrictModel):
    status: str
    checks: list[ReadyCheck]
    request_id: str


class AssetListResponse(StrictModel):
    assets: list[Asset]


class AssetDetailResponse(StrictModel):
    asset: Asset
    recent_inspections: list[InspectionRecord]


class InspectionCreateResponse(StrictModel):
    inspection: InspectionRecord


class RunStartResponse(StrictModel):
    run_id: str
    status: RunStatus
    current_stage: str
    approval_id: str | None


class ApprovalDecisionRequest(StrictModel):
    decision: ApprovalDecision
    reviewer: str = Field(default="demo-reviewer", min_length=1)
    reason: str | None = None


class VisionSummary(StrictModel):
    available: bool
    provider: str | None = None
    fixture: bool = False
    findings: list[dict[str, Any]] = Field(default_factory=list)


class SensorSummary(StrictModel):
    available: bool
    quality_usable: bool | None = None
    anomaly_point_count: int = 0
    segments: list[dict[str, Any]] = Field(default_factory=list)


class RunStatusResponse(StrictModel):
    run_id: str
    inspection_id: str
    status: RunStatus
    current_stage: str
    warnings: list[str]
    errors: list[str]
    vision_summary: VisionSummary
    sensor_summary: SensorSummary
    diagnosis: dict[str, Any] | None
    risk: dict[str, Any] | None
    approval_status: str
    approval_id: str | None
    work_order_status: str
    work_order_id: str | None


class SensorChartSeries(StrictModel):
    sensor_id: str
    display_name: str
    unit: str
    operating_min: float
    operating_max: float
    timestamps: list[str]
    values: list[float]
    anomaly_segments: list[dict[str, Any]]


class KnowledgeEvidenceView(StrictModel):
    evidence_id: str
    chunk_id: str
    title: str
    section: str
    excerpt: str
    score: float | None
    source: str


class WorkOrderView(StrictModel):
    work_order_id: str
    draft_id: str
    asset_id: str
    title: str
    description: str
    priority: RiskLevel
    status: str
    recommended_actions: list[str]
    evidence_ids: list[str]
    approval_id: str | None
    idempotency_key: str
    created_at: datetime


class RunDetailView(StrictModel):
    run: RunRecord
    inspection: InspectionRecord
    asset: Asset
    artifact: ArtifactRecord
    status: RunStatusResponse
    sensor_series: list[SensorChartSeries]
    knowledge_evidence: list[KnowledgeEvidenceView]
    stages: list[dict[str, Any]]
    work_order: WorkOrderView | None


class WorkOrderListResponse(StrictModel):
    work_orders: list[WorkOrderView]
