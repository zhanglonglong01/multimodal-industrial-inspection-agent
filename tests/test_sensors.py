from __future__ import annotations

import csv
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from inspection_agent.analysis_schemas import AnomalyMethod
from inspection_agent.config import Settings
from inspection_agent.demo import DEMO_ASSETS
from inspection_agent.repository import SQLiteRepository
from inspection_agent.services.sensors import (
    AnomalyDetector,
    RuleBasedAndMADDetector,
    SensorDataQualityService,
)


def _source_csv(settings: Settings, scenario_id: str) -> Path:
    return settings.scenarios_dir / scenario_id / "sensor_data.csv"


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def test_data_quality_accepts_complete_phase1_csv(seeded_demo: Settings) -> None:
    report = SensorDataQualityService().analyze(
        _source_csv(seeded_demo, "SCENARIO-001"),
        ["vibration", "bearing_temperature", "outlet_pressure"],
        expected_sampling_interval_seconds=300,
    )

    assert report.is_usable is True
    assert report.row_count == 288
    assert report.timestamp_parse_errors == 0
    assert report.timestamps_strictly_increasing is True
    assert report.duplicate_timestamp_count == 0
    assert report.observed_sampling_interval_seconds == 300
    assert report.time_span_seconds == 86_100
    assert all(rate == 0 for rate in report.missing_rates.values())


def test_data_quality_reports_missing_sensor_column(tmp_path: Path) -> None:
    path = tmp_path / "missing.csv"
    _write_csv(
        path,
        ["timestamp", "vibration"],
        [{"timestamp": "2025-01-01T00:00:00Z", "vibration": "2.0"}],
    )

    report = SensorDataQualityService().analyze(
        path,
        ["vibration", "bearing_temperature"],
        expected_sampling_interval_seconds=300,
    )

    assert report.is_usable is False
    assert report.missing_columns == ["bearing_temperature"]
    assert report.missing_rates["bearing_temperature"] == 1.0


