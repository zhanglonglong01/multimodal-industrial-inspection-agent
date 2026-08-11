from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from inspection_agent.services.diagnosis import FixtureDiagnosisProvider
from inspection_agent.workflow import (
    WorkflowRuntime,
    build_initial_state,
    get_interrupt_payload,
)
from inspection_agent.workflow_schemas import (
    ApprovalDecision,
    ApprovalDecisionInput,
    DiagnosisProviderInput,
    DiagnosisReport,
    EvidenceStrength,
)


class FailingVisionProvider:
    provider_name = "failing_vision"

    def analyze(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError("injected vision outage")


class FailingSensorService:
    def detect(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError("injected sensor outage")


class CapturingDiagnosisProvider(FixtureDiagnosisProvider):
    def __init__(self) -> None:
        self.request: DiagnosisProviderInput | None = None

    def diagnose(self, request: DiagnosisProviderInput) -> DiagnosisReport:
        self.request = request
        return super().diagnose(request)


class HallucinatingDiagnosisProvider(FixtureDiagnosisProvider):
    def diagnose(self, request: DiagnosisProviderInput) -> DiagnosisReport:
        valid = super().diagnose(request)
        return valid.model_copy(
            update={
                "supporting_evidence_ids": ["EVIDENCE-SENSOR-HALLUCINATED-001"],
                "evidence_strength": EvidenceStrength.STRONG,
            }
        )


def _state(settings: Any, scenario_id: str, name: str):
    return build_initial_state(
        settings,
        scenario_id,
        run_id=f"RUN-TEST-{name}",
        inspection_id=f"INSPECTION-TEST-{name}",
    )


def test_normal_end_to_end_has_no_work_order(
    seeded_demo: Any, knowledge_retriever: Any
) -> None:
    with WorkflowRuntime(seeded_demo, retriever=knowledge_retriever) as runtime:
        result = runtime.invoke(_state(seeded_demo, "SCENARIO-003", "NORMAL"))
    assert get_interrupt_payload(result) is None
    assert result["final_report"]["status"] == "DIAGNOSED_NO_WORK_ORDER"
    assert result["risk_assessment"]["risk_level"] == "LOW"
    assert result["work_order_draft_id"] is None
    assert result["approval_request_id"] is None
    assert result["work_order_id"] is None
    assert "does not sufficiently support an actionable maintenance fault" in result[
        "final_report"
    ]["diagnosis"]["explanation"]
    assert "anomaly_points" not in result["sensor_result"]


def test_high_risk_interrupt_payload_and_reject(
    seeded_demo: Any, knowledge_retriever: Any
) -> None:
    with WorkflowRuntime(seeded_demo, retriever=knowledge_retriever) as runtime:
        state = _state(seeded_demo, "SCENARIO-001", "REJECT")
        result = runtime.invoke(state)
        payload = get_interrupt_payload(result)
        assert payload is not None
        assert set(payload) == {
            "approval_id",
            "draft_id",
            "risk_level",
            "summary",
            "recommended_actions",
        }
        result = runtime.resume(
            state["run_id"],
            ApprovalDecisionInput(decision=ApprovalDecision.REJECT, comment="unsafe"),
        )
        assert runtime.workflow_repository.count_work_orders() == 0
    assert result["final_report"]["status"] == "APPROVAL_REJECTED"


def test_high_risk_approve_creates_exactly_one_work_order(
    seeded_demo: Any, knowledge_retriever: Any
) -> None:
    with WorkflowRuntime(seeded_demo, retriever=knowledge_retriever) as runtime:
        state = _state(seeded_demo, "SCENARIO-001", "APPROVE")
        first = runtime.invoke(state)
        assert get_interrupt_payload(first) is not None
        result = runtime.resume(
            state["run_id"],
            ApprovalDecisionInput(decision=ApprovalDecision.APPROVE),
        )
        duplicate = runtime.workflow.work_orders.create_work_order(
            draft_id=result["work_order_draft_id"],
            approval_id=result["approval_request_id"],
        )
        assert duplicate.work_order_id == result["work_order_id"]
        replayed = runtime.resume(
            state["run_id"],
            ApprovalDecisionInput(decision=ApprovalDecision.APPROVE),
        )
        assert replayed["work_order_id"] == result["work_order_id"]
        assert runtime.workflow_repository.count_work_orders(
            result["work_order_draft_id"]
        ) == 1


def test_resume_after_runtime_rebuild_uses_persistent_sqlite_checkpoint(
    seeded_demo: Any, knowledge_retriever: Any
) -> None:
    state = _state(seeded_demo, "SCENARIO-002", "REBUILD")
    first_runtime = WorkflowRuntime(seeded_demo, retriever=knowledge_retriever)
    interrupted = first_runtime.invoke(state)
    assert get_interrupt_payload(interrupted) is not None
    first_runtime.close()

    with WorkflowRuntime(seeded_demo, retriever=knowledge_retriever) as rebuilt:
        result = rebuilt.resume(
            state["run_id"],
            ApprovalDecisionInput(decision=ApprovalDecision.APPROVE),
        )
    assert result["final_report"]["status"] == "WORK_ORDER_CREATED"
    with sqlite3.connect(seeded_demo.checkpoint_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "checkpoints" in tables


@pytest.mark.parametrize(
    ("vision", "sensor", "warning_fragment"),
    [
        (FailingVisionProvider(), None, "vision analysis unavailable"),
        (None, FailingSensorService(), "sensor analysis unavailable"),
    ],
)
def test_single_modality_failure_degrades_but_continues(
    seeded_demo: Any,
    knowledge_retriever: Any,
    vision: Any,
    sensor: Any,
    warning_fragment: str,
) -> None:
    with WorkflowRuntime(
        seeded_demo,
        retriever=knowledge_retriever,
        vision_provider=vision,
        sensor_service=sensor,
    ) as runtime:
        state = _state(seeded_demo, "SCENARIO-001", warning_fragment.split()[0].upper())
        result = runtime.invoke(state)
    assert result["evidence_sufficient"] is True
    assert get_interrupt_payload(result) is not None
    assert any(warning_fragment in item for item in result["warnings"])


def test_dual_modality_failure_finishes_insufficient(
    seeded_demo: Any, knowledge_retriever: Any
) -> None:
    with WorkflowRuntime(
        seeded_demo,
        retriever=knowledge_retriever,
        vision_provider=FailingVisionProvider(),
        sensor_service=FailingSensorService(),
    ) as runtime:
        result = runtime.invoke(_state(seeded_demo, "SCENARIO-001", "DUAL"))
    assert result["final_report"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert result["diagnosis_report"] is None
    assert result["work_order_draft_id"] is None


def test_provider_hallucinated_evidence_is_rejected(
    seeded_demo: Any, knowledge_retriever: Any
) -> None:
    with WorkflowRuntime(
        seeded_demo,
        retriever=knowledge_retriever,
        diagnosis_provider=HallucinatingDiagnosisProvider(),
    ) as runtime:
        with pytest.raises(ValueError, match="unavailable evidence"):
            runtime.invoke(_state(seeded_demo, "SCENARIO-003", "HALLUCINATION"))


def test_diagnosis_boundary_and_state_contain_no_ground_truth(
    seeded_demo: Any, knowledge_retriever: Any
) -> None:
    provider = CapturingDiagnosisProvider()
    state = _state(seeded_demo, "SCENARIO-003", "NO-GROUND-TRUTH")
    assert "ground_truth" not in str(state).lower()
    with WorkflowRuntime(
        seeded_demo,
        retriever=knowledge_retriever,
        diagnosis_provider=provider,
    ) as runtime:
        result = runtime.invoke(state)
    assert provider.request is not None
    payload = provider.request.model_dump(mode="json")
    assert set(payload) == {
        "asset_context",
        "vision_evidence",
        "sensor_evidence",
        "failure_mode_candidates",
        "knowledge_evidence",
    }
    assert "ground_truth" not in str(payload).lower()
    assert "ground_truth" not in str(result).lower()


def test_tool_trace_is_safe_and_query_trace_is_auditable(
    seeded_demo: Any, knowledge_retriever: Any
) -> None:
    with WorkflowRuntime(seeded_demo, retriever=knowledge_retriever) as runtime:
        result = runtime.invoke(_state(seeded_demo, "SCENARIO-003", "TRACE"))
    names = {item["node_name"] for item in result["tool_trace"]}
    assert "build_retrieval_queries" in names
    assert "finalize_report" in names
    query_trace = next(
        item for item in result["tool_trace"] if item["node_name"] == "build_retrieval_queries"
    )
    assert query_trace["details"]["queries"] == result["retrieval_queries"]
    serialized = str(result["tool_trace"]).lower()
    assert "api_key" not in serialized
    assert "ground_truth" not in serialized
    assert "sensor_data.csv" not in serialized


def test_repeated_failed_node_attempts_keep_distinct_traces(
    seeded_demo: Any, knowledge_retriever: Any
) -> None:
    with WorkflowRuntime(
        seeded_demo,
        retriever=knowledge_retriever,
        vision_provider=FailingVisionProvider(),
    ) as runtime:
        state = _state(seeded_demo, "SCENARIO-001", "TRACE-RETRY")
        state.update(runtime.workflow.load_asset_context(state))
        first = runtime.workflow.analyze_image(state)
        second = runtime.workflow.analyze_image(state)
        traces = runtime.workflow_repository.list_traces(
            state["run_id"], "analyze_image"
        )

    assert first["tool_trace"][-1]["trace_id"] != second["tool_trace"][-1]["trace_id"]
    assert [trace.attempt_index for trace in traces] == [1, 2]
    assert len({trace.trace_id for trace in traces}) == 2
    assert all(trace.status.value == "DEGRADED" for trace in traces)
