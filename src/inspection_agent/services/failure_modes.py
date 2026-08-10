"""File-backed controlled failure-mode lookup."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field

from ..analysis_schemas import FailureMode, SensorId, VisionLabel
from ..schemas import AssetType, StrictModel


class FailureModeCatalog(StrictModel):
    schema_version: str = "1.0"
    synthetic: bool = True
    failure_modes: list[FailureMode] = Field(min_length=1)


class FailureModeRepository:
    """Load a small, versioned JSON catalog without adding database tables."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).resolve()
        self._catalog: FailureModeCatalog | None = None

    def get_failure_modes(
        self,
        asset_type: AssetType | str,
        *,
        visual_label: VisionLabel | str | None = None,
        sensor_anomaly: SensorId | str | None = None,
    ) -> list[FailureMode]:
        normalized_asset_type = AssetType(asset_type)
        normalized_label = VisionLabel(visual_label) if visual_label else None
        catalog = self._load()
        results = [
            mode
            for mode in catalog.failure_modes
            if mode.asset_type is normalized_asset_type
        ]
        if normalized_label is not None:
            results = [mode for mode in results if normalized_label in mode.visual_labels]
        if sensor_anomaly is not None:
            results = [
                mode for mode in results if str(sensor_anomaly) in mode.related_sensors
            ]
        return results

    def _load(self) -> FailureModeCatalog:
        if self._catalog is not None:
            return self._catalog
        if not self.path.is_file():
            raise FileNotFoundError(f"failure-mode catalog not found: {self.path}")
        catalog = FailureModeCatalog.model_validate(
            json.loads(self.path.read_text(encoding="utf-8"))
        )
        mode_ids = [mode.mode_id for mode in catalog.failure_modes]
        if len(mode_ids) != len(set(mode_ids)):
            raise ValueError("failure-mode IDs must be unique")
        self._catalog = catalog
        return catalog
