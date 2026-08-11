"""Vision provider contract and deterministic fixture implementation."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

from PIL import Image, UnidentifiedImageError
from pydantic import Field, model_validator

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
from ..repository import SQLiteRepository
from ..schemas import Asset, ImageFixture, ImageFixtureManifest, StrictModel


@runtime_checkable
class VisionProvider(Protocol):
    """Interface implemented by fixture and future real vision providers."""

    def analyze(self, artifact_id: str, asset_context: Asset) -> VisionAnalysisResult:
        """Analyze one registered image artifact for the supplied asset context."""


class _OpenAIVisionFinding(StrictModel):
    label: VisionLabel
    observation: str = Field(min_length=1)
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    region: ImageRegion | None = None


class _OpenAIVisionOutput(StrictModel):
    image_quality: ImageQuality
    findings: list[_OpenAIVisionFinding]
    negative_findings: list[VisionLabel]
    limitations: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def observations_are_consistent(self) -> "_OpenAIVisionOutput":
        observed = {item.label for item in self.findings}
        overlap = observed & set(self.negative_findings)
        if overlap:
            raise ValueError(f"labels cannot be both observed and negative: {sorted(overlap)}")
        if VisionLabel.NO_VISIBLE_ANOMALY in observed and len(observed) > 1:
            raise ValueError("no_visible_anomaly cannot be combined with anomaly findings")
        return self


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
        if self.settings.database_path is None:
            raise ValueError("database path is required for fixture timestamps")
        dataset = SQLiteRepository(self.settings.database_path).get_sensor_dataset(
            fixture.scenario_id
        )
        if dataset is None:
            raise ValueError(
                f"fixture scenario is not seeded: {fixture.scenario_id}"
            )
        finding_id = f"FINDING-{artifact_id}-001"
        finding = VisionFinding.model_validate(
            {
                "finding_id": finding_id,
                "label": spec["label"],
                "observation": str(spec["observation"]),
                "severity": spec["severity"],
                "confidence": 1.0,
                "region": spec["region"],
                "evidence_id": f"EVIDENCE-VISION-{finding_id}",
                "observed_at": dataset.start_time,
            }
        )
        negative_findings = cast(list[VisionLabel], spec["negative_findings"])
        return VisionAnalysisResult(
            artifact_id=artifact_id,
            asset_id=asset_context.asset_id,
            image_quality=ImageQuality(
                rating=ImageQualityRating.GOOD,
                usable=True,
                notes=["Quality is declared by the versioned synthetic fixture manifest."],
            ),
            findings=[finding],
            negative_findings=negative_findings,
            limitations=[
                "Preset fixture result; no pixel inference or real vision model was run."
            ],
            provider=self.provider_name,
            model=None,
            latency_ms=None,
            fixture=True,
        )

    def _load_manifest(self) -> ImageFixtureManifest:
        path = self.settings.image_manifest_path
        if not path.is_file():
            raise FileNotFoundError(f"fixture image manifest not found: {path}")
        return ImageFixtureManifest.model_validate_json(path.read_text(encoding="utf-8"))

    def _validate_fixture_file(self, fixture: ImageFixture) -> None:
        path = self.settings.data_dir / fixture.path
        if not path.is_file():
            raise FileNotFoundError(f"fixture image not found: {path}")
        if _sha256(path) != fixture.sha256:
            raise ValueError(f"fixture image hash mismatch: {fixture.fixture_id}")


class OpenAIVisionProvider:
    """Optional pixel-reading provider constrained to visual observations.

    The provider receives only an image artifact and non-evaluative asset context. It
    never reads scenario manifests, failure-mode expectations, or evaluation labels.
    """

    provider_name = "openai_responses_vision"
    _SUPPORTED_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}

    def __init__(
        self,
        settings: Settings,
        artifact_resolver: Callable[[str], Path],
        *,
        client: Any | None = None,
    ) -> None:
        if client is None:
            if settings.openai_api_key is None:
                raise ValueError("INSPECTION_OPENAI_API_KEY is required")
            from openai import OpenAI

            client = OpenAI(api_key=settings.openai_api_key.get_secret_value())
        self.model = settings.openai_vision_model
        self.client = client
        self._artifact_resolver = artifact_resolver

    def analyze(self, artifact_id: str, asset_context: Asset) -> VisionAnalysisResult:
        image_path = self._artifact_resolver(artifact_id).resolve()
        image_url = self._image_data_url(image_path)
        labels = [item.value for item in VisionLabel]
        asset_payload = {
            "asset_type": asset_context.asset_type.value,
            "asset_name": asset_context.name,
            "description": asset_context.description,
        }
        started = time.perf_counter()
        response = self.client.responses.parse(
            model=self.model,
            instructions=(
                "You are a visual inspection observation module. Report only what is "
                "visible in the image. Do not diagnose equipment faults, infer root causes, "
                "or claim a failure mode is confirmed. Use only the supplied visual labels."
            ),
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps(
                                {
                                    "task": "Describe visible inspection observations.",
                                    "asset_context": asset_payload,
                                    "allowed_visual_labels": labels,
                                    "region_format": (
                                        "Normalized x, y, width, height in [0,1], or null."
                                    ),
                                },
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
                        },
                        {"type": "input_image", "image_url": image_url, "detail": "high"},
                    ],
                }
            ],
            text_format=_OpenAIVisionOutput,
            store=False,
        )
        latency_ms = (time.perf_counter() - started) * 1000.0
        parsed_value = response.output_parsed
        if parsed_value is None:
            raise ValueError("vision provider returned no parsed structured output")
        parsed = _OpenAIVisionOutput.model_validate(parsed_value)
        observed_at = datetime.now(UTC)
        findings = []
        for index, item in enumerate(parsed.findings, start=1):
            finding_id = f"FINDING-{artifact_id}-{index:03d}"
            findings.append(
                VisionFinding(
                    finding_id=finding_id,
                    label=item.label,
                    observation=item.observation,
                    severity=item.severity,
                    confidence=item.confidence,
                    region=item.region,
                    evidence_id=f"EVIDENCE-VISION-{finding_id}",
                    observed_at=observed_at,
                )
            )
        return VisionAnalysisResult(
            artifact_id=artifact_id,
            asset_id=asset_context.asset_id,
            image_quality=parsed.image_quality,
            findings=findings,
            negative_findings=parsed.negative_findings,
            limitations=parsed.limitations,
            provider=self.provider_name,
            model=self.model,
            latency_ms=latency_ms,
            fixture=False,
        )

    @classmethod
    def _image_data_url(cls, path: Path) -> str:
        if not path.is_file():
            raise FileNotFoundError(f"image artifact not found: {path}")
        try:
            with Image.open(path) as image:
                image.load()
                image_format = image.format
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise ValueError("image artifact cannot be decoded") from exc
        media_type = cls._SUPPORTED_FORMATS.get(str(image_format))
        if media_type is None:
            raise ValueError("OpenAI Vision accepts only PNG, JPEG, or WebP artifacts")
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{media_type};base64,{encoded}"


def resolve_fixture_artifact(settings: Settings, artifact_id: str) -> Path:
    """Resolve only the image path from the fixture registry for CLI live smoke use."""

    manifest = ImageFixtureManifest.model_validate_json(
        settings.image_manifest_path.read_text(encoding="utf-8")
    )
    fixture = next((item for item in manifest.fixtures if item.fixture_id == artifact_id), None)
    if fixture is None:
        raise KeyError(f"fixture image is not registered: {artifact_id}")
    path = (settings.data_dir / fixture.path).resolve()
    data_root = settings.data_dir.resolve()
    if data_root not in path.parents:
        raise ValueError("fixture artifact escaped the data directory")
    return path
