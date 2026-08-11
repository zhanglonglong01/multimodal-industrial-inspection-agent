"""The single Phase 3 LangGraph inspection workflow."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .analysis_schemas import (
    EvidenceKind,
    EvidenceRef,
    FailureMode,
    SensorAnalysisResult,
    Severity,
    VisionAnalysisResult,
    VisionLabel,
)
from .config import Settings
from .repository import SQLiteRepository
from .schemas import Asset, ImageFixtureManifest
from .services.diagnosis import DiagnosisProvider, FixtureDiagnosisProvider
from .services.failure_modes import FailureModeRepository
from .services.knowledge import KnowledgeRetriever
from .services.risk import DeterministicRiskPolicy
from .services.sensors import RuleBasedAndMADDetector
from .services.vision import FixtureVisionProvider, VisionProvider
from .services.work_orders import WorkOrderService
from .workflow_repository import WorkflowRepository
from .workflow_schemas import (
    ApprovalDecision,
    ApprovalDecisionInput,
    DiagnosisProviderInput,
    DiagnosisReport,
    EvidenceStrength,
    FinalInspectionReport,
    InspectionState,
    RiskAssessment,
    RiskInputs,
    RiskLevel,
    SensorResultSummary,
    ToolTrace,
    TraceStatus,
    validate_diagnosis_evidence,
)


def _json_dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _hash_input(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            _json_dump(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _severity_max(values: list[Severity]) -> Severity:
    order = {Severity.INFO: 0, Severity.LOW: 1, Severity.MEDIUM: 2, Severity.HIGH: 3}
    return max(values, key=order.__getitem__) if values else Severity.INFO


def _actual_evidence_strength(evidence: list[EvidenceRef]) -> EvidenceStrength:
    kinds = {item.kind for item in evidence}
    if {EvidenceKind.VISION, EvidenceKind.SENSOR, EvidenceKind.KNOWLEDGE} <= kinds:
        return EvidenceStrength.STRONG
    if EvidenceKind.KNOWLEDGE in kinds and kinds & {
        EvidenceKind.VISION,
        EvidenceKind.SENSOR,
    }:
        return EvidenceStrength.MODERATE
    return EvidenceStrength.WEAK


class InspectionWorkflow:
    """Node implementation kept separate from graph construction for direct tests."""

    def __init__(
        self,
        *,
        settings: Settings,
        vision_provider: VisionProvider,
        sensor_service: RuleBasedAndMADDetector,
        failure_modes: FailureModeRepository,
        retriever: KnowledgeRetriever,
        diagnosis_provider: DiagnosisProvider,
        risk_policy: DeterministicRiskPolicy,
        asset_repository: SQLiteRepository,
        workflow_repository: WorkflowRepository,
    ) -> None:
        self.settings = settings
        self.vision_provider = vision_provider
        self.sensor_service = sensor_service
        self.failure_modes = failure_modes
        self.retriever = retriever
        self.diagnosis_provider = diagnosis_provider
        self.risk_policy = risk_policy
        self.asset_repository = asset_repository
        self.workflow_repository = workflow_repository
        self.work_orders = WorkOrderService(workflow_repository)

    def _finish_trace(
        self,
        state: InspectionState,
        *,
        node_name: str,
        tool_name: str,
        input_summary: Any,
        started: float,
        status: TraceStatus = TraceStatus.SUCCESS,
        error_code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        input_hash = _hash_input(input_summary)
        attempt_index = self.workflow_repository.next_trace_attempt(
            state["run_id"], node_name
        )
        trace = ToolTrace(
            trace_id=f"TRACE-{uuid.uuid4().hex.upper()}",
            run_id=state["run_id"],
            node_name=node_name,
            attempt_index=attempt_index,
            tool_name=tool_name,
            input_hash=input_hash,
            status=status,
            duration_ms=(time.perf_counter() - started) * 1000,
            error_code=error_code,
            timestamp=datetime.now(UTC),
            details=details or {},
        )
        self.workflow_repository.insert_trace(trace)
        return [*state.get("tool_trace", []), trace.model_dump(mode="json")]

    def validate_request(self, state: InspectionState) -> dict[str, Any]:
        started = time.perf_counter()
        required = (
            "run_id",
            "inspection_id",
            "scenario_id",
            "asset_id",
            "image_artifact_id",
            "sensor_dataset_id",
        )
        missing = [key for key in required if not state.get(key)]
        if missing:
            raise ValueError(f"inspection request missing fields: {missing}")
        return {
            "warnings": list(state.get("warnings", [])),
            "errors": list(state.get("errors", [])),
            "tool_trace": self._finish_trace(
                state,
                node_name="validate_request",
                tool_name="pydantic_request_validation",
                input_summary={key: state[key] for key in required},
                started=started,
            ),
        }

    def load_asset_context(self, state: InspectionState) -> dict[str, Any]:
        started = time.perf_counter()
        asset = self.asset_repository.get_asset(state["asset_id"])
        dataset = self.asset_repository.get_sensor_dataset(state["scenario_id"])
        if asset is None:
            raise ValueError(f"asset is not seeded: {state['asset_id']}")
        if dataset is None:
            raise ValueError(f"sensor dataset is not seeded: {state['scenario_id']}")
        if dataset.asset_id != asset.asset_id or dataset.dataset_id != state["sensor_dataset_id"]:
            raise ValueError("asset and sensor dataset context do not match the request")
        return {
            "asset_context": asset.model_dump(mode="json"),
            "sensor_relative_path": dataset.relative_path,
            "sensor_sample_interval_seconds": dataset.sample_interval_seconds,
            "tool_trace": self._finish_trace(
                state,
                node_name="load_asset_context",
                tool_name="sqlite_asset_repository",
                input_summary={"asset_id": asset.asset_id, "dataset_id": dataset.dataset_id},
                started=started,
            ),
        }

    def analyze_image(self, state: InspectionState) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            asset = Asset.model_validate(state["asset_context"])
            result = self.vision_provider.analyze(state["image_artifact_id"], asset)
            return {
                "vision_result": result.model_dump(mode="json"),
                "tool_trace": self._finish_trace(
                    state,
                    node_name="analyze_image",
                    tool_name=type(self.vision_provider).__name__,
                    input_summary={
                        "artifact_id": state["image_artifact_id"],
                        "asset_id": state["asset_id"],
                    },
                    started=started,
                ),
            }
        except Exception as exc:
            warning = f"vision analysis unavailable: {type(exc).__name__}"
            return {
                "vision_result": None,
                "warnings": [*state.get("warnings", []), warning],
                "tool_trace": self._finish_trace(
                    state,
                    node_name="analyze_image",
                    tool_name=type(self.vision_provider).__name__,
                    input_summary={
                        "artifact_id": state["image_artifact_id"],
                        "asset_id": state["asset_id"],
                    },
                    started=started,
                    status=TraceStatus.DEGRADED,
                    error_code=type(exc).__name__,
                ),
            }

    def analyze_sensors(self, state: InspectionState) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            asset = Asset.model_validate(state["asset_context"])
            result: SensorAnalysisResult = self.sensor_service.detect(
                self.settings.data_dir / state["sensor_relative_path"],
                asset,
                state["sensor_dataset_id"],
                expected_sampling_interval_seconds=state[
                    "sensor_sample_interval_seconds"
                ],
            )
            summary = SensorResultSummary(
                dataset_id=result.dataset_id,
                detector=result.detector,
                quality_usable=result.quality.is_usable,
                evaluated_sensor_ids=result.evaluated_sensor_ids,
                segments=result.segments,
                anomaly_point_count=len(result.anomaly_points),
                warnings=result.warnings,
            )
            return {
                "sensor_result": summary.model_dump(mode="json"),
                "warnings": [*state.get("warnings", []), *result.warnings],
                "tool_trace": self._finish_trace(
                    state,
                    node_name="analyze_sensors",
                    tool_name=type(self.sensor_service).__name__,
                    input_summary={
                        "dataset_id": state["sensor_dataset_id"],
                        "asset_id": state["asset_id"],
                    },
                    started=started,
                ),
            }
        except Exception as exc:
            warning = f"sensor analysis unavailable: {type(exc).__name__}"
            return {
                "sensor_result": None,
                "warnings": [*state.get("warnings", []), warning],
                "tool_trace": self._finish_trace(
                    state,
                    node_name="analyze_sensors",
                    tool_name=type(self.sensor_service).__name__,
                    input_summary={
                        "dataset_id": state["sensor_dataset_id"],
                        "asset_id": state["asset_id"],
                    },
                    started=started,
                    status=TraceStatus.DEGRADED,
                    error_code=type(exc).__name__,
                ),
            }

    def evidence_gate(self, state: InspectionState) -> dict[str, Any]:
        started = time.perf_counter()
        vision_usable = False
        if state.get("vision_result"):
            vision_usable = VisionAnalysisResult.model_validate(
                state["vision_result"]
            ).image_quality.usable
        sensor_usable = False
        if state.get("sensor_result"):
            sensor_usable = SensorResultSummary.model_validate(
                state["sensor_result"]
            ).quality_usable
        sufficient = vision_usable or sensor_usable
        errors = list(state.get("errors", []))
        if not sufficient:
            errors.append("INSUFFICIENT_EVIDENCE: vision and sensor analysis are unavailable")
        return {
            "evidence_sufficient": sufficient,
            "errors": errors,
            "tool_trace": self._finish_trace(
                state,
                node_name="evidence_gate",
                tool_name="deterministic_evidence_gate",
                input_summary={
                    "vision_usable": vision_usable,
                    "sensor_usable": sensor_usable,
                },
                started=started,
                status=TraceStatus.SUCCESS if sufficient else TraceStatus.DEGRADED,
                error_code=None if sufficient else "INSUFFICIENT_EVIDENCE",
            ),
        }

    @staticmethod
    def route_evidence(state: InspectionState) -> str:
        return "sufficient" if state.get("evidence_sufficient") else "insufficient"

    def lookup_failure_modes(self, state: InspectionState) -> dict[str, Any]:
        started = time.perf_counter()
        asset = Asset.model_validate(state["asset_context"])
        visual_labels: set[VisionLabel] = set()
        if state.get("vision_result"):
            vision = VisionAnalysisResult.model_validate(state["vision_result"])
            visual_labels = {finding.label for finding in vision.findings}
        anomalous_sensors: set[str] = set()
        if state.get("sensor_result"):
            sensor = SensorResultSummary.model_validate(state["sensor_result"])
            anomalous_sensors = {segment.sensor_id for segment in sensor.segments}

        is_visually_normal = visual_labels == {VisionLabel.NO_VISIBLE_ANOMALY}
        candidates = self.failure_modes.get_failure_modes(asset.asset_type)
        if is_visually_normal and not anomalous_sensors:
            candidates = []
        else:
            candidates = [
                mode
                for mode in candidates
                if visual_labels.intersection(mode.visual_labels)
                or anomalous_sensors.intersection(mode.related_sensors)
            ]
        return {
            "failure_mode_candidates": [
                candidate.model_dump(mode="json") for candidate in candidates
            ],
            "tool_trace": self._finish_trace(
                state,
                node_name="lookup_failure_modes",
                tool_name="FailureModeRepository",
                input_summary={
                    "asset_type": asset.asset_type.value,
                    "visual_labels": sorted(item.value for item in visual_labels),
                    "anomalous_sensors": sorted(anomalous_sensors),
                },
                started=started,
                details={"candidate_ids": [item.mode_id for item in candidates]},
            ),
        }

    def build_retrieval_queries(self, state: InspectionState) -> dict[str, Any]:
        started = time.perf_counter()
        asset = Asset.model_validate(state["asset_context"])
        observed_terms: list[str] = [asset.asset_type.value, "inspection"]
        if state.get("vision_result"):
            vision = VisionAnalysisResult.model_validate(state["vision_result"])
            observed_terms.extend(
                finding.label.value.replace("_", " ") for finding in vision.findings
            )
        if state.get("sensor_result"):
            sensor = SensorResultSummary.model_validate(state["sensor_result"])
            observed_terms.extend(
                f"{segment.sensor_id.replace('_', ' ')} {segment.direction}"
                for segment in sensor.segments
            )
        candidates = [
            FailureMode.model_validate(item)
            for item in state.get("failure_mode_candidates", [])
        ]
        if candidates:
            queries = [
                " ".join([*observed_terms, candidate.name, *candidate.recommended_checks])
                for candidate in candidates
            ]
        else:
            queries = [" ".join([*observed_terms, "normal baseline routine inspection"])]
        return {
            "retrieval_queries": queries,
            "tool_trace": self._finish_trace(
                state,
                node_name="build_retrieval_queries",
                tool_name="deterministic_query_builder",
                input_summary={
                    "observed_terms": observed_terms,
                    "candidate_ids": [item.mode_id for item in candidates],
                },
                started=started,
                details={"queries": queries},
            ),
        }

    def retrieve_knowledge(self, state: InspectionState) -> dict[str, Any]:
        started = time.perf_counter()
        asset = Asset.model_validate(state["asset_context"])
        candidates = state.get("failure_mode_candidates", [])
        results = []
        for query in state.get("retrieval_queries", []):
            current = self.retriever.search(
                query,
                top_k=(3 if candidates else self.retriever.metadata.chunk_count),
                asset_type=asset.asset_type,
            )
            if not candidates:
                current = [
                    item for item in current if item.doc_id == "KNOW-INSPECTION-SOP"
                ][:3]
            results.extend(current)
        evidence_by_id: dict[str, EvidenceRef] = {}
        for result in results:
            chunk = self.retriever.get_chunk(result.chunk_id)
            if chunk is not None:
                evidence = chunk.to_evidence_ref()
                evidence_by_id[evidence.evidence_id] = evidence
        evidence = list(evidence_by_id.values())
        return {
            "knowledge_evidence": [item.model_dump(mode="json") for item in evidence],
            "tool_trace": self._finish_trace(
                state,
                node_name="retrieve_knowledge",
                tool_name="KnowledgeRetriever",
                input_summary={"queries": state.get("retrieval_queries", [])},
                started=started,
                details={"evidence_ids": [item.evidence_id for item in evidence]},
            ),
        }

    @staticmethod
    def _observational_evidence(
        state: InspectionState,
    ) -> tuple[list[EvidenceRef], list[EvidenceRef]]:
        vision_evidence: list[EvidenceRef] = []
        sensor_evidence: list[EvidenceRef] = []
        if state.get("vision_result"):
            vision = VisionAnalysisResult.model_validate(state["vision_result"])
            vision_evidence = [item.to_evidence_ref() for item in vision.findings]
        if state.get("sensor_result"):
            sensor = SensorResultSummary.model_validate(state["sensor_result"])
            sensor_evidence = [item.to_evidence_ref() for item in sensor.segments]
        return vision_evidence, sensor_evidence

    def synthesize_diagnosis(self, state: InspectionState) -> dict[str, Any]:
        started = time.perf_counter()
        vision_evidence, sensor_evidence = self._observational_evidence(state)
        knowledge_evidence = [
            EvidenceRef.model_validate(item)
            for item in state.get("knowledge_evidence", [])
        ]
        request = DiagnosisProviderInput(
            asset_context=Asset.model_validate(state["asset_context"]),
            vision_evidence=vision_evidence,
            sensor_evidence=sensor_evidence,
            failure_mode_candidates=[
                FailureMode.model_validate(item)
                for item in state.get("failure_mode_candidates", [])
            ],
            knowledge_evidence=knowledge_evidence,
        )
        report = self.diagnosis_provider.diagnose(request)
        report = report.model_copy(
            update={
                "diagnosis_id": (
                    "DIAG-"
                    + hashlib.sha256(state["inspection_id"].encode("utf-8"))
                    .hexdigest()[:20]
                    .upper()
                )
            }
        )
        validate_diagnosis_evidence(
            report, [*vision_evidence, *sensor_evidence, *knowledge_evidence]
        )
        return {
            "diagnosis_report": report.model_dump(mode="json"),
            "tool_trace": self._finish_trace(
                state,
                node_name="synthesize_diagnosis",
                tool_name=type(self.diagnosis_provider).__name__,
                input_summary={
                    "evidence_ids": [
                        item.evidence_id
                        for item in [
                            *vision_evidence,
                            *sensor_evidence,
                            *knowledge_evidence,
                        ]
                    ],
                    "candidate_ids": [
                        item.mode_id for item in request.failure_mode_candidates
                    ],
                },
                started=started,
            ),
        }

    def apply_risk_policy(self, state: InspectionState) -> dict[str, Any]:
        started = time.perf_counter()
        asset = Asset.model_validate(state["asset_context"])
        diagnosis = DiagnosisReport.model_validate(state["diagnosis_report"])
        candidates = {
            item.mode_id: item
            for item in (
                FailureMode.model_validate(raw)
                for raw in state.get("failure_mode_candidates", [])
            )
        }
        primary = candidates.get(diagnosis.primary_fault_candidate or "")
        fault_severity = primary.fault_severity if primary else Severity.INFO
        sensor_severities: list[Severity] = []
        if state.get("sensor_result"):
            sensor = SensorResultSummary.model_validate(state["sensor_result"])
            sensor_severities = [segment.severity for segment in sensor.segments]
        vision_evidence, sensor_evidence = self._observational_evidence(state)
        knowledge = [
            EvidenceRef.model_validate(item)
            for item in state.get("knowledge_evidence", [])
        ]
        inputs = RiskInputs(
            fault_severity=fault_severity,
            evidence_strength=_actual_evidence_strength(
                [*vision_evidence, *sensor_evidence, *knowledge]
            ),
            asset_criticality=asset.criticality,
            sensor_severity=_severity_max(sensor_severities),
        )
        risk = self.risk_policy.assess(inputs)
        return {
            "risk_assessment": risk.model_dump(mode="json"),
            "tool_trace": self._finish_trace(
                state,
                node_name="apply_risk_policy",
                tool_name=type(self.risk_policy).__name__,
                input_summary=inputs,
                started=started,
                details={"risk_level": risk.risk_level.value},
            ),
        }

    def draft_work_order(self, state: InspectionState) -> dict[str, Any]:
        started = time.perf_counter()
        diagnosis = DiagnosisReport.model_validate(state["diagnosis_report"])
        risk = RiskAssessment.model_validate(state["risk_assessment"])
        draft = self.work_orders.create_draft(
            inspection_id=state["inspection_id"],
            asset_id=state["asset_id"],
            diagnosis_id=diagnosis.diagnosis_id,
            risk=risk,
            title=(
                f"Inspection action: {diagnosis.primary_fault_candidate}"
                if diagnosis.primary_fault_candidate
                else "Inspection monitoring recommendation"
            ),
            description=diagnosis.explanation,
            summary=diagnosis.explanation,
            recommended_actions=diagnosis.recommended_actions,
            evidence_ids=diagnosis.supporting_evidence_ids,
        )
        approval_id: str | None = None
        if risk.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
            approval_id = self.work_orders.request_approval(draft).approval_id
        return {
            "work_order_draft_id": draft.draft_id,
            "approval_request_id": approval_id,
            "tool_trace": self._finish_trace(
                state,
                node_name="draft_work_order",
                tool_name="WorkOrderService.create_draft",
                input_summary={
                    "inspection_id": state["inspection_id"],
                    "diagnosis_id": diagnosis.diagnosis_id,
                    "risk_level": risk.risk_level.value,
                },
                started=started,
                details={"draft_id": draft.draft_id, "approval_id": approval_id},
            ),
        }

    @staticmethod
    def route_actionability(state: InspectionState) -> str:
        diagnosis = DiagnosisReport.model_validate(state["diagnosis_report"])
        return (
            "draft"
            if diagnosis.primary_fault_candidate and diagnosis.actionable
            else "finalize"
        )

    @staticmethod
    def route_risk(state: InspectionState) -> str:
        risk = RiskAssessment.model_validate(state["risk_assessment"])
        return (
            "approval"
            if risk.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
            else "finalize"
        )

    def approval_gate(self, state: InspectionState) -> dict[str, Any]:
        """No side effect occurs before interrupt, because this node re-runs on resume."""

        started = time.perf_counter()
        draft = self.workflow_repository.get_draft(state["work_order_draft_id"])
        if draft is None or state.get("approval_request_id") is None:
            raise ValueError("approval gate requires a persistent draft and approval request")
        payload = {
            "approval_id": state["approval_request_id"],
            "draft_id": draft.draft_id,
            "risk_level": draft.risk_level.value,
            "summary": draft.summary,
            "recommended_actions": draft.recommended_actions,
        }
        resumed = interrupt(payload)
        decision = ApprovalDecisionInput.model_validate(resumed)
        return {
            "approval_decision": decision.model_dump(mode="json"),
            "tool_trace": self._finish_trace(
                state,
                node_name="approval_gate",
                tool_name="langgraph.interrupt",
                input_summary={
                    "approval_id": payload["approval_id"],
                    "draft_id": payload["draft_id"],
                    "risk_level": payload["risk_level"],
                },
                started=started,
            ),
        }

    def validate_approval(self, state: InspectionState) -> dict[str, Any]:
        started = time.perf_counter()
        decision = ApprovalDecisionInput.model_validate(state["approval_decision"])
        approval = self.work_orders.record_decision(
            state["approval_request_id"], decision
        )
        return {
            "approval_decision": decision.model_dump(mode="json"),
            "tool_trace": self._finish_trace(
                state,
                node_name="validate_approval",
                tool_name="WorkOrderService.record_decision",
                input_summary={
                    "approval_id": approval.approval_id,
                    "decision": approval.decision.value,
                },
                started=started,
            ),
        }

    @staticmethod
    def route_approval(state: InspectionState) -> str:
        decision = ApprovalDecisionInput.model_validate(state["approval_decision"])
        return "create" if decision.decision is ApprovalDecision.APPROVE else "finalize"

    def create_work_order(self, state: InspectionState) -> dict[str, Any]:
        started = time.perf_counter()
        work_order = self.work_orders.create_work_order(
            draft_id=state["work_order_draft_id"],
            approval_id=state.get("approval_request_id"),
        )
        return {
            "work_order_id": work_order.work_order_id,
            "tool_trace": self._finish_trace(
                state,
                node_name="create_work_order",
                tool_name="WorkOrderService.create_work_order",
                input_summary={
                    "draft_id": state["work_order_draft_id"],
                    "approval_id": state.get("approval_request_id"),
                },
                started=started,
                details={"work_order_id": work_order.work_order_id},
            ),
        }

    def finalize_report(self, state: InspectionState) -> dict[str, Any]:
        started = time.perf_counter()
        diagnosis = (
            DiagnosisReport.model_validate(state["diagnosis_report"])
            if state.get("diagnosis_report")
            else None
        )
        risk = (
            RiskAssessment.model_validate(state["risk_assessment"])
            if state.get("risk_assessment")
            else None
        )
        decision = None
        if state.get("approval_decision"):
            decision = ApprovalDecisionInput.model_validate(
                state["approval_decision"]
            ).decision
        if not state.get("evidence_sufficient", True):
            status = "INSUFFICIENT_EVIDENCE"
        elif state.get("work_order_id"):
            status = "WORK_ORDER_CREATED"
        elif decision is ApprovalDecision.REJECT:
            status = "APPROVAL_REJECTED"
        elif decision is ApprovalDecision.REQUEST_CHANGES:
            status = "CHANGES_REQUESTED"
        else:
            status = "DIAGNOSED_NO_WORK_ORDER"
        report = FinalInspectionReport(
            inspection_id=state["inspection_id"],
            run_id=state["run_id"],
            status=status,
            diagnosis=diagnosis,
            risk=risk,
            draft_id=state.get("work_order_draft_id"),
            approval_decision=decision,
            work_order_id=state.get("work_order_id"),
            warnings=state.get("warnings", []),
            errors=state.get("errors", []),
        )
        return {
            "final_report": report.model_dump(mode="json"),
            "tool_trace": self._finish_trace(
                state,
                node_name="finalize_report",
                tool_name="structured_report_builder",
                input_summary={
                    "inspection_id": state["inspection_id"],
                    "status": status,
                },
                started=started,
            ),
        }


def build_graph(workflow: InspectionWorkflow, checkpointer: SqliteSaver):
    builder = StateGraph(InspectionState)
    builder.add_node("validate_request", workflow.validate_request)
    builder.add_node("load_asset_context", workflow.load_asset_context)
    builder.add_node("analyze_image", workflow.analyze_image)
    builder.add_node("analyze_sensors", workflow.analyze_sensors)
    builder.add_node("evidence_gate", workflow.evidence_gate)
    builder.add_node("lookup_failure_modes", workflow.lookup_failure_modes)
    builder.add_node("build_retrieval_queries", workflow.build_retrieval_queries)
    builder.add_node("retrieve_knowledge", workflow.retrieve_knowledge)
    builder.add_node("synthesize_diagnosis", workflow.synthesize_diagnosis)
    builder.add_node("apply_risk_policy", workflow.apply_risk_policy)
    builder.add_node("draft_work_order", workflow.draft_work_order)
    builder.add_node("approval_gate", workflow.approval_gate)
    builder.add_node("validate_approval", workflow.validate_approval)
    builder.add_node("create_work_order", workflow.create_work_order)
    builder.add_node("finalize_report", workflow.finalize_report)

    builder.add_edge(START, "validate_request")
    builder.add_edge("validate_request", "load_asset_context")
    builder.add_edge("load_asset_context", "analyze_image")
    builder.add_edge("analyze_image", "analyze_sensors")
    builder.add_edge("analyze_sensors", "evidence_gate")
    builder.add_conditional_edges(
        "evidence_gate",
        workflow.route_evidence,
        {"sufficient": "lookup_failure_modes", "insufficient": "finalize_report"},
    )
    builder.add_edge("lookup_failure_modes", "build_retrieval_queries")
    builder.add_edge("build_retrieval_queries", "retrieve_knowledge")
    builder.add_edge("retrieve_knowledge", "synthesize_diagnosis")
    builder.add_edge("synthesize_diagnosis", "apply_risk_policy")
    builder.add_conditional_edges(
        "apply_risk_policy",
        workflow.route_actionability,
        {"draft": "draft_work_order", "finalize": "finalize_report"},
    )
    builder.add_conditional_edges(
        "draft_work_order",
        workflow.route_risk,
        {"approval": "approval_gate", "finalize": "finalize_report"},
    )
    builder.add_edge("approval_gate", "validate_approval")
    builder.add_conditional_edges(
        "validate_approval",
        workflow.route_approval,
        {"create": "create_work_order", "finalize": "finalize_report"},
    )
    builder.add_edge("create_work_order", "finalize_report")
    builder.add_edge("finalize_report", END)
    return builder.compile(checkpointer=checkpointer, name="industrial_inspection")


def build_initial_state(
    settings: Settings,
    scenario_id: str,
    *,
    run_id: str | None = None,
    inspection_id: str | None = None,
) -> InspectionState:
    """Build request metadata from seeded DB/image registry, never a GT manifest."""

    if settings.database_path is None:
        raise ValueError("database path is required to build an inspection request")
    dataset = SQLiteRepository(settings.database_path).get_sensor_dataset(scenario_id)
    if dataset is None:
        raise ValueError(f"scenario is not seeded: {scenario_id}")
    fixture_manifest = ImageFixtureManifest.model_validate_json(
        settings.image_manifest_path.read_text(encoding="utf-8")
    )
    fixture = next(
        (item for item in fixture_manifest.fixtures if item.scenario_id == scenario_id),
        None,
    )
    if fixture is None:
        raise ValueError(f"scenario has no registered image fixture: {scenario_id}")
    if fixture.asset_id != dataset.asset_id:
        raise ValueError("image fixture and sensor dataset belong to different assets")
    suffix = uuid.uuid4().hex.upper()
    return InspectionState(
        run_id=run_id or f"RUN-{suffix}",
        inspection_id=inspection_id or f"INSPECTION-{suffix}",
        scenario_id=scenario_id,
        asset_id=dataset.asset_id,
        image_artifact_id=fixture.fixture_id,
        sensor_dataset_id=dataset.dataset_id,
        vision_result=None,
        sensor_result=None,
        failure_mode_candidates=[],
        knowledge_evidence=[],
        diagnosis_report=None,
        risk_assessment=None,
        work_order_draft_id=None,
        approval_request_id=None,
        approval_decision=None,
        work_order_id=None,
        warnings=[],
        errors=[],
        tool_trace=[],
        final_report=None,
    )


def get_interrupt_payload(result: Mapping[str, Any]) -> dict[str, Any] | None:
    interrupts = result.get("__interrupt__", ())
    if not interrupts:
        return None
    return dict(interrupts[0].value)


class WorkflowRuntime:
    """Own one compiled graph and a durable synchronous SQLite checkpointer."""

    def __init__(
        self,
        settings: Settings,
        *,
        vision_provider: VisionProvider | None = None,
        sensor_service: RuleBasedAndMADDetector | None = None,
        diagnosis_provider: DiagnosisProvider | None = None,
        retriever: KnowledgeRetriever | None = None,
    ) -> None:
        settings.ensure_directories()
        assert settings.database_path is not None
        assert settings.checkpoint_path is not None
        self.settings = settings
        self.asset_repository = SQLiteRepository(settings.database_path)
        self.workflow_repository = WorkflowRepository(settings.database_path)
        self.workflow_repository.initialize_schema()
        self._checkpoint_connection = sqlite3.connect(
            settings.checkpoint_path, check_same_thread=False
        )
        self.checkpointer = SqliteSaver(self._checkpoint_connection)
        self.workflow = InspectionWorkflow(
            settings=settings,
            vision_provider=vision_provider or FixtureVisionProvider(settings),
            sensor_service=sensor_service or RuleBasedAndMADDetector(),
            failure_modes=FailureModeRepository(settings.failure_modes_path),
            retriever=retriever or KnowledgeRetriever(settings.knowledge_index_dir),
            diagnosis_provider=diagnosis_provider or FixtureDiagnosisProvider(),
            risk_policy=DeterministicRiskPolicy(),
            asset_repository=self.asset_repository,
            workflow_repository=self.workflow_repository,
        )
        self.graph = build_graph(self.workflow, self.checkpointer)

    @staticmethod
    def _config(run_id: str) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": run_id}}

    def invoke(self, state: InspectionState) -> dict[str, Any]:
        return self.graph.invoke(state, config=self._config(state["run_id"]))

    def resume(
        self, run_id: str, decision: ApprovalDecisionInput | dict[str, Any]
    ) -> dict[str, Any]:
        payload = _json_dump(decision)
        return self.graph.invoke(
            Command(resume=payload), config=self._config(run_id)
        )

    def get_state(self, run_id: str) -> dict[str, Any]:
        snapshot = self.graph.get_state(self._config(run_id))
        return dict(snapshot.values)

    def close(self) -> None:
        self._checkpoint_connection.close()

    def __enter__(self) -> "WorkflowRuntime":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
