from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from inspection_agent.config import Settings
from inspection_agent.demo import DEMO_ASSETS
from inspection_agent.services.vision import (
    FixtureVisionProvider,
    OpenAIVisionProvider,
    VisionProvider,
)

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
    image = seeded_demo.fixtures_dir / "images" / "pump_seal_leak.png"
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


class _FakeResponses:
    def __init__(self, output: dict[str, object]) -> None:
        self.output = output
        self.calls: list[dict[str, object]] = []

    def parse(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(output_parsed=self.output)


class _FakeOpenAI:
    def __init__(self, output: dict[str, object]) -> None:
        self.responses = _FakeResponses(output)


def _openai_output() -> dict[str, object]:
    return {
        "image_quality": {"rating": "good", "usable": True, "notes": []},
        "findings": [
            {
                "label": "leakage_trace",
                "observation": "A blue liquid-like trace is visible below the housing.",
                "severity": "medium",
                "confidence": 0.86,
                "region": {
                    "x": 0.4,
                    "y": 0.5,
                    "width": 0.2,
                    "height": 0.3,
                    "description": "Trace below the housing",
                },
            }
        ],
        "negative_findings": ["corrosion", "crack_like_mark"],
        "limitations": ["Single synthetic image; depth and material cannot be verified."],
    }


def test_openai_vision_reads_pixels_and_returns_existing_schema(
    seeded_demo: Settings, tmp_path: Path
) -> None:
    image_path = tmp_path / "uploaded.png"
    Image.new("RGB", (32, 24), color=(20, 130, 180)).save(image_path)
    fake = _FakeOpenAI(_openai_output())
    provider = OpenAIVisionProvider(
        seeded_demo.model_copy(update={"openai_vision_model": "vision-test-model"}),
        lambda artifact_id: image_path,
        client=fake,
    )

    result = provider.analyze("ARTIFACT-UPLOAD-001", ASSETS["PUMP-001"])

    assert isinstance(provider, VisionProvider)
    assert result.provider == "openai_responses_vision"
    assert result.model == "vision-test-model"
    assert result.fixture is False
    assert result.latency_ms is not None and result.latency_ms >= 0
    assert result.findings[0].label == "leakage_trace"
    assert result.findings[0].evidence_id.startswith("EVIDENCE-VISION-")

    call = fake.responses.calls[0]
    serialized = json.dumps(
        {key: value for key, value in call.items() if key != "text_format"},
        ensure_ascii=False,
    )
    assert "data:image/png;base64," in serialized
    assert "allowed_visual_labels" in serialized
    assert "SCENARIO-" not in serialized
    assert "ground_truth" not in serialized
    assert "expected_failure_mode" not in serialized


def test_openai_vision_rejects_inconsistent_structured_output(
    seeded_demo: Settings, tmp_path: Path
) -> None:
    image_path = tmp_path / "uploaded.png"
    Image.new("RGB", (16, 16)).save(image_path)
    output = _openai_output()
    output["negative_findings"] = ["leakage_trace"]
    provider = OpenAIVisionProvider(
        seeded_demo,
        lambda artifact_id: image_path,
        client=_FakeOpenAI(output),
    )

    with pytest.raises(ValueError, match="both observed and negative"):
        provider.analyze("ARTIFACT-UPLOAD-002", ASSETS["PUMP-001"])


def test_openai_vision_requires_key_without_injected_client(seeded_demo: Settings) -> None:
    settings = seeded_demo.model_copy(update={"openai_api_key": None})
    with pytest.raises(ValueError, match="INSPECTION_OPENAI_API_KEY"):
        OpenAIVisionProvider(
            settings,
            lambda artifact_id: settings.data_dir / "missing.png",
        )
