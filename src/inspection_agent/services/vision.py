"""Vision provider contract and deterministic fixture implementation."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..analysis_schemas import (
    ImageQuality,
    ImageQualityRating,
    ImageRegion,
    Severity,
    VisionAnalysisResult,
    VisionFinding,
    VisionLabel,
)
from ..config import Settings
from ..schemas import Asset, ImageFixture, ImageFixtureManifest, ScenarioManifest


@runtime_checkable
class VisionProvider(Protocol):
    """Interface implemented by fixture and future real vision providers."""

    def analyze(self, artifact_id: str, asset_context: Asset) -> VisionAnalysisResult:
        """Analyze one registered image artifact for the supplied asset context."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65_536), b""):
            digest.update(block)
    return digest.hexdigest()


_FIXTURE_FINDINGS: dict[str, dict[str, object]] = {
    "SCENARIO-001": {
        "label": VisionLabel.LEAKAGE_TRACE,
        "observation": "A synthetic liquid trace is highlighted below the pump seal area.",
        "severity": Severity.MEDIUM,
        "region": ImageRegion(
            x=0.42,
            y=0.52,
            width=0.24,
            height=0.38,
            description="Highlighted trace beneath the seal housing",
        ),
        "negative_findings": [
            VisionLabel.CORROSION,
            VisionLabel.CRACK_LIKE_MARK,
            VisionLabel.LOOSE_COMPONENT,
            VisionLabel.FOREIGN_OBJECT,
        ],
    },
    "SCENARIO-002": {
        "label": VisionLabel.DISCOLORATION,
        "observation": "Synthetic discoloration is highlighted around the motor bearing housing.",
        "severity": Severity.MEDIUM,
        "region": ImageRegion(
            x=0.68,
            y=0.24,
            width=0.2,
            height=0.44,
            description="Highlighted motor bearing housing",
        ),
        "negative_findings": [
            VisionLabel.LEAKAGE_TRACE,
            VisionLabel.CRACK_LIKE_MARK,
            VisionLabel.LOOSE_COMPONENT,
            VisionLabel.FOREIGN_OBJECT,
        ],
    },
    "SCENARIO-003": {
        "label": VisionLabel.NO_VISIBLE_ANOMALY,
        "observation": "The normal synthetic fixture contains no highlighted visible anomaly.",
        "severity": Severity.INFO,
        "region": None,
        "negative_findings": [
            VisionLabel.LEAKAGE_TRACE,
            VisionLabel.CORROSION,
            VisionLabel.CRACK_LIKE_MARK,
            VisionLabel.LOOSE_COMPONENT,
            VisionLabel.DISCOLORATION,
            VisionLabel.FOREIGN_OBJECT,
        ],
    },
}


class FixtureVisionProvider:
    """Return declared findings only for versioned repository fixtures.

    This provider does not inspect pixels and cannot generalize to uploaded images. Its
    output always carries ``fixture=true`` and an explicit limitation.
    """

    provider_name = "fixture"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def analyze(self, artifact_id: str, asset_context: Asset) -> VisionAnalysisResult:
        manifest = self._load_manifest()
        fixture = next(
            (item for item in manifest.fixtures if item.fixture_id == artifact_id),
            None,
        )
        if fixture is None:
            raise KeyError(f"fixture image is not registered: {artifact_id}")
        if fixture.asset_id != asset_context.asset_id:
            raise ValueError(
                f"fixture {artifact_id} belongs to {fixture.asset_id}, "
                f"not {asset_context.asset_id}"
            )
        self._validate_fixture_file(fixture)

        spec = _FIXTURE_FINDINGS.get(fixture.scenario_id)
        if spec is None:
            raise ValueError(
                f"fixture has no declared analysis data: {fixture.scenario_id}"
            )
        scenario = self._load_scenario(fixture.scenario_id)
        finding_id = f"FINDING-{artifact_id}-001"
        finding = VisionFinding(
            finding_id=finding_id,
            label=spec["label"],
            observation=str(spec["observation"]),
            severity=spec["severity"],
            confidence=1.0,
            region=spec["region"],
            evidence_id=f"EVIDENCE-VISION-{finding_id}",
            observed_at=scenario.sensor_data.start_time,
        )
        return VisionAnalysisResult(
            artifact_id=artifact_id,
            asset_id=asset_context.asset_id,
            image_quality=ImageQuality(
                rating=ImageQualityRating.GOOD,
                usable=True,
                notes=["Quality is declared by the versioned synthetic fixture manifest."],
            ),
            findings=[finding],
            negative_findings=spec["negative_findings"],
            limitations=[
                "Preset fixture result; no pixel inference or real vision model was run."
            ],
            provider=self.provider_name,
            fixture=True,
        )

    def _load_manifest(self) -> ImageFixtureManifest:
        path = self.settings.image_manifest_path
        if not path.is_file():
            raise FileNotFoundError(f"fixture image manifest not found: {path}")
        return ImageFixtureManifest.model_validate_json(path.read_text(encoding="utf-8"))

    def _load_scenario(self, scenario_id: str) -> ScenarioManifest:
        path = self.settings.scenarios_dir / scenario_id / "manifest.json"
        if not path.is_file():
            raise FileNotFoundError(f"scenario manifest not found: {path}")
        return ScenarioManifest.model_validate_json(path.read_text(encoding="utf-8"))

    def _validate_fixture_file(self, fixture: ImageFixture) -> None:
        path = self.settings.data_dir / fixture.path
        if not path.is_file():
            raise FileNotFoundError(f"fixture image not found: {path}")
        if _sha256(path) != fixture.sha256:
            raise ValueError(f"fixture image hash mismatch: {fixture.fixture_id}")
