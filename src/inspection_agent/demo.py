"""Deterministic Phase 1 demo data generation and validation."""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import fmean

from .config import Settings
from .logging_config import log_event
from .repository import SQLiteRepository
from .schemas import (
    AnomalyDirection,
    Asset,
    AssetStatus,
    AssetType,
    Criticality,
    DemoValidationResult,
    GroundTruthAnomaly,
    ImageFixture,
    ImageFixtureManifest,
    KnowledgeManifest,
    ScenarioGroundTruth,
    ScenarioManifest,
    SeedResult,
    SensorDataManifest,
    SensorDatasetMetadata,
    SensorDefinition,
)


logger = logging.getLogger(__name__)

SERIES_START = datetime(2025, 1, 1, tzinfo=UTC)
SAMPLE_INTERVAL_SECONDS = 300
SAMPLE_COUNT = 288
EXPECTED_SCENARIO_IDS = {
    "SCENARIO-001",
    "SCENARIO-002",
    "SCENARIO-003",
}


PUMP_SENSORS = [
    SensorDefinition(
        sensor_name="vibration",
        display_name="Vibration RMS",
        unit="mm/s",
        operating_min=0.0,
        operating_max=4.5,
    ),
    SensorDefinition(
        sensor_name="bearing_temperature",
        display_name="Bearing Temperature",
        unit="degC",
        operating_min=20.0,
        operating_max=80.0,
    ),
    SensorDefinition(
        sensor_name="outlet_pressure",
        display_name="Outlet Pressure",
        unit="bar",
        operating_min=4.0,
        operating_max=7.0,
    ),
]

MOTOR_SENSORS = [
    SensorDefinition(
        sensor_name="vibration",
        display_name="Vibration RMS",
        unit="mm/s",
        operating_min=0.0,
        operating_max=4.5,
    ),
    SensorDefinition(
        sensor_name="bearing_temperature",
        display_name="Bearing Temperature",
        unit="degC",
        operating_min=20.0,
        operating_max=85.0,
    ),
    SensorDefinition(
        sensor_name="current",
        display_name="Motor Current",
        unit="A",
        operating_min=5.0,
        operating_max=30.0,
    ),
]

DEMO_ASSETS = [
    Asset(
        asset_id="PUMP-001",
        name="Demo Centrifugal Pump",
        asset_type=AssetType.PUMP,
        site="DEMO-SITE",
        status=AssetStatus.ACTIVE,
        criticality=Criticality.HIGH,
        description="Synthetic centrifugal pump used by the Phase 1 demo scenarios.",
        sensors=PUMP_SENSORS,
    ),
    Asset(
        asset_id="MOTOR-001",
        name="Demo Induction Motor",
        asset_type=AssetType.MOTOR,
        site="DEMO-SITE",
        status=AssetStatus.ACTIVE,
        criticality=Criticality.MEDIUM,
        description="Synthetic induction motor used by the Phase 1 demo scenarios.",
        sensors=MOTOR_SENSORS,
    ),
]


@dataclass(frozen=True)
class InjectionSpec:
    sensor_name: str
    start_index: int
    end_index: int
    direction: AnomalyDirection
    magnitude_start: float
    magnitude_end: float
    failure_mode: str
    injection: str


@dataclass(frozen=True)
class ScenarioSpec:
    scenario_id: str
    name: str
    description: str
    asset_id: str
    image_fixture_id: str
    knowledge_document_ids: tuple[str, ...]
    expected_failure_mode: str | None
    seed_offset: int
    injections: tuple[InjectionSpec, ...]


