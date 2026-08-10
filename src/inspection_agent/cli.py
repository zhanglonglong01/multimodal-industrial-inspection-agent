"""Command-line entry points for Phase 1 demo initialization and inspection."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import Settings
from .demo import seed_demo, validate_demo
from .evaluation import evaluate_detector, evaluate_retrieval
from .logging_config import configure_logging, log_event
from .phase2 import run_scenario_analysis
from .repository import SQLiteRepository
from .services.knowledge import KnowledgeIndexBuilder, KnowledgeRetriever


logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="inspection-agent",
        description="Deterministic Phase 1 data and Phase 2 analysis commands.",
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
    return parser


def _settings_from_args(args: argparse.Namespace) -> Settings:
    overrides: dict[str, object] = {}
    if args.data_dir is not None:
        overrides["data_dir"] = args.data_dir
    if args.database_path is not None:
        overrides["database_path"] = args.database_path
    if args.log_level is not None:
        overrides["log_level"] = args.log_level
    return Settings(**overrides)


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
