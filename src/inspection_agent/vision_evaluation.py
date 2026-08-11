"""Opt-in real Vision smoke evaluation, isolated from synthetic ground truth."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from pydantic import Field

from .analysis_schemas import VisionLabel
from .config import Settings
from .repository import SQLiteRepository
from .schemas import ImageFixtureManifest, StrictModel
from .services.vision import OpenAIVisionProvider, resolve_fixture_artifact


class LiveVisionSample(StrictModel):
    artifact_id: str
    asset_id: str
    schema_valid: bool
    expected_labels: list[VisionLabel]
    observed_labels: list[VisionLabel]
    expected_label_overlap: float = Field(ge=0.0, le=1.0)
    latency_ms: float = Field(ge=0.0)
    finding_count: int = Field(ge=0)


class LiveVisionSmokeReport(StrictModel):
    timestamp: datetime
    provider: str
    model: str
    sample_count: int
    schema_success_count: int
    mean_expected_label_overlap: float = Field(ge=0.0, le=1.0)
    mean_latency_ms: float = Field(ge=0.0)
    samples: list[LiveVisionSample]
    limitations: list[str]


def run_live_vision_smoke(
    settings: Settings,
    *,
    output_path: Path | None = None,
) -> LiveVisionSmokeReport:
    """Run three real calls only after an explicit opt-in environment flag."""

    if os.getenv("RUN_LIVE_TESTS") != "1":
        raise RuntimeError("RUN_LIVE_TESTS=1 is required for paid Vision smoke tests")
    if settings.openai_api_key is None:
        raise RuntimeError("INSPECTION_OPENAI_API_KEY is required for live Vision smoke tests")
    if settings.database_path is None:
        raise ValueError("database path is required")

    manifest = ImageFixtureManifest.model_validate_json(
        settings.image_manifest_path.read_text(encoding="utf-8")
    )
    repository = SQLiteRepository(settings.database_path)
    provider = OpenAIVisionProvider(
        settings,
        lambda artifact_id: resolve_fixture_artifact(settings, artifact_id),
    )
    samples: list[LiveVisionSample] = []
    for fixture in manifest.fixtures:
        asset = repository.get_asset(fixture.asset_id)
        if asset is None:
            raise ValueError(f"asset is not seeded: {fixture.asset_id}")

        # Provider execution happens before expected labels are used by this scorer.
        result = provider.analyze(fixture.fixture_id, asset)
        expected = {VisionLabel(item) for item in fixture.visual_labels}
        observed = {item.label for item in result.findings}
        overlap = len(expected & observed) / len(expected) if expected else 0.0
        samples.append(
            LiveVisionSample(
                artifact_id=fixture.fixture_id,
                asset_id=fixture.asset_id,
                schema_valid=True,
                expected_labels=sorted(expected),
                observed_labels=sorted(observed),
                expected_label_overlap=overlap,
                latency_ms=result.latency_ms or 0.0,
                finding_count=len(result.findings),
            )
        )

    report = LiveVisionSmokeReport(
        timestamp=datetime.now(UTC),
        provider=provider.provider_name,
        model=provider.model,
        sample_count=len(samples),
        schema_success_count=sum(item.schema_valid for item in samples),
        mean_expected_label_overlap=(
            sum(item.expected_label_overlap for item in samples) / len(samples)
        ),
        mean_latency_ms=sum(item.latency_ms for item in samples) / len(samples),
        samples=samples,
        limitations=[
            "Only three synthetic schematic demo images were used.",
            "Expected-label overlap is a smoke signal, not industrial vision accuracy.",
            "The report does not measure generalization, production safety, or factory performance.",
        ],
    )
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    return report