SCENARIO_SPECS = (
    ScenarioSpec(
        scenario_id="SCENARIO-001",
        name="Pump Seal Leakage",
        description=(
            "Synthetic pump scenario with rising vibration and falling outlet pressure "
            "during a seal-leakage window."
        ),
        asset_id="PUMP-001",
        image_fixture_id="IMAGE-SCENARIO-001",
        knowledge_document_ids=("KNOW-PUMP-MANUAL", "KNOW-INSPECTION-SOP"),
        expected_failure_mode="pump_seal_leakage",
        seed_offset=1,
        injections=(
            InjectionSpec(
                sensor_name="vibration",
                start_index=216,
                end_index=240,
                direction=AnomalyDirection.INCREASE,
                magnitude_start=2.0,
                magnitude_end=3.0,
                failure_mode="pump_seal_leakage",
                injection="linear positive offset added to the normal vibration baseline",
            ),
            InjectionSpec(
                sensor_name="outlet_pressure",
                start_index=216,
                end_index=240,
                direction=AnomalyDirection.DECREASE,
                magnitude_start=1.3,
                magnitude_end=1.8,
                failure_mode="pump_seal_leakage",
                injection="linear negative offset applied to the normal pressure baseline",
            ),
        ),
    ),
    ScenarioSpec(
        scenario_id="SCENARIO-002",
        name="Motor Bearing Fault",
        description=(
            "Synthetic motor scenario with rising bearing vibration and temperature "
            "while current remains within its normal operating band."
        ),
        asset_id="MOTOR-001",
        image_fixture_id="IMAGE-SCENARIO-002",
        knowledge_document_ids=("KNOW-MOTOR-MANUAL", "KNOW-INSPECTION-SOP"),
        expected_failure_mode="motor_bearing_fault",
        seed_offset=2,
        injections=(
            InjectionSpec(
                sensor_name="vibration",
                start_index=192,
                end_index=228,
                direction=AnomalyDirection.INCREASE,
                magnitude_start=1.8,
                magnitude_end=3.0,
                failure_mode="motor_bearing_fault",
                injection="linear positive offset added to the normal vibration baseline",
            ),
            InjectionSpec(
                sensor_name="bearing_temperature",
                start_index=192,
                end_index=228,
                direction=AnomalyDirection.INCREASE,
                magnitude_start=9.0,
                magnitude_end=16.0,
                failure_mode="motor_bearing_fault",
                injection="linear positive offset added to the normal temperature baseline",
            ),
        ),
    ),
    ScenarioSpec(
        scenario_id="SCENARIO-003",
        name="Normal Pump Operation",
        description="Synthetic normal-operation control scenario with no injected anomaly.",
        asset_id="PUMP-001",
        image_fixture_id="IMAGE-SCENARIO-003",
        knowledge_document_ids=("KNOW-PUMP-MANUAL", "KNOW-INSPECTION-SOP"),
        expected_failure_mode=None,
        seed_offset=3,
        injections=(),
    ),
)


IMAGE_FIXTURE_SPECS = (
    {
        "fixture_id": "IMAGE-SCENARIO-001",
        "scenario_id": "SCENARIO-001",
        "asset_id": "PUMP-001",
        "path": "demo/fixtures/images/pump_seal_leak.svg",
        "visual_labels": ["leakage_trace"],
        "description": "Synthetic schematic showing a visible liquid trace below a pump seal.",
    },
    {
        "fixture_id": "IMAGE-SCENARIO-002",
        "scenario_id": "SCENARIO-002",
        "asset_id": "MOTOR-001",
        "path": "demo/fixtures/images/motor_bearing_fault.svg",
        "visual_labels": ["bearing_discoloration"],
        "description": "Synthetic schematic highlighting discoloration around a motor bearing.",
    },
    {
        "fixture_id": "IMAGE-SCENARIO-003",
        "scenario_id": "SCENARIO-003",
        "asset_id": "PUMP-001",
        "path": "demo/fixtures/images/pump_normal.svg",
        "visual_labels": ["no_visible_anomaly"],
        "description": "Synthetic schematic of a pump with no highlighted visual anomaly.",
    },
)


def _centered_noise(random_source: random.Random, scale: float) -> float:
    """Stable bounded pseudo-normal noise based only on Random.random()."""

    centered = sum(random_source.random() for _ in range(6)) - 3.0
    return centered * scale


