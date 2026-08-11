from __future__ import annotations

import json

import pytest

from inspection_agent.cli import main
from inspection_agent.config import Settings
from inspection_agent.demo import seed_demo, validate_demo
from inspection_agent.repository import SQLiteRepository
from inspection_agent.schemas import ScenarioManifest

EXPECTED_ASSET_SENSORS = {
    "MOTOR-001": ["vibration", "bearing_temperature", "current"],
    "PUMP-001": ["vibration", "bearing_temperature", "outlet_pressure"],
}


def _manifest(settings: Settings, scenario_id: str) -> ScenarioManifest:
    path = settings.scenarios_dir / scenario_id / "manifest.json"
    return ScenarioManifest.model_validate_json(path.read_text(encoding="utf-8"))


def test_seed_creates_only_two_tables_and_two_assets(seeded_demo: Settings) -> None:
    assert seeded_demo.database_path is not None
    repository = SQLiteRepository(seeded_demo.database_path)

    assert repository.table_names() == ["assets", "sensor_datasets"]
    assets = repository.list_assets()
    assert [asset.asset_id for asset in assets] == ["MOTOR-001", "PUMP-001"]
    assert {
        asset.asset_id: [sensor.sensor_name for sensor in asset.sensors]
        for asset in assets
    } == EXPECTED_ASSET_SENSORS


def test_seed_queries_pump_and_motor(seeded_demo: Settings) -> None:
    assert seeded_demo.database_path is not None
    repository = SQLiteRepository(seeded_demo.database_path)

    pump = repository.get_asset("PUMP-001")
    motor = repository.get_asset("MOTOR-001")

    assert pump is not None and pump.asset_type == "pump"
    assert motor is not None and motor.asset_type == "motor"
    assert repository.get_asset("COMPRESSOR-001") is None


def test_manifests_capture_exact_ground_truth(seeded_demo: Settings) -> None:
    pump_leak = _manifest(seeded_demo, "SCENARIO-001")
    motor_bearing = _manifest(seeded_demo, "SCENARIO-002")
    normal = _manifest(seeded_demo, "SCENARIO-003")

    assert pump_leak.asset_id == "PUMP-001"
    assert pump_leak.ground_truth.expected_failure_mode == "pump_seal_leakage"
    assert {
        (item.sensor_name, item.direction.value)
        for item in pump_leak.ground_truth.sensor_anomalies
    } == {("vibration", "increase"), ("outlet_pressure", "decrease")}
    assert {
        (item.start_time.hour, item.end_time.hour)
        for item in pump_leak.ground_truth.sensor_anomalies
    } == {(18, 20)}

    assert motor_bearing.asset_id == "MOTOR-001"
    assert motor_bearing.ground_truth.expected_failure_mode == "motor_bearing_fault"
    assert {
        (item.sensor_name, item.direction.value)
        for item in motor_bearing.ground_truth.sensor_anomalies
    } == {("vibration", "increase"), ("bearing_temperature", "increase")}
    assert {
        (item.start_time.hour, item.end_time.hour)
        for item in motor_bearing.ground_truth.sensor_anomalies
    } == {(16, 19)}
    assert "current" not in {
        item.sensor_name for item in motor_bearing.ground_truth.sensor_anomalies
    }

    assert normal.asset_id == "PUMP-001"
    assert normal.ground_truth.is_normal is True
    assert normal.ground_truth.expected_failure_mode is None
    assert normal.ground_truth.sensor_anomalies == []


def test_seed_is_byte_reproducible(demo_settings: Settings) -> None:
    first = seed_demo(demo_settings)
    first_csv = {
        scenario_id: (
            demo_settings.scenarios_dir / scenario_id / "sensor_data.csv"
        ).read_bytes()
        for scenario_id in first.scenario_ids
    }

    second = seed_demo(demo_settings)
    second_csv = {
        scenario_id: (
            demo_settings.scenarios_dir / scenario_id / "sensor_data.csv"
        ).read_bytes()
        for scenario_id in second.scenario_ids
    }

    assert first.dataset_hashes == second.dataset_hashes
    assert first_csv == second_csv


def test_validation_checks_three_complete_scenarios(seeded_demo: Settings) -> None:
    result = validate_demo(seeded_demo)

    assert result.valid is True
    assert result.scenario_ids == ["SCENARIO-001", "SCENARIO-002", "SCENARIO-003"]
    assert len(result.checks) == 5


def test_seed_writes_fixture_and_knowledge_manifests(seeded_demo: Settings) -> None:
    image_payload = json.loads(
        seeded_demo.image_manifest_path.read_text(encoding="utf-8")
    )
    knowledge_payload = json.loads(
        seeded_demo.knowledge_manifest_path.read_text(encoding="utf-8")
    )

    assert len(image_payload["fixtures"]) == 3
    assert all(item["fixture_only"] for item in image_payload["fixtures"])
    assert len(knowledge_payload["documents"]) == 3
    assert all(item["synthetic"] for item in knowledge_payload["documents"])


def test_cli_seed_list_and_validate(
    demo_settings: Settings,
    capsys: pytest.CaptureFixture[str],
) -> None:
    common_args = [
        "--data-dir",
        str(demo_settings.data_dir),
        "--database-path",
        str(demo_settings.database_path),
        "--log-level",
        "CRITICAL",
    ]

    assert main([*common_args, "seed-demo"]) == 0
    seed_output = json.loads(capsys.readouterr().out)
    assert seed_output["asset_ids"] == ["MOTOR-001", "PUMP-001"]

    assert main([*common_args, "list-assets"]) == 0
    asset_output = json.loads(capsys.readouterr().out)
    assert [item["asset_id"] for item in asset_output] == ["MOTOR-001", "PUMP-001"]

    assert main([*common_args, "validate-demo"]) == 0
    validation_output = json.loads(capsys.readouterr().out)
    assert validation_output["valid"] is True
