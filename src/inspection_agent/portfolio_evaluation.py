"""Unified, offline-first Portfolio MVP evaluation and report rendering."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT, Settings
from .demo import seed_demo
from .evaluation import evaluate_detector, evaluate_retrieval
from .metropt3 import measured_metropt3_evaluation
from .repository import SQLiteRepository
from .services.knowledge import KnowledgeIndexBuilder, KnowledgeRetriever
from .services.work_orders import WorkOrderService
from .workflow import WorkflowRuntime, build_initial_state, get_interrupt_payload
from .workflow_evaluation import evaluate_graph_paths, evaluate_offline_scenarios
from .workflow_repository import WorkflowRepository
from .workflow_schemas import ApprovalDecision, ApprovalDecisionInput


def _timed(callable_: Any) -> tuple[Any, float]:
    started = time.perf_counter()
    result = callable_()
    return result, (time.perf_counter() - started) * 1000.0


def _git_metadata() -> tuple[str, bool]:
    sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    return sha, dirty


def _pytest_summary() -> dict[str, Any]:
    started = time.perf_counter()
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    output = f"{completed.stdout}\n{completed.stderr}"

    def count(label: str) -> int:
        match = re.search(rf"(\d+) {label}", output)
        return int(match.group(1)) if match else 0

    summary = {
        "collected": sum(
            count(label) for label in ("passed", "failed", "skipped", "error", "errors")
        ),
        "passed": count("passed"),
        "failed": count("failed"),
        "skipped": count("skipped"),
        "errors": max(count("error"), count("errors")),
        "duration_ms": (time.perf_counter() - started) * 1000.0,
        "command": "python -m pytest -q",
    }
    if completed.returncode != 0:
        raise RuntimeError(f"pytest failed during portfolio evaluation:\n{output[-4000:]}")
    return summary


def _isolated_settings(source: Settings, temporary_root: Path) -> Settings:
    data_dir = temporary_root / "data"
    for relative in (
        Path("demo") / "fixtures" / "images",
        Path("knowledge"),
        Path("failure_modes"),
        Path("evaluation"),
        Path("real") / "metropt3",
    ):
        shutil.copytree(source.data_dir / relative, data_dir / relative)
    settings = Settings(
        app_env="test",
        app_mode="demo",
        vision_provider="fixture",
        log_level="ERROR",
        data_dir=data_dir,
        database_path=data_dir / "runtime" / "evaluation.db",
        checkpoint_path=data_dir / "runtime" / "evaluation_checkpoints.db",
        random_seed=source.random_seed,
    )
    seed_demo(settings)
    KnowledgeIndexBuilder(settings).build()
    return settings


def _sensor_section(report: Any) -> dict[str, Any]:
    scenarios = []
    for item in report.scenarios:
        if item.expected_point_count == 0 and item.expected_segment_count == 0:
            scenarios.append(
                {
                    "scenario_id": item.scenario_id,
                    "normal_case_pass": (
                        item.predicted_point_count == 0 and item.predicted_segment_count == 0
                    ),
                    "expected_point_count": 0,
                    "predicted_point_count": item.predicted_point_count,
                    "expected_segment_count": 0,
                    "predicted_segment_count": item.predicted_segment_count,
                }
            )
        else:
            scenarios.append(
                {
                    "scenario_id": item.scenario_id,
                    "normal_case_pass": None,
                    "point_metrics": item.point_metrics.model_dump(mode="json"),
                    "segment_metrics": item.segment_metrics.model_dump(mode="json"),
                    "expected_point_count": item.expected_point_count,
                    "predicted_point_count": item.predicted_point_count,
                    "expected_segment_count": item.expected_segment_count,
                    "predicted_segment_count": item.predicted_segment_count,
                }
            )
    return {
        "detector": report.detector,
        "parameters": report.parameters.model_dump(mode="json"),
        "overall_point_metrics": report.overall_point_metrics.model_dump(mode="json"),
        "overall_segment_metrics": report.overall_segment_metrics.model_dump(mode="json"),
        "scenarios": scenarios,
        "normal_case_reporting": "Normal-case pass; undefined positive-class F1 is not reported.",
    }


def _workflow_section(report: dict[str, Any]) -> dict[str, Any]:
    scenarios = []
    for item in report["scenarios"]:
        passed = all(
            (
                item["failure_mode_match"],
                item["required_evidence_present"],
                item["interrupt_match"],
                item["work_order_side_effect_match"],
            )
        )
        scenarios.append({**item, "scenario_pass": passed})
    passed_count = sum(item["scenario_pass"] for item in scenarios)
    return {
        "scenario_count": len(scenarios),
        "passed_count": passed_count,
        "scenario_pass_rate": passed_count / len(scenarios),
        "candidate_match_count": sum(item["failure_mode_match"] for item in scenarios),
        "required_evidence_count": sum(
            item["required_evidence_present"] for item in scenarios
        ),
        "interrupt_behavior_count": sum(item["interrupt_match"] for item in scenarios),
        "work_order_side_effect_count": sum(
            item["work_order_side_effect_match"] for item in scenarios
        ),
        "scenarios": scenarios,
        "metric_name": "Synthetic scenario task success",
    }


def _safety_section(settings: Settings, graph_report: dict[str, Any]) -> dict[str, Any]:
    run_id = "RUN-PORTFOLIO-SAFETY-RESTART"
    inspection_id = "INSPECTION-PORTFOLIO-SAFETY-RESTART"
    first_runtime = WorkflowRuntime(settings)
    try:
        state = build_initial_state(
            settings,
            "SCENARIO-001",
            run_id=run_id,
            inspection_id=inspection_id,
        )
        interrupted = first_runtime.invoke(state)
        interrupt_present = get_interrupt_payload(interrupted) is not None
    finally:
        first_runtime.close()

    repository = WorkflowRepository(settings.database_path)  # type: ignore[arg-type]
    service = WorkOrderService(repository)
    draft_id = interrupted["work_order_draft_id"]
    approval_id = interrupted["approval_request_id"]
    bypass_rejected = False
    try:
        service.create_work_order(draft_id=draft_id, approval_id=None)
    except PermissionError:
        bypass_rejected = True

    resumed_runtime = WorkflowRuntime(settings)
    try:
        resumed = resumed_runtime.resume(
            run_id,
            ApprovalDecisionInput(
                decision=ApprovalDecision.APPROVE,
                reviewer="portfolio-evaluator",
                comment="deterministic restart/resume safety evaluation",
            ),
        )
    finally:
        resumed_runtime.close()
    first_work_order = service.create_work_order(
        draft_id=draft_id,
        approval_id=approval_id,
    )
    second_work_order = service.create_work_order(
        draft_id=draft_id,
        approval_id=approval_id,
    )
    dual_case = next(
        item for item in graph_report["cases"] if item["case_id"] == "DUAL-FAILURE"
    )
    checks = {
        "high_risk_approval_enforcement": interrupt_present and bypass_rejected,
        "direct_bypass_rejection": bypass_rejected,
        "work_order_idempotency": (
            first_work_order.work_order_id == second_work_order.work_order_id
            and repository.count_work_orders(draft_id) == 1
        ),
        "restart_resume": resumed.get("work_order_id") == first_work_order.work_order_id,
        "dual_evidence_failure": (
            dual_case["final_status"] == "INSUFFICIENT_EVIDENCE"
            and dual_case["work_order_id"] is None
        ),
    }
    return {
        "passed_count": sum(checks.values()),
        "check_count": len(checks),
        "all_passed": all(checks.values()),
        "checks": checks,
    }


def _versions(settings: Settings) -> dict[str, list[str]]:
    scenario_versions: set[str] = set()
    dataset_versions: set[str] = set()
    repository = SQLiteRepository(settings.database_path)  # type: ignore[arg-type]
    for scenario_id in ("SCENARIO-001", "SCENARIO-002", "SCENARIO-003"):
        payload = json.loads(
            (settings.scenarios_dir / scenario_id / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        scenario_versions.add(payload["schema_version"])
        dataset = repository.get_sensor_dataset(scenario_id)
        if dataset is None:
            raise ValueError(f"sensor dataset is not seeded: {scenario_id}")
        dataset_versions.add(dataset.schema_version)
    return {
        "scenario_schema_versions": sorted(scenario_versions),
        "dataset_schema_versions": sorted(dataset_versions),
    }


def run_portfolio_evaluation(
    settings: Settings,
    *,
    output_dir: Path,
    run_tests: bool = True,
) -> dict[str, Any]:
    """Run deterministic MVP evaluation and write machine/human-readable reports."""

    commit_sha, dirty = _git_metadata()
    tests = _pytest_summary() if run_tests else {"status": "not_run"}
    with tempfile.TemporaryDirectory(prefix="inspection-portfolio-eval-") as temporary:
        isolated = _isolated_settings(settings, Path(temporary))
        sensor, sensor_ms = _timed(lambda: evaluate_detector(isolated))
        retriever = KnowledgeRetriever(isolated.knowledge_index_dir)
        retrieval, retrieval_ms = _timed(
            lambda: evaluate_retrieval(isolated, retriever)
        )
        workflow, workflow_ms = _timed(lambda: evaluate_offline_scenarios(isolated))
        graph, graph_ms = _timed(lambda: evaluate_graph_paths(isolated))
        safety, safety_ms = _timed(lambda: _safety_section(isolated, graph))
        real_sensor, real_sensor_ms = measured_metropt3_evaluation(isolated)
        versions = _versions(isolated)

    report = {
        "schema_version": "1.1",
        "metadata": {
            "commit_sha": commit_sha,
            "git_dirty": dirty,
            "timestamp": datetime.now(UTC).isoformat(),
            "app_mode": "demo",
            "vision_provider": "fixture",
            "diagnosis_provider": "fixture_diagnosis",
            **versions,
            "scenario_count": 3,
        },
        "sensor": _sensor_section(sensor),
        "real_sensor": real_sensor.model_dump(mode="json"),
        "retrieval": {
            **retrieval.model_dump(mode="json"),
            "scope_note": "Only 4 manually defined retrieval queries.",
        },
        "workflow": _workflow_section(workflow),
        "safety": safety,
        "tests": tests,
        "operational": {
            "sensor_evaluation_ms": sensor_ms,
            "retrieval_evaluation_ms": retrieval_ms,
            "workflow_evaluation_ms": workflow_ms,
            "graph_path_evaluation_ms": graph_ms,
            "safety_evaluation_ms": safety_ms,
            "real_sensor_evaluation_ms": real_sensor_ms,
        },
        "limitations": [
            "The end-to-end workflow evaluation still contains three synthetic scenarios only.",
            "MetroPT-3 contributes two real operational sensor windows, not a real multimodal workflow.",
            "Four manually defined retrieval queries only.",
            "Fixture Vision and Fixture Diagnosis make offline execution deterministic.",
            "No metric in this report represents industrial diagnosis, vision, or production accuracy.",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(_render_markdown(report), encoding="utf-8")
    return report


def _metric_row(name: str, values: dict[str, Any]) -> str:
    return (
        f"| {name} | {values['precision']:.4f} | {values['recall']:.4f} | "
        f"{values['f1']:.4f} | {values['true_positives']} | "
        f"{values['false_positives']} | {values['false_negatives']} |"
    )


def _render_markdown(report: dict[str, Any]) -> str:
    metadata = report["metadata"]
    sensor = report["sensor"]
    real_sensor = report["real_sensor"]
    retrieval = report["retrieval"]
    workflow = report["workflow"]
    safety = report["safety"]
    tests = report["tests"]
    lines = [
        "# Portfolio MVP Evaluation Report",
        "",
        "> Synthetic end-to-end task evaluation plus real sensor event-window analysis; not industrial accuracy or factory validation.",
        "",
        "## Run Metadata",
        "",
        f"- Commit: `{metadata['commit_sha']}` (dirty: `{metadata['git_dirty']}`)",
        f"- Timestamp: `{metadata['timestamp']}`",
        f"- Mode/providers: `{metadata['app_mode']}` / `{metadata['vision_provider']}` / `{metadata['diagnosis_provider']}`",
        f"- Scope: {metadata['scenario_count']} scenarios; scenario schema {metadata['scenario_schema_versions']}; dataset schema {metadata['dataset_schema_versions']}",
        "",
        "## Sensor Evaluation",
        "",
        "| Level | Precision | Recall | F1 | TP | FP | FN |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        _metric_row("Point", sensor["overall_point_metrics"]),
        _metric_row("Segment", sensor["overall_segment_metrics"]),
        "",
        "Normal scenario: **Normal-case pass** (no expected or predicted anomaly points/segments); positive-class F1 is not reported.",
        "",
        "## Real Sensor Event-Window Analysis",
        "",
        "MetroPT-3 is real operational railway APU data. It has company failure-event reports, not point-level anomaly labels, so precision/recall/F1 are not reported.",
        "",
        "| Window | Relation | Alerted timestamps | Alert rate | Alerted sensors |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for item in real_sensor["windows"]:
        lines.append(
            f"| {item['window_id']} | {item['report_relation']} | "
            f"{item['alerted_timestamp_count']} | {item['alert_timestamp_rate']:.2%} | "
            f"{', '.join(item['alerted_sensor_ids']) or 'none'} |"
        )
    lines.extend(
        [
            "",
            "The outside-report reference window is not verified healthy. Its higher alert rate demonstrates that the synthetic-demo detector is not calibrated for MetroPT-3 operating-state transitions.",
            "",
            "## Retrieval Evaluation",
            "",
            f"Only **{retrieval['query_count']} manually defined retrieval queries**.",
            "",
            "| Recall@1 | Recall@3 | MRR |",
            "| ---: | ---: | ---: |",
            f"| {retrieval['recall_at_1']:.4f} | {retrieval['recall_at_3']:.4f} | {retrieval['mrr']:.4f} |",
            "",
            "## Workflow Evaluation",
            "",
            f"Synthetic scenario task success: **{workflow['passed_count']}/{workflow['scenario_count']} ({workflow['scenario_pass_rate']:.1%})**.",
            "",
            "| Scenario | Candidate | Evidence | Interrupt | WorkOrder side effect | Pass |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item in workflow["scenarios"]:
        lines.append(
            f"| {item['scenario_id']} | {item['failure_mode_match']} | "
            f"{item['required_evidence_present']} | {item['interrupt_match']} | "
            f"{item['work_order_side_effect_match']} | {item['scenario_pass']} |"
        )
    lines.extend(
        [
            "",
            "## Safety and Idempotency",
            "",
            f"Passed **{safety['passed_count']}/{safety['check_count']}** deterministic checks.",
            "",
        ]
    )
    for name, passed in safety["checks"].items():
        lines.append(f"- `{name}`: **{'PASS' if passed else 'FAIL'}**")
    if tests.get("status") == "not_run":
        test_text = "Tests were not run by this report invocation."
    else:
        test_text = (
            f"Collected {tests['collected']}; passed {tests['passed']}; "
            f"skipped {tests['skipped']}; failed {tests['failed']}; errors {tests['errors']}."
        )
    lines.extend(
        [
            "",
            "## Tests",
            "",
            test_text,
            "",
            "## Measured Runtime",
            "",
        ]
    )
    for name, duration in report["operational"].items():
        lines.append(f"- `{name}`: {duration:.2f} ms")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    lines.append("")
    return "\n".join(lines)