def _base_sensor_values(
    asset_id: str, index: int, random_source: random.Random
) -> dict[str, float]:
    phase = 2.0 * math.pi * index / SAMPLE_COUNT
    if asset_id == "PUMP-001":
        return {
            "vibration": 2.15
            + 0.12 * math.sin(4.0 * phase)
            + _centered_noise(random_source, 0.08),
            "bearing_temperature": 57.5
            + 1.5 * math.sin(phase - 1.0)
            + _centered_noise(random_source, 0.25),
            "outlet_pressure": 5.8
            + 0.12 * math.sin(2.0 * phase)
            + _centered_noise(random_source, 0.05),
        }
    if asset_id == "MOTOR-001":
        return {
            "vibration": 2.0
            + 0.1 * math.sin(5.0 * phase)
            + _centered_noise(random_source, 0.07),
            "bearing_temperature": 61.0
            + 1.8 * math.sin(phase - 0.6)
            + _centered_noise(random_source, 0.3),
            "current": 21.0
            + 0.8 * math.sin(3.0 * phase)
            + _centered_noise(random_source, 0.2),
        }
    raise ValueError(f"unsupported demo asset: {asset_id}")


def _apply_injections(
    values: dict[str, float], index: int, injections: tuple[InjectionSpec, ...]
) -> None:
    for injection in injections:
        if not injection.start_index <= index < injection.end_index:
            continue
        width = injection.end_index - injection.start_index
        progress = (index - injection.start_index) / max(1, width - 1)
        magnitude = injection.magnitude_start + (
            injection.magnitude_end - injection.magnitude_start
        ) * progress
        sign = 1.0 if injection.direction is AnomalyDirection.INCREASE else -1.0
        values[injection.sensor_name] += sign * magnitude


