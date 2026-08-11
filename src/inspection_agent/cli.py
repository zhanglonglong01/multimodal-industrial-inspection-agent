"""Command-line entry points for Phase 1 demo initialization and inspection."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .application import InspectionApplicationService
from .config import PROJECT_ROOT, Settings
from .demo import seed_demo, validate_demo
from .evaluation import evaluate_detector, evaluate_retrieval
from .hygiene import assert_repository_hygiene
from .logging_config import configure_logging, log_event
from .phase2 import run_scenario_analysis
from .portfolio_evaluation import run_portfolio_evaluation
from .repository import SQLiteRepository
from .services.knowledge import KnowledgeIndexBuilder, KnowledgeRetriever
from .vision_evaluation import run_live_vision_smoke
from .workflow import WorkflowRuntime, build_initial_state, get_interrupt_payload
from .workflow_evaluation import evaluate_graph_paths, evaluate_offline_scenarios
from .workflow_schemas import ApprovalDecision, ApprovalDecisionInput

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="inspection-agent",
        description="Local demo data, analysis, and Phase 3 workflow commands.",
    )
    parser.add_argument("--data-dir", type=Path, help="Override INSPECTION_DATA_DIR")
    parser.add_argument(
        "--database-path", type=Path, help="Override INSPECTION_DATABASE_PATH"
    )
    parser.add_argument("--log-level", help="Override INSPECTION_LOG_LEVEL")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed-demo", help="Generate deterministic demo data and seed SQLite")
    commands.add_parser("list-assets", help="Query the seeded demo assets")
    commands.add_parser("validate-demo", help="Validate all three scenario fixtures")
    commands.add_parser(
        "build-knowledge-index",
        help="Build the local FAISS index and independent chunk metadata",
    )
    commands.add_parser(
        "evaluate-sensors",
        help="Calculate detector metrics against the three ground-truth manifests",
    )
    commands.add_parser(
        "evaluate-retrieval",
        help="Build the index and calculate Recall@k and MRR",
    )
    scenario_parser = commands.add_parser(
        "analyze-scenario",
        help="Run every independent Phase 2 module for one fixture scenario",
    )
    scenario_parser.add_argument(
        "scenario_id",
        choices=("SCENARIO-001", "SCENARIO-002", "SCENARIO-003"),
    )
    workflow_parser = commands.add_parser(
        "run-workflow", help="Run one fixture scenario through the LangGraph workflow"
    )
    workflow_parser.add_argument(
        "scenario_id",
        choices=("SCENARIO-001", "SCENARIO-002", "SCENARIO-003"),
    )
    workflow_parser.add_argument(
        "--decision",
        choices=("approve", "reject", "request_changes"),
        help="Resume a high-risk interrupt in the same process",
    )
    resume_parser = commands.add_parser(
        "resume-workflow", help="Resume a checkpointed approval in a rebuilt process"
    )
    resume_parser.add_argument("run_id")
    resume_parser.add_argument(
        "decision", choices=("approve", "reject", "request_changes")
    )
    resume_parser.add_argument("--comment")
    commands.add_parser(
        "evaluate-graph", help="Exercise eight deterministic graph routing cases"
    )
    commands.add_parser(
        "evaluate-workflow", help="Score all three workflows after execution"
    )
    commands.add_parser(
        "init-web-demo", help="Initialize demo metadata, FAISS, uploads, and web schema"
    )
    evaluation_parser = commands.add_parser(
        "evaluate", help="Generate the final offline Portfolio MVP evaluation reports"
    )
    evaluation_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("evaluation"),
        help="Directory for report.json and report.md",
    )
    evaluation_parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="Skip the embedded pytest run (intended only when pytest already ran in CI)",
    )
    live_vision_parser = commands.add_parser(
        "evaluate-vision-live",
        help="Opt-in three-image OpenAI Vision smoke test (requires RUN_LIVE_TESTS=1)",
    )
    live_vision_parser.add_argument(
        "--output",
        type=Path,
        default=Path("evaluation/live_vision_smoke.json"),
    )
    commands.add_parser(
        "check-hygiene",
        help="Fail if Git tracks runtime artifacts or obvious secret formats",
    )
    return parser


def _settings_from_args(args: argparse.Namespace) -> Settings:
    overrides: dict[str, object] = {}
    if args.data_dir is not None:
        overrides["data_dir"] = args.data_dir
    if args.database_path is not None:
        overrides["database_path"] = args.database_path
    if args.log_level is not None:
        overrides["log_level"] = args.log_level
    return Settings.model_validate(overrides)


def _print_json(payload: object) -> None:
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = _settings_from_args(args)
    configure_logging(settings.log_level)

    try:
        if args.command == "seed-demo":
            _print_json(seed_demo(settings))
            return 0
        if args.command == "validate-demo":
            _print_json(validate_demo(settings))
            return 0
        if args.command == "list-assets":
            assert settings.database_path is not None
            repository = SQLiteRepository(settings.database_path)
            repository.initialize_schema()
            _print_json(
                [asset.model_dump(mode="json") for asset in repository.list_assets()]
            )
            return 0
        if args.command == "build-knowledge-index":
            _print_json(KnowledgeIndexBuilder(settings).build())
            return 0
        if args.command == "evaluate-sensors":
            _print_json(evaluate_detector(settings))
            return 0
        if args.command == "evaluate-retrieval":
            KnowledgeIndexBuilder(settings).build()
            retriever = KnowledgeRetriever(settings.knowledge_index_dir)
            _print_json(evaluate_retrieval(settings, retriever))
            return 0
        if args.command == "analyze-scenario":
            KnowledgeIndexBuilder(settings).build()
            retriever = KnowledgeRetriever(settings.knowledge_index_dir)
            _print_json(run_scenario_analysis(settings, args.scenario_id, retriever))
            return 0
        if args.command == "run-workflow":
            KnowledgeIndexBuilder(settings).build()
            with WorkflowRuntime(settings) as runtime:
                state = build_initial_state(settings, args.scenario_id)
                result = runtime.invoke(state)
                interrupt_payload = get_interrupt_payload(result)
                if interrupt_payload is not None and args.decision:
                    decision = ApprovalDecisionInput(
                        decision=ApprovalDecision(args.decision.upper())
                    )
                    result = runtime.resume(state["run_id"], decision)
                    interrupt_payload = get_interrupt_payload(result)
                serializable = {
                    key: value for key, value in result.items() if key != "__interrupt__"
                }
                if interrupt_payload is not None:
                    serializable["interrupt_payload"] = interrupt_payload
                _print_json(serializable)
            return 0
        if args.command == "resume-workflow":
            with WorkflowRuntime(settings) as runtime:
                result = runtime.resume(
                    args.run_id,
                    ApprovalDecisionInput(
                        decision=ApprovalDecision(args.decision.upper()),
                        comment=args.comment,
                    ),
                )
                _print_json(
                    {
                        key: value
                        for key, value in result.items()
                        if key != "__interrupt__"
                    }
                )
            return 0
        if args.command == "evaluate-graph":
            _print_json(evaluate_graph_paths(settings))
            return 0
        if args.command == "evaluate-workflow":
            _print_json(evaluate_offline_scenarios(settings))
            return 0
        if args.command == "init-web-demo":
            service = InspectionApplicationService(settings)
            service.initialize()
            _print_json(
                {
                    "app_mode": settings.app_mode,
                    "checks": [
                        item.model_dump(mode="json")
                        for item in service.readiness_checks()
                    ],
                }
            )
            return 0
        if args.command == "evaluate":
            _print_json(
                run_portfolio_evaluation(
                    settings,
                    output_dir=args.output_dir,
                    run_tests=not args.skip_tests,
                )
            )
            return 0
        if args.command == "evaluate-vision-live":
            seed_demo(settings)
            _print_json(run_live_vision_smoke(settings, output_path=args.output))
            return 0
        if args.command == "check-hygiene":
            assert_repository_hygiene(PROJECT_ROOT)
            _print_json({"status": "passed", "scope": "git commit candidates"})
            return 0
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "command_failed",
            command=args.command,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return 1

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
