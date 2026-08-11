"""Typed Phase 3 contracts for diagnosis, risk, approval, and graph state."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, TypedDict

from pydantic import Field, field_validator, model_validator

from .analysis_schemas import (
    AnomalySegment,
    EvidenceRef,
    FailureMode,
    Severity,
    VisionAnalysisResult,
)
from .schemas import Asset, Criticality, StrictModel


class EvidenceStrength(StrEnum):
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ApprovalDecision(StrEnum):
    PENDING = "PENDING"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REQUEST_CHANGES = "REQUEST_CHANGES"


class DraftStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    ISSUED = "ISSUED"


class TraceStatus(StrEnum):
    SUCCESS = "SUCCESS"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"


class SensorResultSummary(StrictModel):
    """Checkpoint-safe sensor result: no raw CSV rows or point-level series."""

    dataset_id: str
    detector: str
    quality_usable: bool
    evaluated_sensor_ids: list[str]
    segments: list[AnomalySegment]
    anomaly_point_count: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)


class DiagnosisProviderInput(StrictModel):
    """The complete and deliberately narrow diagnosis-provider boundary."""

    asset_context: Asset
    vision_evidence: list[EvidenceRef]
    sensor_evidence: list[EvidenceRef]
    failure_mode_candidates: list[FailureMode]
    knowledge_evidence: list[EvidenceRef]


class DiagnosisReport(StrictModel):
    diagnosis_id: str = Field(pattern=r"^DIAG-[A-Z0-9-]+$")
    primary_fault_candidate: str | None
    actionable: bool
    alternative_candidates: list[str] = Field(default_factory=list)
    possible_causes: list[str] = Field(default_factory=list)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    evidence_strength: EvidenceStrength
    explanation: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    fixture: bool

    @model_validator(mode="after")
    def evidence_lists_do_not_overlap(self) -> "DiagnosisReport":
        if self.primary_fault_candidate is None and self.actionable:
            raise ValueError("a diagnosis without a primary fault cannot be actionable")
        if set(self.supporting_evidence_ids) & set(self.contradicting_evidence_ids):
            raise ValueError("supporting and contradicting evidence cannot overlap")
        return self


class RiskInputs(StrictModel):
    fault_severity: Severity
    evidence_strength: EvidenceStrength
    asset_criticality: Criticality
    sensor_severity: Severity


class RiskAssessment(StrictModel):
    policy_version: str = Field(min_length=1)
    risk_level: RiskLevel
    inputs: RiskInputs
    explanation: str = Field(min_length=1)


class WorkOrderDraft(StrictModel):
    draft_id: str = Field(pattern=r"^DRAFT-[A-Z0-9-]+$")
    inspection_id: str
    asset_id: str
    diagnosis_id: str
    risk_level: RiskLevel
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    priority: RiskLevel
    summary: str = Field(min_length=1)
    recommended_actions: list[str] = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: DraftStatus
    created_at: datetime


class ApprovalRequest(StrictModel):
    approval_id: str = Field(pattern=r"^APPROVAL-[A-Z0-9-]+$")
    draft_id: str
    draft_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    risk_level: RiskLevel
    decision: ApprovalDecision = ApprovalDecision.PENDING
    reviewer: str | None = None
    reason: str | None = None
    created_at: datetime
    decided_at: datetime | None = None


class ApprovalDecisionInput(StrictModel):
    decision: ApprovalDecision
    reviewer: str = "demo-reviewer"
    comment: str | None = None

    @field_validator("decision")
    @classmethod
    def decision_must_be_terminal(cls, value: ApprovalDecision) -> ApprovalDecision:
        if value is ApprovalDecision.PENDING:
            raise ValueError("PENDING is not a resume decision")
        return value


class WorkOrder(StrictModel):
    work_order_id: str = Field(pattern=r"^WO-[A-Z0-9-]+$")
    draft_id: str
    approval_id: str | None
    asset_id: str
    risk_level: RiskLevel
    summary: str
    recommended_actions: list[str]
    idempotency_key: str = Field(min_length=1)
    created_at: datetime


class ToolTrace(StrictModel):
    trace_id: str = Field(pattern=r"^TRACE-[A-Z0-9-]+$")
    run_id: str
    node_name: str = Field(min_length=1)
    attempt_index: int = Field(ge=1)
    tool_name: str = Field(min_length=1)
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: TraceStatus
    duration_ms: float = Field(ge=0)
    error_code: str | None = None
    timestamp: datetime
    details: dict[str, Any] = Field(default_factory=dict)


class FinalInspectionReport(StrictModel):
    inspection_id: str
    run_id: str
    status: str
    diagnosis: DiagnosisReport | None
    risk: RiskAssessment | None
    draft_id: str | None
    approval_decision: ApprovalDecision | None
    work_order_id: str | None
    warnings: list[str]
    errors: list[str]


class InspectionState(TypedDict, total=False):
    """Small serializable state persisted by the LangGraph checkpointer."""

    run_id: str
    inspection_id: str
    scenario_id: str
    asset_id: str
    image_artifact_id: str
    sensor_dataset_id: str
    asset_context: dict[str, Any]
    sensor_relative_path: str
    sensor_sample_interval_seconds: int
    vision_result: dict[str, Any] | None
    sensor_result: dict[str, Any] | None
    failure_mode_candidates: list[dict[str, Any]]
    retrieval_queries: list[str]
    knowledge_evidence: list[dict[str, Any]]
    diagnosis_report: dict[str, Any] | None
    risk_assessment: dict[str, Any] | None
    work_order_draft_id: str | None
    approval_request_id: str | None
    approval_decision: dict[str, Any] | None
    work_order_id: str | None
    evidence_sufficient: bool
    warnings: list[str]
    errors: list[str]
    tool_trace: list[dict[str, Any]]
    final_report: dict[str, Any] | None


def validate_diagnosis_evidence(
    report: DiagnosisReport, available_evidence: list[EvidenceRef]
) -> DiagnosisReport:
    """Reject provider references that were not present in its supplied evidence."""

    available = {item.evidence_id for item in available_evidence}
    referenced = {
        *report.supporting_evidence_ids,
        *report.contradicting_evidence_ids,
    }
    unknown = sorted(referenced - available)
    if unknown:
        raise ValueError(f"diagnosis referenced unavailable evidence IDs: {unknown}")
    return report