def _timestamp(index: int) -> datetime:
    return SERIES_START + timedelta(seconds=index * SAMPLE_INTERVAL_SECONDS)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65_536), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_model(path: Path, model: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = model.model_dump_json(indent=2)  # type: ignore[attr-defined]
    path.write_text(payload + "\n", encoding="utf-8", newline="\n")


def _write_sensor_csv(
    path: Path,
    asset: Asset,
    scenario: ScenarioSpec,
    random_seed: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sensor_names = [sensor.sensor_name for sensor in asset.sensors]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["timestamp", *sensor_names],
            lineterminator="\n",
        )
        writer.writeheader()
        random_source = random.Random(random_seed)
        for index in range(SAMPLE_COUNT):
            values = _base_sensor_values(asset.asset_id, index, random_source)
            _apply_injections(values, index, scenario.injections)
            writer.writerow(
                {
                    "timestamp": _format_timestamp(_timestamp(index)),
                    **{name: f"{values[name]:.4f}" for name in sensor_names},
                }
            )


def _build_image_manifest(settings: Settings) -> ImageFixtureManifest:
    fixtures: list[ImageFixture] = []
    for spec in IMAGE_FIXTURE_SPECS:
        path = settings.data_dir / str(spec["path"])
        if not path.is_file():
            raise FileNotFoundError(f"missing fixture image: {path}")
        fixtures.append(
            ImageFixture(
                **spec,
                sha256=_sha256_file(path),
            )
        )
    manifest = ImageFixtureManifest(fixtures=fixtures)
    _write_model(settings.image_manifest_path, manifest)
    return manifest


def _load_knowledge_manifest(settings: Settings) -> KnowledgeManifest:
    if not settings.knowledge_manifest_path.is_file():
        raise FileNotFoundError(
            f"missing knowledge manifest: {settings.knowledge_manifest_path}"
        )
    manifest = KnowledgeManifest.model_validate_json(
        settings.knowledge_manifest_path.read_text(encoding="utf-8")
    )
    for document in manifest.documents:
        path = settings.data_dir / document.path
        if not path.is_file():
            raise FileNotFoundError(f"missing knowledge document: {path}")
    return manifest


def seed_demo(settings: Settings) -> SeedResult:
    """Generate all three scenarios and replace the two-table SQLite demo seed."""

    settings.ensure_directories()
    knowledge_manifest = _load_knowledge_manifest(settings)
    image_manifest = _build_image_manifest(settings)
    assets_by_id = {asset.asset_id: asset for asset in DEMO_ASSETS}
    datasets: list[SensorDatasetMetadata] = []
    hashes: dict[str, str] = {}

    log_event(
        logger,
        logging.INFO,
        "demo_seed_started",
        random_seed=settings.random_seed,
        scenario_count=len(SCENARIO_SPECS),
    )

    for scenario in SCENARIO_SPECS:
        asset = assets_by_id[scenario.asset_id]
        scenario_seed = settings.random_seed + scenario.seed_offset
        scenario_dir = settings.scenarios_dir / scenario.scenario_id
        csv_path = scenario_dir / "sensor_data.csv"
        _write_sensor_csv(csv_path, asset, scenario, scenario_seed)
        sensor_names = [sensor.sensor_name for sensor in asset.sensors]
        digest = _sha256_file(csv_path)

        anomalies = [
            GroundTruthAnomaly(
                sensor_name=injection.sensor_name,
                start_time=_timestamp(injection.start_index),
                end_time=_timestamp(injection.end_index),
                direction=injection.direction,
                failure_mode=injection.failure_mode,
                injection=injection.injection,
            )
            for injection in scenario.injections
        ]
        sensor_data = SensorDataManifest(
            dataset_id=f"DATASET-{scenario.scenario_id}",
            format="csv",
            path="sensor_data.csv",
            sha256=digest,
            random_seed=scenario_seed,
            sensor_columns=sensor_names,
            sample_interval_seconds=SAMPLE_INTERVAL_SECONDS,
            row_count=SAMPLE_COUNT,
            start_time=_timestamp(0),
            end_time=_timestamp(SAMPLE_COUNT - 1),
        )
        manifest = ScenarioManifest(
            scenario_id=scenario.scenario_id,
            name=scenario.name,
            description=scenario.description,
            asset_id=scenario.asset_id,
            image_fixture_id=scenario.image_fixture_id,
            knowledge_document_ids=list(scenario.knowledge_document_ids),
            sensor_data=sensor_data,
            ground_truth=ScenarioGroundTruth(
                is_normal=not scenario.injections,
                expected_failure_mode=scenario.expected_failure_mode,
                sensor_anomalies=anomalies,
            ),
        )
        _write_model(scenario_dir / "manifest.json", manifest)
        relative_path = csv_path.relative_to(settings.data_dir).as_posix()
        datasets.append(
            SensorDatasetMetadata(
                dataset_id=sensor_data.dataset_id,
                scenario_id=scenario.scenario_id,
                asset_id=scenario.asset_id,
                relative_path=relative_path,
                sha256=digest,
                random_seed=scenario_seed,
                row_count=SAMPLE_COUNT,
                sample_interval_seconds=SAMPLE_INTERVAL_SECONDS,
                start_time=sensor_data.start_time,
                end_time=sensor_data.end_time,
                sensor_columns=sensor_names,
            )
        )
        hashes[scenario.scenario_id] = digest

    assert settings.database_path is not None
    repository = SQLiteRepository(settings.database_path)
    repository.initialize_schema()
    repository.replace_demo_data(DEMO_ASSETS, datasets)
    validation = validate_demo(settings)
    if not validation.valid:
        raise RuntimeError("demo validation did not pass")

    result = SeedResult(
        database_path=str(settings.database_path),
        asset_ids=[asset.asset_id for asset in repository.list_assets()],
        scenario_ids=validation.scenario_ids,
        dataset_hashes=hashes,
        image_fixture_count=len(image_manifest.fixtures),
        knowledge_document_count=len(knowledge_manifest.documents),
    )
    log_event(
        logger,
        logging.INFO,
        "demo_seed_completed",
        assets=len(result.asset_ids),
        scenarios=len(result.scenario_ids),
        database_path=result.database_path,
    )
    return result


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def validate_demo(settings: Settings) -> DemoValidationResult:
    """Validate file references, hashes, schemas, and injected anomaly directions."""

    asset_map = {asset.asset_id: asset for asset in DEMO_ASSETS}
    image_manifest = ImageFixtureManifest.model_validate_json(
        settings.image_manifest_path.read_text(encoding="utf-8")
    )
    knowledge_manifest = _load_knowledge_manifest(settings)
    image_map = {fixture.fixture_id: fixture for fixture in image_manifest.fixtures}
    knowledge_ids = {
        document.document_id for document in knowledge_manifest.documents
    }
    manifests: list[ScenarioManifest] = []
    checks: list[str] = []

    manifest_paths = sorted(settings.scenarios_dir.glob("SCENARIO-*/manifest.json"))
    for manifest_path in manifest_paths:
        manifests.append(
            ScenarioManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
        )
    found_ids = {manifest.scenario_id for manifest in manifests}
    if found_ids != EXPECTED_SCENARIO_IDS:
        raise ValueError(
            f"expected scenarios {sorted(EXPECTED_SCENARIO_IDS)}, found {sorted(found_ids)}"
        )

    for manifest, manifest_path in zip(manifests, manifest_paths, strict=True):
        if manifest_path.parent.name != manifest.scenario_id:
            raise ValueError(f"scenario directory mismatch: {manifest_path}")
        asset = asset_map.get(manifest.asset_id)
        if asset is None:
            raise ValueError(f"unknown asset in {manifest.scenario_id}")
        expected_sensors = [sensor.sensor_name for sensor in asset.sensors]
        if manifest.sensor_data.sensor_columns != expected_sensors:
            raise ValueError(f"sensor columns mismatch in {manifest.scenario_id}")

        csv_path = manifest_path.parent / manifest.sensor_data.path
        if not csv_path.is_file():
            raise FileNotFoundError(f"missing sensor CSV: {csv_path}")
        if _sha256_file(csv_path) != manifest.sensor_data.sha256:
            raise ValueError(f"sensor CSV hash mismatch in {manifest.scenario_id}")
        fields, rows = _read_csv(csv_path)
        if fields != [manifest.sensor_data.timestamp_column, *expected_sensors]:
            raise ValueError(f"CSV header mismatch in {manifest.scenario_id}")
        if len(rows) != manifest.sensor_data.row_count:
            raise ValueError(f"CSV row count mismatch in {manifest.scenario_id}")
        timestamps = [datetime.fromisoformat(row["timestamp"]) for row in rows]
        if timestamps[0] != manifest.sensor_data.start_time:
            raise ValueError(f"CSV start timestamp mismatch in {manifest.scenario_id}")
        if timestamps[-1] != manifest.sensor_data.end_time:
            raise ValueError(f"CSV end timestamp mismatch in {manifest.scenario_id}")

        fixture = image_map.get(manifest.image_fixture_id)
        if fixture is None or fixture.scenario_id != manifest.scenario_id:
            raise ValueError(f"image fixture mismatch in {manifest.scenario_id}")
        fixture_path = settings.data_dir / fixture.path
        if not fixture_path.is_file() or _sha256_file(fixture_path) != fixture.sha256:
            raise ValueError(f"image fixture file mismatch in {manifest.scenario_id}")
        if not set(manifest.knowledge_document_ids).issubset(knowledge_ids):
            raise ValueError(f"knowledge reference mismatch in {manifest.scenario_id}")

        for anomaly in manifest.ground_truth.sensor_anomalies:
            if anomaly.sensor_name not in expected_sensors:
                raise ValueError(f"unknown anomaly sensor in {manifest.scenario_id}")
            if anomaly.failure_mode != manifest.ground_truth.expected_failure_mode:
                raise ValueError(f"failure mode mismatch in {manifest.scenario_id}")
            anomaly_values = [
                float(row[anomaly.sensor_name])
                for timestamp, row in zip(timestamps, rows, strict=True)
                if anomaly.start_time <= timestamp < anomaly.end_time
            ]
            baseline_values = [
                float(row[anomaly.sensor_name])
                for timestamp, row in zip(timestamps, rows, strict=True)
                if not anomaly.start_time <= timestamp < anomaly.end_time
            ]
            if not anomaly_values or not baseline_values:
                raise ValueError(f"empty anomaly window in {manifest.scenario_id}")
            delta = fmean(anomaly_values) - fmean(baseline_values)
            if anomaly.direction is AnomalyDirection.INCREASE and delta <= 0:
                raise ValueError(f"increase injection not present in {manifest.scenario_id}")
            if anomaly.direction is AnomalyDirection.DECREASE and delta >= 0:
                raise ValueError(f"decrease injection not present in {manifest.scenario_id}")

        checks.append(
            f"{manifest.scenario_id}: schema, references, CSV hash and ground truth valid"
        )

    if {fixture.scenario_id for fixture in image_manifest.fixtures} != found_ids:
        raise ValueError("fixture image manifest must cover exactly the three scenarios")
    checks.append("image fixture manifest covers exactly three synthetic scenarios")
    checks.append("knowledge manifest references existing synthetic documents")
    return DemoValidationResult(
        valid=True,
        scenario_ids=sorted(found_ids),
        checks=checks,
    )

