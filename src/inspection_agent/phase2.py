"""Small application functions for running Phase 2 modules without an Agent."""

from __future__ import annotations

from .analysis_schemas import Phase2ScenarioAnalysis
from .config import Settings
from .evidence import ensure_unique_evidence
from .repository import SQLiteRepository
from .schemas import ScenarioManifest
from .services.failure_modes import FailureModeRepository
from .services.knowledge import KnowledgeRetriever
from .services.sensors import RuleBasedAndMADDetector
from .services.vision import FixtureVisionProvider


def run_scenario_analysis(
    settings: Settings,
    scenario_id: str,
    retriever: KnowledgeRetriever,
) -> Phase2ScenarioAnalysis:
    """Execute every Phase 2 module in-process for one fixture scenario."""

    manifest_path = settings.scenarios_dir / scenario_id / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"scenario manifest not found: {manifest_path}")
    manifest = ScenarioManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    if manifest.scenario_id != scenario_id:
        raise ValueError("scenario manifest ID does not match requested scenario")
    if settings.database_path is None:
        raise ValueError("database path is required for scenario analysis")
    asset_repository = SQLiteRepository(settings.database_path)
    asset = asset_repository.get_asset(manifest.asset_id)
    if asset is None:
        raise ValueError(f"asset is not seeded: {manifest.asset_id}")

    vision = FixtureVisionProvider(settings).analyze(
        manifest.image_fixture_id,
        asset,
    )
    sensor = RuleBasedAndMADDetector().detect(
        settings.scenarios_dir / scenario_id / manifest.sensor_data.path,
        asset,
        manifest.sensor_data.dataset_id,
        expected_sampling_interval_seconds=manifest.sensor_data.sample_interval_seconds,
    )
    visual_label = vision.findings[0].label if vision.findings else None
    failure_modes = FailureModeRepository(settings.failure_modes_path).get_failure_modes(
        asset.asset_type,
        visual_label=visual_label,
    )
    anomalous_sensors = {segment.sensor_id for segment in sensor.segments}
    if anomalous_sensors:
        failure_modes = [
            mode
            for mode in failure_modes
            if anomalous_sensors.intersection(mode.related_sensors)
        ]

    if (
        vision.findings
        and vision.findings[0].label.value == "no_visible_anomaly"
        and not anomalous_sensors
    ):
        query_terms = [
            "normal inspection",
            "no visible anomaly",
            "baseline ranges",
            "no failure mode",
        ]
    else:
        query_terms = [
            asset.asset_type.value,
            *(finding.label.value.replace("_", " ") for finding in vision.findings),
            *(sensor_id.replace("_", " ") for sensor_id in sorted(anomalous_sensors)),
            "inspection",
        ]
    retrieval_query = " ".join(query_terms)
    if vision.findings[0].label.value == "no_visible_anomaly":
        knowledge = [
            result
            for result in retriever.search(
                retrieval_query,
                top_k=retriever.metadata.chunk_count,
                asset_type=asset.asset_type,
            )
            if result.doc_id == "KNOW-INSPECTION-SOP"
        ][:3]
    else:
        knowledge = retriever.search(
            retrieval_query,
            top_k=3,
            asset_type=asset.asset_type,
        )
    evidence = [
        *(finding.to_evidence_ref() for finding in vision.findings),
        *(segment.to_evidence_ref() for segment in sensor.segments),
        *(
            chunk.to_evidence_ref()
            for result in knowledge
            if (chunk := retriever.get_chunk(result.chunk_id)) is not None
        ),
    ]
    ensure_unique_evidence(evidence)
    return Phase2ScenarioAnalysis(
        scenario_id=scenario_id,
        asset_id=asset.asset_id,
        vision=vision,
        sensor=sensor,
        failure_modes=failure_modes,
        retrieval_query=retrieval_query,
        knowledge=knowledge,
        evidence=evidence,
    )
