from __future__ import annotations

from datetime import UTC, datetime

import pytest

from inspection_agent.analysis_schemas import EvidenceKind, EvidenceRef
from inspection_agent.workflow import InspectionWorkflow
from inspection_agent.workflow_schemas import (
    DiagnosisReport,
    EvidenceStrength,
    InspectionState,
    validate_diagnosis_evidence,
)


def test_inspection_state_declares_required_checkpoint_fields() -> None:
    required = {
        "run_id",
        "inspection_id",
        "scenario_id",
        "asset_id",
        "image_artifact_id",
        "sensor_dataset_id",
        "vision_result",
        "sensor_result",
        "failure_mode_candidates",
        "knowledge_evidence",
        "diagnosis_report",
        "risk_assessment",
        "work_order_draft_id",
        "approval_request_id",
        "approval_decision",
        "work_order_id",
        "warnings",
        "errors",
        "tool_trace",
    }
    assert required <= InspectionState.__annotations__.keys()


def test_diagnosis_rejects_hallucinated_evidence_reference() -> None:
    evidence = EvidenceRef(
        evidence_id="EVIDENCE-VISION-FINDING-001",
        kind=EvidenceKind.VISION,
        source_id="FINDING-001",
        summary="visible observation",
        observed_at=datetime.now(UTC),
    )
    report = DiagnosisReport(
        diagnosis_id="DIAG-001",
        primary_fault_candidate=None,
        actionable=False,
        supporting_evidence_ids=["EVIDENCE-SENSOR-NOT-AVAILABLE"],
        evidence_strength=EvidenceStrength.WEAK,
        explanation="Unsupported result for validation test.",
        provider="test",
        fixture=True,
    )
    with pytest.raises(ValueError, match="unavailable evidence"):
        validate_diagnosis_evidence(report, [evidence])


@pytest.mark.parametrize(
    ("sufficient", "expected"), [(True, "sufficient"), (False, "insufficient")]
)
def test_evidence_gate_routing(sufficient: bool, expected: str) -> None:
    assert InspectionWorkflow.route_evidence({"evidence_sufficient": sufficient}) == expected


def test_fault_candidate_remains_draft_eligible_independent_of_low_risk() -> None:
    report = DiagnosisReport(
        diagnosis_id="DIAG-ACTIONABLE-001",
        primary_fault_candidate="actionable_fault",
        actionable=True,
        evidence_strength=EvidenceStrength.MODERATE,
        explanation="A real candidate remains eligible for a draft.",
        provider="test",
        fixture=True,
    )
    assert (
        InspectionWorkflow.route_actionability(
            {"diagnosis_report": report.model_dump(mode="json")}
        )
        == "draft"
    )
