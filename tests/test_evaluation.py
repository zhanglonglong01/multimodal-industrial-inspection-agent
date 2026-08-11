from __future__ import annotations

from inspection_agent.config import Settings
from inspection_agent.evaluation import evaluate_detector, evaluate_retrieval
from inspection_agent.portfolio_evaluation import run_portfolio_evaluation
from inspection_agent.services.knowledge import KnowledgeRetriever


def test_detector_evaluation_uses_all_three_ground_truth_manifests(
    seeded_demo: Settings,
) -> None:
    report = evaluate_detector(seeded_demo)
    scenarios = {item.scenario_id: item for item in report.scenarios}

    assert set(scenarios) == {"SCENARIO-001", "SCENARIO-002", "SCENARIO-003"}
    assert scenarios["SCENARIO-001"].expected_point_count == 48
    assert scenarios["SCENARIO-002"].expected_point_count == 72
    assert scenarios["SCENARIO-003"].expected_point_count == 0
    assert scenarios["SCENARIO-003"].predicted_point_count == 0
    assert report.overall_point_metrics.true_positives == 107
    assert report.overall_point_metrics.false_positives == 0
    assert report.overall_point_metrics.false_negatives == 13
    assert report.overall_segment_metrics.true_positives == 4


def test_retrieval_evaluation_calculates_recall_and_mrr(
    seeded_demo: Settings,
    knowledge_retriever: KnowledgeRetriever,
) -> None:
    report = evaluate_retrieval(seeded_demo, knowledge_retriever)

    assert report.query_count == 4
    assert {query.scenario_id for query in report.queries} == {
        "SCENARIO-001",
        "SCENARIO-002",
        "SCENARIO-003",
    }
    assert report.recall_at_1 == 1.0
    assert report.recall_at_3 == 1.0
    assert report.mrr == 1.0


def test_portfolio_evaluation_writes_truthful_offline_reports(
    seeded_demo: Settings, tmp_path
) -> None:
    output_dir = tmp_path / "evaluation"

    report = run_portfolio_evaluation(
        seeded_demo,
        output_dir=output_dir,
        run_tests=False,
    )

    assert (output_dir / "report.json").is_file()
    assert (output_dir / "report.md").is_file()
    assert report["metadata"]["scenario_count"] == 3
    assert report["metadata"]["vision_provider"] == "fixture"
    assert report["retrieval"]["query_count"] == 4
    assert report["workflow"]["scenario_pass_rate"] == 1.0
    assert report["safety"]["all_passed"] is True
    normal = next(
        item for item in report["sensor"]["scenarios"]
        if item["scenario_id"] == "SCENARIO-003"
    )
    assert normal["normal_case_pass"] is True
    assert "point_metrics" not in normal
    assert "Normal-case pass" in (output_dir / "report.md").read_text(encoding="utf-8")
