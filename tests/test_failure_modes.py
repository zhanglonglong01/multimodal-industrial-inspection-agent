from __future__ import annotations

from inspection_agent.config import Settings
from inspection_agent.services.failure_modes import FailureModeRepository


def test_failure_mode_repository_returns_asset_modes(seeded_demo: Settings) -> None:
    repository = FailureModeRepository(seeded_demo.failure_modes_path)

    pump_modes = repository.get_failure_modes("pump")
    motor_modes = repository.get_failure_modes("motor")

    assert [mode.mode_id for mode in pump_modes] == ["pump_seal_leakage"]
    assert [mode.mode_id for mode in motor_modes] == ["motor_bearing_fault"]
    assert pump_modes[0].possible_causes
    assert pump_modes[0].recommended_checks
    assert pump_modes[0].source.startswith("KNOW-PUMP-MANUAL")


def test_failure_mode_repository_filters_visual_label(seeded_demo: Settings) -> None:
    repository = FailureModeRepository(seeded_demo.failure_modes_path)

    assert repository.get_failure_modes(
        "pump", visual_label="leakage_trace"
    )[0].mode_id == "pump_seal_leakage"
    assert repository.get_failure_modes("pump", visual_label="discoloration") == []


def test_failure_mode_repository_filters_sensor_anomaly(seeded_demo: Settings) -> None:
    repository = FailureModeRepository(seeded_demo.failure_modes_path)

    assert repository.get_failure_modes(
        "motor", sensor_anomaly="bearing_temperature"
    )[0].mode_id == "motor_bearing_fault"
    assert repository.get_failure_modes("motor", sensor_anomaly="outlet_pressure") == []
