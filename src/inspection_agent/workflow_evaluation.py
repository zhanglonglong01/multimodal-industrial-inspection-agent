"""Offline workflow evaluations; ground truth is isolated to post-run scoring."""

from __future__ import annotations

from typing import Any

from .config import Settings
from .schemas import ScenarioManifest
from .services.knowledge import KnowledgeIndexBuilder
from .workflow import WorkflowRuntime, build_initial_state, get_interrupt_payload
from .workflow_schemas import ApprovalDecision, ApprovalDecisionInput


class _FailingVisionProvider:
    provider_name = "failing_test_vision"

    def analyze(self, artifact_id: str, asset_context: object) -> object:
        raise RuntimeError("injected vision failure")


class _FailingSensorService:
    def detect(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError("injected sensor failure")


def _execute_case(
    settings: Settings,
    *,
    case_id: str,
    scenario_id: str,
    decision: ApprovalDecision | None = None,
    fail_vision: bool = False,
    fail_sensor: bool = False,
) -> dict[str, Any]:
    runtime = WorkflowRuntime(
        settings,
        vision_provider=_FailingVisionProvider() if fail_vision else None,
        sensor_service=_FailingSensorService() if fail_sensor else None,  # type: ignore[arg-type]
    )
    try:
        state = build_initial_state(
            settings,
            scenario_id,
            run_id=f"RUN-EVAL-V2-{case_id}",
            inspection_id=f"INSPECTION-EVAL-V2-{case_id}",
        )
        result = runtime.invoke(state)
        interrupt_payload = get_interrupt_payload(result)
        interrupted = interrupt_payload is not None
        if interrupted and decision is not None:
            result = runtime.resume(
                state["run_id"],
                ApprovalDecisionInput(decision=decision, comment="offline graph evaluation"),
            )
        final = result.get("final_report")
        return {
            "case_id": case_id,
            "scenario_id": scenario_id,
            "interrupted": interrupted,
            "interrupt_payload": interrupt_payload,
            "final_status": final["status"] if final else None,
            "risk_level": (
                result.get("risk_assessment", {}).get("risk_level")
                if result.get("risk_assessment")
                else None
            ),
            "work_order_id": result.get("work_order_id"),
            "warning_count": len(result.get("warnings", [])),
            "error_count": len(result.get("errors", [])),
            "passed": bool(final) if decision is not None or not interrupted else interrupted,
        }
    finally:
        runtime.close()


def evaluate_graph_paths(settings: Settings) -> dict[str, Any]:
    """Exercise eight graph/routing/degraded/approval cases without network calls."""

    KnowledgeIndexBuilder(settings).build()
    cases = [
        _execute_case(settings, case_id="NORMAL", scenario_id="SCENARIO-003"),
        _execute_case(
            settings,
            case_id="PUMP",
            scenario_id="SCENARIO-001",
            decision=ApprovalDecision.REQUEST_CHANGES,
        ),
        _execute_case(
            settings,
            case_id="MOTOR",
            scenario_id="SCENARIO-002",
            decision=ApprovalDecision.REQUEST_CHANGES,
        ),
        _execute_case(
            settings,
            case_id="VISION-FAILURE",
            scenario_id="SCENARIO-001",
            fail_vision=True,
            decision=ApprovalDecision.REJECT,
        ),
        _execute_case(
            settings,
            case_id="SENSOR-FAILURE",
            scenario_id="SCENARIO-001",
            fail_sensor=True,
            decision=ApprovalDecision.REJECT,
        ),
        _execute_case(
            settings,
            case_id="DUAL-FAILURE",
            scenario_id="SCENARIO-001",
            fail_vision=True,
            fail_sensor=True,
        ),
        _execute_case(
            settings,
            case_id="HIGH-REJECT",
            scenario_id="SCENARIO-002",
            decision=ApprovalDecision.REJECT,
        ),
        _execute_case(
            settings,
            case_id="HIGH-APPROVE",
            scenario_id="SCENARIO-001",
            decision=ApprovalDecision.APPROVE,
        ),
    ]
    return {
        "fixture_only": True,
        "case_count": len(cases),
        "passed_count": sum(bool(item["passed"]) for item in cases),
        "cases": cases,
    }


def evaluate_offline_scenarios(settings: Settings) -> dict[str, Any]:
    """Run graphs first; only the scorer below opens ground-truth manifests."""

    KnowledgeIndexBuilder(settings).build()
    completed: dict[str, dict[str, Any]] = {}
    interrupted_by_scenario: dict[str, bool] = {}
    fixed_decisions = {
        "SCENARIO-001": ApprovalDecision.APPROVE,
        "SCENARIO-002": ApprovalDecision.REJECT,
    }
    for scenario_id in ("SCENARIO-001", "SCENARIO-002", "SCENARIO-003"):
        runtime = WorkflowRuntime(settings)
        try:
            state = build_initial_state(
                settings,
                scenario_id,
                run_id=f"RUN-OFFLINE-V2-{scenario_id}",
                inspection_id=f"INSPECTION-OFFLINE-V2-{scenario_id}",
            )
            result = runtime.invoke(state)
            interrupted_by_scenario[scenario_id] = get_interrupt_payload(result) is not None
            if interrupted_by_scenario[scenario_id]:
                result = runtime.resume(
                    state["run_id"],
                    ApprovalDecisionInput(
                        decision=fixed_decisions[scenario_id],
                        comment="fixed offline evaluation decision",
                    ),
                )
            completed[scenario_id] = result
        finally:
            runtime.close()

    # Scoring boundary: ground truth becomes visible only after all graph runs finish.
    scenario_scores = []
    for scenario_id, result in completed.items():
        manifest = ScenarioManifest.model_validate_json(
            (settings.scenarios_dir / scenario_id / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        diagnosis = result.get("diagnosis_report") or {}
        predicted = diagnosis.get("primary_fault_candidate")
        expected = manifest.ground_truth.expected_failure_mode
        expected_interrupt = expected is not None
        expected_sensors = {
            item.sensor_name for item in manifest.ground_truth.sensor_anomalies
        }
        detected_sensors = {
            item["sensor_id"]
            for item in (result.get("sensor_result") or {}).get("segments", [])
        }
        supporting_ids = diagnosis.get("supporting_evidence_ids", [])
        actual_evidence_kinds = {
            evidence_id.split("-")[1]
            for evidence_id in supporting_ids
            if evidence_id.startswith("EVIDENCE-")
        }
        required_evidence_kinds = (
            {"VISION", "SENSOR", "KNOWLEDGE"}
            if expected is not None
            else {"VISION", "KNOWLEDGE"}
        )
        work_order_expected = (
            fixed_decisions.get(scenario_id) is ApprovalDecision.APPROVE
            and expected_interrupt
        )
        work_order_created = result.get("work_order_id") is not None
        scenario_scores.append(
            {
                "scenario_id": scenario_id,
                "expected_failure_mode": expected,
                "predicted_failure_mode": predicted,
                "failure_mode_match": predicted == expected,
                "expected_anomalous_sensors": sorted(expected_sensors),
                "detected_anomalous_sensors": sorted(detected_sensors),
                "sensor_set_match": expected_sensors == detected_sensors,
                "risk_level": (result.get("risk_assessment") or {}).get("risk_level"),
                "expected_interrupt": expected_interrupt,
                "actual_interrupt": interrupted_by_scenario[scenario_id],
                "interrupt_match": expected_interrupt
                == interrupted_by_scenario[scenario_id],
                "required_evidence_kinds": sorted(required_evidence_kinds),
                "actual_evidence_kinds": sorted(actual_evidence_kinds),
                "required_evidence_present": required_evidence_kinds
                <= actual_evidence_kinds,
                "work_order_expected": work_order_expected,
                "work_order_created": work_order_created,
                "work_order_side_effect_match": work_order_expected
                == work_order_created,
            }
        )
    return {
        "fixture_only": True,
        "scenario_count": len(scenario_scores),
        "failure_mode_matches": sum(
            item["failure_mode_match"] for item in scenario_scores
        ),
        "sensor_set_matches": sum(item["sensor_set_match"] for item in scenario_scores),
        "interrupt_matches": sum(item["interrupt_match"] for item in scenario_scores),
        "required_evidence_matches": sum(
            item["required_evidence_present"] for item in scenario_scores
        ),
        "work_order_side_effect_matches": sum(
            item["work_order_side_effect_match"] for item in scenario_scores
        ),
        "scenarios": scenario_scores,
    }
