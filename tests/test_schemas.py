from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from inspection_agent.schemas import (
    AnomalyDirection,
    GroundTruthAnomaly,
    ScenarioGroundTruth,
    SensorDefinition,
)


def test_sensor_definition_rejects_reversed_operating_range() -> None:
    with pytest.raises(ValidationError, match="operating_min"):
        SensorDefinition(
            sensor_name="vibration",
            display_name="Vibration",
            unit="mm/s RMS",
            operating_min=10,
            operating_max=1,
        )


def test_ground_truth_requires_timezone_and_valid_window() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        GroundTruthAnomaly(
            sensor_name="vibration",
            start_time=datetime(2025, 1, 1),
            end_time=datetime(2025, 1, 1, 1),
            direction=AnomalyDirection.INCREASE,
            failure_mode="motor_bearing_fault",
            injection="test injection",
        )


def test_normal_ground_truth_cannot_include_anomaly() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    anomaly = GroundTruthAnomaly(
        sensor_name="vibration",
        start_time=start,
        end_time=start + timedelta(hours=1),
        direction=AnomalyDirection.INCREASE,
        failure_mode="motor_bearing_fault",
        injection="test injection",
    )

    with pytest.raises(ValidationError, match="normal scenario"):
        ScenarioGroundTruth(
            is_normal=True,
            expected_failure_mode=None,
            sensor_anomalies=[anomaly],
        )


def test_fault_ground_truth_requires_failure_mode_and_anomaly() -> None:
    with pytest.raises(ValidationError, match="fault scenario"):
        ScenarioGroundTruth(
            is_normal=False,
            expected_failure_mode=None,
            sensor_anomalies=[],
        )
