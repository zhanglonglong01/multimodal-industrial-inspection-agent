from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from inspection_agent.analysis_schemas import Severity
from inspection_agent.schemas import Criticality
from inspection_agent.services.risk import DeterministicRiskPolicy
from inspection_agent.services.work_orders import WorkOrderService
from inspection_agent.workflow_repository import WorkflowRepository
from inspection_agent.workflow_schemas import (
    ApprovalDecision,
    ApprovalDecisionInput,
    EvidenceStrength,
    RiskInputs,
    RiskLevel,
)


@pytest.mark.parametrize(
    ("severity", "strength", "criticality", "expected"),
    [
        (Severity.INFO, EvidenceStrength.MODERATE, Criticality.HIGH, RiskLevel.LOW),
        (Severity.HIGH, EvidenceStrength.STRONG, Criticality.MEDIUM, RiskLevel.HIGH),
        (Severity.HIGH, EvidenceStrength.MODERATE, Criticality.HIGH, RiskLevel.HIGH),
        (Severity.HIGH, EvidenceStrength.WEAK, Criticality.HIGH, RiskLevel.MEDIUM),
    ],
)
def test_deterministic_risk_policy(
    severity: Severity,
    strength: EvidenceStrength,
    criticality: Criticality,
    expected: RiskLevel,
) -> None:
    result = DeterministicRiskPolicy().assess(
        RiskInputs(
            fault_severity=severity,
            evidence_strength=strength,
            asset_criticality=criticality,
            sensor_severity=severity,
        )
    )
    assert result.risk_level is expected
    assert result.policy_version == "risk-policy-1.0"


@pytest.fixture
def work_order_service(tmp_path: Path) -> WorkOrderService:
    repository = WorkflowRepository(tmp_path / "workflow.db")
    repository.initialize_schema()
    return WorkOrderService(repository)


def _risk(level: RiskLevel):
    assessment = DeterministicRiskPolicy().assess(
        RiskInputs(
            fault_severity=Severity.HIGH,
            evidence_strength=EvidenceStrength.STRONG,
            asset_criticality=Criticality.MEDIUM,
            sensor_severity=Severity.HIGH,
        )
    )
    return assessment.model_copy(update={"risk_level": level})


def _draft(service: WorkOrderService, inspection_id: str = "INSPECTION-001"):
    return service.create_draft(
        inspection_id=inspection_id,
        asset_id="PUMP-001",
        diagnosis_id="DIAG-001",
        risk=_risk(RiskLevel.HIGH),
        title="Synthetic inspection task",
        description="Persistent draft description",
        summary="Persistent draft summary",
        recommended_actions=["Inspect the synthetic fixture."],
        evidence_ids=["EVIDENCE-VISION-FINDING-001"],
    )


def test_draft_content_hash_is_immutable(work_order_service: WorkOrderService) -> None:
    first = _draft(work_order_service)
    assert len(first.content_hash) == 64
    with pytest.raises(ValueError, match="different content"):
        work_order_service.create_draft(
            inspection_id=first.inspection_id,
            asset_id=first.asset_id,
            diagnosis_id=first.diagnosis_id,
            risk=_risk(RiskLevel.HIGH),
            title=first.title,
            description=first.description,
            summary="changed summary",
            recommended_actions=first.recommended_actions,
            evidence_ids=first.evidence_ids,
        )


def test_high_risk_direct_creation_cannot_bypass_approval(
    work_order_service: WorkOrderService,
) -> None:
    draft = _draft(work_order_service)
    with pytest.raises(PermissionError, match="requires approval"):
        work_order_service.create_work_order(draft_id=draft.draft_id, approval_id=None)


@pytest.mark.parametrize(
    "decision",
    [ApprovalDecision.REJECT, ApprovalDecision.REQUEST_CHANGES],
)
def test_non_approved_decisions_never_authorize_creation(
    work_order_service: WorkOrderService, decision: ApprovalDecision
) -> None:
    draft = _draft(work_order_service)
    approval = work_order_service.request_approval(draft)
    work_order_service.record_decision(
        approval.approval_id, ApprovalDecisionInput(decision=decision)
    )
    with pytest.raises(PermissionError, match="not APPROVE"):
        work_order_service.create_work_order(
            draft_id=draft.draft_id, approval_id=approval.approval_id
        )


def test_approved_creation_is_idempotent(work_order_service: WorkOrderService) -> None:
    draft = _draft(work_order_service)
    approval = work_order_service.request_approval(draft)
    saved_approval = work_order_service.record_decision(
        approval.approval_id,
        ApprovalDecisionInput(
            decision=ApprovalDecision.APPROVE,
            reviewer="reviewer-001",
            comment="validated synthetic evidence",
        ),
    )
    first = work_order_service.create_work_order(
        draft_id=draft.draft_id, approval_id=approval.approval_id
    )
    second = work_order_service.create_work_order(
        draft_id=draft.draft_id, approval_id=approval.approval_id
    )
    assert first.work_order_id == second.work_order_id
    assert first.idempotency_key == f"work-order:{draft.draft_id}"
    assert saved_approval.reviewer == "reviewer-001"
    assert saved_approval.reason == "validated synthetic evidence"
    assert work_order_service.repository.count_work_orders(draft.draft_id) == 1


def test_tampered_draft_hash_blocks_creation(
    work_order_service: WorkOrderService,
) -> None:
    draft = _draft(work_order_service)
    approval = work_order_service.request_approval(draft)
    work_order_service.record_decision(
        approval.approval_id,
        ApprovalDecisionInput(decision=ApprovalDecision.APPROVE),
    )
    with sqlite3.connect(work_order_service.repository.database_path) as connection:
        connection.execute(
            "UPDATE work_order_drafts SET summary = ? WHERE draft_id = ?",
            ("tampered", draft.draft_id),
        )
    with pytest.raises(ValueError, match="integrity"):
        work_order_service.create_work_order(
            draft_id=draft.draft_id, approval_id=approval.approval_id
        )
