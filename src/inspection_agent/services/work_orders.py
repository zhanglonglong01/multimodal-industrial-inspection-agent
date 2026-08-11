"""Protected work-order service with approval, integrity, and idempotency checks."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from ..workflow_repository import WorkflowRepository
from ..workflow_schemas import (
    ApprovalDecision,
    ApprovalDecisionInput,
    ApprovalRequest,
    DraftStatus,
    RiskAssessment,
    RiskLevel,
    WorkOrder,
    WorkOrderDraft,
)


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:20].upper()
    return f"{prefix}-{digest}"


def draft_content_hash(
    *,
    inspection_id: str,
    asset_id: str,
    diagnosis_id: str,
    risk_level: RiskLevel,
    title: str,
    description: str,
    priority: RiskLevel,
    summary: str,
    recommended_actions: list[str],
    evidence_ids: list[str],
) -> str:
    payload = {
        "inspection_id": inspection_id,
        "asset_id": asset_id,
        "diagnosis_id": diagnosis_id,
        "risk_level": risk_level.value,
        "title": title,
        "description": description,
        "priority": priority.value,
        "summary": summary,
        "recommended_actions": recommended_actions,
        "evidence_ids": evidence_ids,
    }
    return hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


class WorkOrderService:
    def __init__(self, repository: WorkflowRepository) -> None:
        self.repository = repository

    def create_draft(
        self,
        *,
        inspection_id: str,
        asset_id: str,
        diagnosis_id: str,
        risk: RiskAssessment,
        title: str,
        description: str,
        summary: str,
        recommended_actions: list[str],
        evidence_ids: list[str],
    ) -> WorkOrderDraft:
        content_hash = draft_content_hash(
            inspection_id=inspection_id,
            asset_id=asset_id,
            diagnosis_id=diagnosis_id,
            risk_level=risk.risk_level,
            title=title,
            description=description,
            priority=risk.risk_level,
            summary=summary,
            recommended_actions=recommended_actions,
            evidence_ids=evidence_ids,
        )
        draft = WorkOrderDraft(
            draft_id=_stable_id("DRAFT", inspection_id),
            inspection_id=inspection_id,
            asset_id=asset_id,
            diagnosis_id=diagnosis_id,
            risk_level=risk.risk_level,
            title=title,
            description=description,
            priority=risk.risk_level,
            summary=summary,
            recommended_actions=recommended_actions,
            evidence_ids=evidence_ids,
            content_hash=content_hash,
            status=DraftStatus.PENDING,
            created_at=datetime.now(UTC),
        )
        stored = self.repository.insert_draft(draft)
        if stored.content_hash != content_hash:
            raise ValueError("an immutable draft already exists with different content")
        return stored

    def request_approval(self, draft: WorkOrderDraft) -> ApprovalRequest:
        if draft.risk_level not in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
            raise ValueError("approval is reserved for HIGH or CRITICAL drafts")
        approval = ApprovalRequest(
            approval_id=_stable_id("APPROVAL", draft.draft_id),
            draft_id=draft.draft_id,
            draft_hash=draft.content_hash,
            risk_level=draft.risk_level,
            created_at=datetime.now(UTC),
        )
        stored = self.repository.insert_approval(approval)
        if stored.draft_hash != draft.content_hash:
            raise ValueError("approval references a different immutable draft hash")
        return stored

    def record_decision(
        self, approval_id: str, decision_input: ApprovalDecisionInput
    ) -> ApprovalRequest:
        current = self.repository.get_approval(approval_id)
        if current is None:
            raise KeyError(f"approval request not found: {approval_id}")
        decided = current.model_copy(
            update={
                "decision": decision_input.decision,
                "reviewer": decision_input.reviewer,
                "reason": decision_input.comment,
                "decided_at": datetime.now(UTC),
            }
        )
        saved = self.repository.decide_approval(decided)
        status = {
            ApprovalDecision.APPROVE: DraftStatus.APPROVED,
            ApprovalDecision.REJECT: DraftStatus.REJECTED,
            ApprovalDecision.REQUEST_CHANGES: DraftStatus.CHANGES_REQUESTED,
        }[saved.decision]
        self.repository.set_draft_status(saved.draft_id, status)
        return saved

    def create_work_order(
        self, *, draft_id: str, approval_id: str | None
    ) -> WorkOrder:
        """Re-check every authorization condition at the side-effect boundary."""

        draft = self.repository.get_draft(draft_id)
        if draft is None:
            raise KeyError(f"work-order draft not found: {draft_id}")
        expected_hash = draft_content_hash(
            inspection_id=draft.inspection_id,
            asset_id=draft.asset_id,
            diagnosis_id=draft.diagnosis_id,
            risk_level=draft.risk_level,
            title=draft.title,
            description=draft.description,
            priority=draft.priority,
            summary=draft.summary,
            recommended_actions=draft.recommended_actions,
            evidence_ids=draft.evidence_ids,
        )
        if expected_hash != draft.content_hash:
            raise ValueError("draft integrity check failed")

        existing = self.repository.get_work_order_for_draft(draft_id)
        if existing is not None:
            return existing

        approval: ApprovalRequest | None = None
        if draft.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
            if approval_id is None:
                raise PermissionError("high-risk work order requires approval")
            approval = self.repository.get_approval(approval_id)
            if approval is None:
                raise PermissionError("approval request does not exist")
            if approval.draft_id != draft.draft_id:
                raise PermissionError("approval belongs to another draft")
            if approval.draft_hash != draft.content_hash:
                raise PermissionError("approval does not authorize this draft content")
            if approval.risk_level is not draft.risk_level:
                raise PermissionError("approval risk level does not match the draft")
            if approval.decision is not ApprovalDecision.APPROVE:
                raise PermissionError("approval decision is not APPROVE")
            if draft.status is not DraftStatus.APPROVED:
                raise PermissionError("draft is not in APPROVED status")

        work_order = WorkOrder(
            work_order_id=_stable_id("WO", draft.draft_id),
            draft_id=draft.draft_id,
            approval_id=approval.approval_id if approval else None,
            asset_id=draft.asset_id,
            risk_level=draft.risk_level,
            summary=draft.summary,
            recommended_actions=draft.recommended_actions,
            idempotency_key=f"work-order:{draft.draft_id}",
            created_at=datetime.now(UTC),
        )
        stored = self.repository.insert_work_order(work_order)
        self.repository.set_draft_status(draft.draft_id, DraftStatus.ISSUED)
        return stored
