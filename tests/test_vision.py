from __future__ import annotations

from pathlib import Path

import pytest

from inspection_agent.config import Settings
from inspection_agent.demo import DEMO_ASSETS
from inspection_agent.services.vision import FixtureVisionProvider, VisionProvider


ASSETS = {asset.asset_id: asset for asset in DEMO_ASSETS}


@pytest.mark.parametrize(
    ("artifact_id", "asset_id", "expected_label"),
    [
        ("IMAGE-SCENARIO-001", "PUMP-001", "leakage_trace"),
        ("IMAGE-SCENARIO-002", "MOTOR-001", "discoloration"),
        ("IMAGE-SCENARIO-003", "PUMP-001", "no_visible_anomaly"),
    ],
)
def test_fixture_vision_returns_declared_scenario_finding(
    seeded_demo: Settings,
    artifact_id: str,
    asset_id: str,
    expected_label: str,
) -> None:
    provider = FixtureVisionProvider(seeded_demo)

    result = provider.analyze(artifact_id, ASSETS[asset_id])

    assert isinstance(provider, VisionProvider)
    assert result.fixture is True
    assert result.provider == "fixture"
    assert result.findings[0].label == expected_label
    assert result.findings[0].evidence_id.startswith("EVIDENCE-VISION-")
    assert "no pixel inference" in result.limitations[0].lower()


def test_fixture_vision_rejects_unknown_artifact(seeded_demo: Settings) -> None:
    with pytest.raises(KeyError, match="not registered"):
        FixtureVisionProvider(seeded_demo).analyze(
            "IMAGE-SCENARIO-999",
            ASSETS["PUMP-001"],
        )


def test_fixture_vision_rejects_wrong_asset(seeded_demo: Settings) -> None:
    with pytest.raises(ValueError, match="belongs to PUMP-001"):
        FixtureVisionProvider(seeded_demo).analyze(
            "IMAGE-SCENARIO-001",
            ASSETS["MOTOR-001"],
        )


def test_fixture_vision_rejects_missing_image(seeded_demo: Settings) -> None:
    image = seeded_demo.fixtures_dir / "images" / "pump_seal_leak.svg"
    image.unlink()

    with pytest.raises(FileNotFoundError, match="fixture image not found"):
        FixtureVisionProvider(seeded_demo).analyze(
            "IMAGE-SCENARIO-001",
            ASSETS["PUMP-001"],
        )


def test_fixture_vision_rejects_missing_manifest(seeded_demo: Settings) -> None:
    seeded_demo.image_manifest_path.unlink()

    with pytest.raises(FileNotFoundError, match="manifest not found"):
        FixtureVisionProvider(seeded_demo).analyze(
            "IMAGE-SCENARIO-001",
            ASSETS["PUMP-001"],
        )
