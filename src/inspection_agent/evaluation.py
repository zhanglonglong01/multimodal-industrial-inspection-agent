"""Deterministic detector and retrieval evaluation for Phase 2 fixtures."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from pydantic import Field

from .analysis_schemas import (
    DetectorEvaluationReport,
    MetricScores,
    RetrievalEvaluationReport,
    RetrievalQuery,
    RetrievalQueryResult,
    ScenarioDetectorEvaluation,
)
from .config import Settings
from .repository import SQLiteRepository
from .schemas import GroundTruthAnomaly, ScenarioManifest, StrictModel
from .services.knowledge import KnowledgeRetriever
from .services.sensors import RuleBasedAndMADDetector


class RetrievalQueryCatalog(StrictModel):
    schema_version: str = "1.0"
    queries: list[RetrievalQuery] = Field(min_length=1)


def _metrics(
    true_positives: int,
    false_positives: int,
    false_negatives: int,
) -> MetricScores:
    predicted = true_positives + false_positives
    expected = true_positives + false_negatives
    precision = true_positives / predicted if predicted else (1.0 if not expected else 0.0)
    recall = true_positives / expected if expected else 1.0
    f1 = (
        2.0 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    return MetricScores(
        precision=precision,
        recall=recall,
        f1=f1,
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
    )


def _load_manifest(settings: Settings, scenario_id: str) -> ScenarioManifest:
    path = settings.scenarios_dir / scenario_id / "manifest.json"
    if not path.is_file():
        raise FileNotFoundError(f"scenario manifest not found: {path}")
    return ScenarioManifest.model_validate_json(path.read_text(encoding="utf-8"))


def _load_timestamps(csv_path: Path) -> list[datetime]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [
            datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
            for row in csv.DictReader(handle)
        ]


def _expected_points(
    timestamps: list[datetime], anomalies: list[GroundTruthAnomaly]
) -> set[tuple[str, datetime]]:
    return {
        (anomaly.sensor_name, timestamp)
        for anomaly in anomalies
        for timestamp in timestamps
        if anomaly.start_time <= timestamp < anomaly.end_time
    }


def _segments_overlap(
    predicted_start: datetime,
    predicted_end: datetime,
    expected: GroundTruthAnomaly,
) -> bool:
    return predicted_start < expected.end_time and expected.start_time < predicted_end


def evaluate_detector(
    settings: Settings,
    detector: RuleBasedAndMADDetector | None = None,
) -> DetectorEvaluationReport:
    detector = detector or RuleBasedAndMADDetector()
    if settings.database_path is None:
        raise ValueError("database path is required for detector evaluation")
    repository = SQLiteRepository(settings.database_path)
    scenario_evaluations: list[ScenarioDetectorEvaluation] = []
    overall_expected_points: set[tuple[str, str, datetime]] = set()
    overall_predicted_points: set[tuple[str, str, datetime]] = set()
    overall_segment_tp = 0
    overall_segment_fp = 0
    overall_segment_fn = 0

    for scenario_id in ("SCENARIO-001", "SCENARIO-002", "SCENARIO-003"):
        manifest = _load_manifest(settings, scenario_id)
        asset = repository.get_asset(manifest.asset_id)
        if asset is None:
            raise ValueError(f"asset is not seeded: {manifest.asset_id}")
        csv_path = settings.scenarios_dir / scenario_id / manifest.sensor_data.path
        analysis = detector.detect(
            csv_path,
            asset,
            manifest.sensor_data.dataset_id,
            expected_sampling_interval_seconds=(
                manifest.sensor_data.sample_interval_seconds
            ),
        )
        timestamps = _load_timestamps(csv_path)
        expected_points = _expected_points(
            timestamps, manifest.ground_truth.sensor_anomalies
        )
        predicted_points = {
            (point.sensor_id, point.timestamp) for point in analysis.anomaly_points
        }
        point_tp = len(expected_points & predicted_points)
        point_fp = len(predicted_points - expected_points)
        point_fn = len(expected_points - predicted_points)

        unmatched_expected = set(range(len(manifest.ground_truth.sensor_anomalies)))
        segment_tp = 0
        for segment in analysis.segments:
            match = next(
                (
                    index
                    for index in sorted(unmatched_expected)
                    if manifest.ground_truth.sensor_anomalies[index].sensor_name
                    == segment.sensor_id
                    and manifest.ground_truth.sensor_anomalies[index].direction.value
                    == segment.direction
                    and _segments_overlap(
                        segment.start_time,
                        segment.end_time,
                        manifest.ground_truth.sensor_anomalies[index],
                    )
                ),
                None,
            )
            if match is not None:
                unmatched_expected.remove(match)
                segment_tp += 1
        segment_fp = len(analysis.segments) - segment_tp
        segment_fn = len(unmatched_expected)
        overall_segment_tp += segment_tp
        overall_segment_fp += segment_fp
        overall_segment_fn += segment_fn

        overall_expected_points.update(
            (scenario_id, sensor_id, timestamp)
            for sensor_id, timestamp in expected_points
        )
        overall_predicted_points.update(
            (scenario_id, sensor_id, timestamp)
            for sensor_id, timestamp in predicted_points
        )
        scenario_evaluations.append(
            ScenarioDetectorEvaluation(
                scenario_id=scenario_id,
                point_metrics=_metrics(point_tp, point_fp, point_fn),
                segment_metrics=_metrics(segment_tp, segment_fp, segment_fn),
                predicted_point_count=len(predicted_points),
                expected_point_count=len(expected_points),
                predicted_segment_count=len(analysis.segments),
                expected_segment_count=len(manifest.ground_truth.sensor_anomalies),
            )
        )

    overall_point_tp = len(overall_expected_points & overall_predicted_points)
    overall_point_fp = len(overall_predicted_points - overall_expected_points)
    overall_point_fn = len(overall_expected_points - overall_predicted_points)
    return DetectorEvaluationReport(
        detector=detector.detector_name,
        parameters=detector.parameters,
        scenarios=scenario_evaluations,
        overall_point_metrics=_metrics(
            overall_point_tp,
            overall_point_fp,
            overall_point_fn,
        ),
        overall_segment_metrics=_metrics(
            overall_segment_tp,
            overall_segment_fp,
            overall_segment_fn,
        ),
    )


def evaluate_retrieval(
    settings: Settings,
    retriever: KnowledgeRetriever,
) -> RetrievalEvaluationReport:
    path = settings.retrieval_evaluation_path
    if not path.is_file():
        raise FileNotFoundError(f"retrieval evaluation queries not found: {path}")
    catalog = RetrievalQueryCatalog.model_validate(
        json.loads(path.read_text(encoding="utf-8"))
    )
    if settings.database_path is None:
        raise ValueError("database path is required for retrieval evaluation")
    repository = SQLiteRepository(settings.database_path)
    query_results: list[RetrievalQueryResult] = []

    for query in catalog.queries:
        manifest = _load_manifest(settings, query.scenario_id)
        asset = repository.get_asset(manifest.asset_id)
        if asset is None:
            raise ValueError(f"asset is not seeded: {manifest.asset_id}")
        results = retriever.search(
            query.query,
            top_k=3,
            asset_type=asset.asset_type,
        )
        retrieved_ids = [result.chunk_id for result in results]
        relevant = set(query.expected_relevant_chunk_ids)
        recall_at_1 = len(set(retrieved_ids[:1]) & relevant) / len(relevant)
        recall_at_3 = len(set(retrieved_ids[:3]) & relevant) / len(relevant)
        first_rank = next(
            (
                rank
                for rank, chunk_id in enumerate(retrieved_ids, start=1)
                if chunk_id in relevant
            ),
            None,
        )
        query_results.append(
            RetrievalQueryResult(
                query_id=query.query_id,
                scenario_id=query.scenario_id,
                expected_relevant_chunk_ids=query.expected_relevant_chunk_ids,
                retrieved_chunk_ids=retrieved_ids,
                recall_at_1=recall_at_1,
                recall_at_3=recall_at_3,
                reciprocal_rank=1.0 / first_rank if first_rank is not None else 0.0,
            )
        )

    count = len(query_results)
    return RetrievalEvaluationReport(
        query_count=count,
        recall_at_1=sum(item.recall_at_1 for item in query_results) / count,
        recall_at_3=sum(item.recall_at_3 for item in query_results) / count,
        mrr=sum(item.reciprocal_rank for item in query_results) / count,
        queries=query_results,
    )