def test_data_quality_reports_duplicate_timestamp(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.csv"
    rows = [
        {"timestamp": "2025-01-01T00:00:00Z", "vibration": "2.0"},
        {"timestamp": "2025-01-01T00:00:00Z", "vibration": "2.1"},
    ]
    _write_csv(path, ["timestamp", "vibration"], rows)

    report = SensorDataQualityService().analyze(
        path,
        ["vibration"],
        expected_sampling_interval_seconds=300,
    )

    assert report.is_usable is False
    assert report.duplicate_timestamp_count == 1
    assert report.timestamps_strictly_increasing is False


def test_data_quality_reports_missing_and_non_numeric_values(tmp_path: Path) -> None:
    path = tmp_path / "values.csv"
    rows = [
        {"timestamp": "2025-01-01T00:00:00Z", "vibration": ""},
        {"timestamp": "2025-01-01T00:05:00Z", "vibration": "invalid"},
    ]
    _write_csv(path, ["timestamp", "vibration"], rows)

    report = SensorDataQualityService().analyze(
        path,
        ["vibration"],
        expected_sampling_interval_seconds=300,
    )

    assert report.missing_counts["vibration"] == 1
    assert report.non_numeric_counts["vibration"] == 1
    assert report.is_usable is False


def test_data_quality_reports_invalid_timestamp(tmp_path: Path) -> None:
    path = tmp_path / "timestamp.csv"
    _write_csv(
        path,
        ["timestamp", "vibration"],
        [{"timestamp": "not-a-time", "vibration": "2.0"}],
    )

    report = SensorDataQualityService().analyze(
        path,
        ["vibration"],
        expected_sampling_interval_seconds=300,
    )

    assert report.timestamp_parse_errors == 1
    assert report.is_usable is False


def test_data_quality_reports_irregular_sampling_interval(tmp_path: Path) -> None:
    path = tmp_path / "interval.csv"
    rows = [
        {"timestamp": "2025-01-01T00:00:00Z", "vibration": "2.0"},
        {"timestamp": "2025-01-01T00:05:00Z", "vibration": "2.0"},
        {"timestamp": "2025-01-01T00:15:00Z", "vibration": "2.0"},
    ]
    _write_csv(path, ["timestamp", "vibration"], rows)

    report = SensorDataQualityService().analyze(
        path,
        ["vibration"],
        expected_sampling_interval_seconds=300,
    )

    assert report.irregular_interval_count == 1
    assert report.sampling_interval_consistent is False
    assert report.is_usable is False


def test_detector_handles_zero_mad_without_nan(tmp_path: Path) -> None:
    path = tmp_path / "constant.csv"
    start = datetime(2025, 1, 1, tzinfo=UTC)
    rows = [
        {
            "timestamp": (start + timedelta(minutes=5 * index)).isoformat(),
            "vibration": "2.0",
            "bearing_temperature": "60.0",
            "outlet_pressure": "5.8",
        }
        for index in range(100)
    ]
    _write_csv(
        path,
        ["timestamp", "vibration", "bearing_temperature", "outlet_pressure"],
        rows,
    )
    pump = next(asset for asset in DEMO_ASSETS if asset.asset_id == "PUMP-001")
    detector = RuleBasedAndMADDetector()

    result = detector.detect(
        path,
        pump,
        "DATASET-CONSTANT-001",
        expected_sampling_interval_seconds=300,
    )

    assert isinstance(detector, AnomalyDetector)
    assert result.anomaly_points == []
    assert result.segments == []
    assert any("MAD was zero" in warning for warning in result.warnings)


def test_detector_zero_mad_fallback_is_finite_and_detects_run(tmp_path: Path) -> None:
    path = tmp_path / "constant-spike.csv"
    start = datetime(2025, 1, 1, tzinfo=UTC)
    rows = []
    for index in range(100):
        vibration = 3.0 if 50 <= index < 53 else 2.0
        rows.append(
            {
                "timestamp": (start + timedelta(minutes=5 * index)).isoformat(),
                "vibration": vibration,
                "bearing_temperature": 60.0,
                "outlet_pressure": 5.8,
            }
        )
    _write_csv(
        path,
        ["timestamp", "vibration", "bearing_temperature", "outlet_pressure"],
        rows,
    )
    pump = next(asset for asset in DEMO_ASSETS if asset.asset_id == "PUMP-001")

    result = RuleBasedAndMADDetector().detect(
        path,
        pump,
        "DATASET-CONSTANT-002",
        expected_sampling_interval_seconds=300,
    )

    assert len(result.segments) == 1
    assert result.segments[0].sensor_id == "vibration"
    assert AnomalyMethod.MAD_ZERO_FALLBACK.value in result.segments[0].method
    assert all(math.isfinite(point.score) for point in result.anomaly_points)


def test_detector_finds_injected_sensors(seeded_demo: Settings) -> None:
    assert seeded_demo.database_path is not None
    repository = SQLiteRepository(seeded_demo.database_path)
    detector = RuleBasedAndMADDetector()

    pump = repository.get_asset("PUMP-001")
    motor = repository.get_asset("MOTOR-001")
    assert pump is not None and motor is not None
    pump_result = detector.detect(
        _source_csv(seeded_demo, "SCENARIO-001"),
        pump,
        "DATASET-SCENARIO-001",
        expected_sampling_interval_seconds=300,
    )
    motor_result = detector.detect(
        _source_csv(seeded_demo, "SCENARIO-002"),
        motor,
        "DATASET-SCENARIO-002",
        expected_sampling_interval_seconds=300,
    )

    assert {segment.sensor_id for segment in pump_result.segments} == {
        "vibration",
        "outlet_pressure",
    }
    assert {segment.sensor_id for segment in motor_result.segments} == {
        "vibration",
        "bearing_temperature",
    }
    assert any(
        "operating_limit" in segment.method for segment in pump_result.segments
    )


def test_normal_scenario_has_no_anomalies(seeded_demo: Settings) -> None:
    assert seeded_demo.database_path is not None
    pump = SQLiteRepository(seeded_demo.database_path).get_asset("PUMP-001")
    assert pump is not None

    result = RuleBasedAndMADDetector().detect(
        _source_csv(seeded_demo, "SCENARIO-003"),
        pump,
        "DATASET-SCENARIO-003",
        expected_sampling_interval_seconds=300,
    )

    assert result.anomaly_points == []
    assert result.segments == []
