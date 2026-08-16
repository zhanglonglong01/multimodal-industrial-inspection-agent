"""Provenance-preserving integration for the real MetroPT-3 sensor dataset."""

from __future__ import annotations

import csv
import hashlib
import io
import math
import time
import urllib.request
import zipfile
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import IO, Literal

from pydantic import Field, field_validator, model_validator

from .analysis_schemas import DetectorParameters
from .config import Settings
from .schemas import (
    Asset,
    AssetStatus,
    AssetType,
    Criticality,
    SensorDefinition,
    Sha256,
    StrictModel,
)
from .services.sensors import RuleBasedAndMADDetector

METROPT3_UCI_URL = "https://archive.ics.uci.edu/dataset/791/metropt%2B3%2B"
METROPT3_DOWNLOAD_URL = (
    "https://archive.ics.uci.edu/static/public/791/metropt%2B3%2Bdataset.zip"
)
METROPT3_DOI = "10.24432/C5VW3R"
METROPT3_LICENSE = "CC BY 4.0"
METROPT3_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
METROPT3_ARCHIVE_SHA256 = (
    "aab991a970e58210de853bb8078ce0e63abb4d9412fdc5c79792dae3d8e1721a"
)
METROPT3_CSV_SHA256 = (
    "db30ccb4ea402e3c8bf2c99db06e288d4f2a772f6928f9dbe26a920d69793e24"
)
METROPT3_CSV_MEMBER = "MetroPT3(AirCompressor).csv"
METROPT3_OBSERVED_ROW_COUNT = 1_516_948
METROPT3_SOURCE_COLUMNS = (
    "TP2",
    "TP3",
    "Oil_temperature",
    "Motor_current",
)
METROPT3_COLUMN_MAP = {
    "TP2": "compressor_pressure",
    "TP3": "pneumatic_panel_pressure",
    "Oil_temperature": "oil_temperature",
    "Motor_current": "motor_current",
}
METROPT3_CANONICAL_COLUMNS = tuple(METROPT3_COLUMN_MAP.values())
METROPT3_TIMESTAMP_ASSUMPTION = (
    "Source timestamps have no timezone. Their clock values are preserved and encoded "
    "with +00:00 solely to satisfy the application's timezone-aware storage contract; "
    "this does not assert that the source clock was UTC."
)


class MetroPT3FailureEvent(StrictModel):
    event_id: str = Field(pattern=r"^METROPT3-EVENT-[0-9]{2}$")
    source_report_number: str
    start_time_source: datetime
    end_time_source: datetime
    failure: Literal["Air leak"]
    severity: Literal["High stress"]
    report_note: str | None = None

    @model_validator(mode="after")
    def validate_source_window(self) -> "MetroPT3FailureEvent":
        if self.start_time_source.tzinfo is not None or self.end_time_source.tzinfo is not None:
            raise ValueError("MetroPT-3 source event timestamps must remain timezone-naive")
        if self.start_time_source >= self.end_time_source:
            raise ValueError("failure event start must be earlier than end")
        return self


METROPT3_FAILURE_EVENTS = (
    MetroPT3FailureEvent(
        event_id="METROPT3-EVENT-01",
        source_report_number="#1",
        start_time_source=datetime(2020, 4, 18, 0, 0),
        end_time_source=datetime(2020, 4, 18, 23, 59),
        failure="Air leak",
        severity="High stress",
    ),
    MetroPT3FailureEvent(
        event_id="METROPT3-EVENT-02",
        source_report_number="#1",
        start_time_source=datetime(2020, 5, 29, 23, 30),
        end_time_source=datetime(2020, 5, 30, 6, 0),
        failure="Air leak",
        severity="High stress",
        report_note="Maintenance on 30 Apr at 12:00 (transcribed as published).",
    ),
    MetroPT3FailureEvent(
        event_id="METROPT3-EVENT-03",
        source_report_number="#3",
        start_time_source=datetime(2020, 6, 5, 10, 0),
        end_time_source=datetime(2020, 6, 7, 14, 30),
        failure="Air leak",
        severity="High stress",
        report_note="Maintenance on 8 Jun at 16:00.",
    ),
    MetroPT3FailureEvent(
        event_id="METROPT3-EVENT-04",
        source_report_number="#4",
        start_time_source=datetime(2020, 7, 15, 14, 30),
        end_time_source=datetime(2020, 7, 15, 19, 0),
        failure="Air leak",
        severity="High stress",
        report_note="Maintenance on 16 Jul at 00:00.",
    ),
)


@dataclass(frozen=True)
class _WindowSelection:
    window_id: str
    source_start: datetime
    source_end_exclusive: datetime
    report_relation: Literal[
        "within_reported_failure", "outside_reported_failure"
    ]
    failure_event_id: str | None
    output_name: str


METROPT3_WINDOW_SELECTIONS = (
    _WindowSelection(
        window_id="METROPT3-REFERENCE-20200410",
        source_start=datetime(2020, 4, 10, 1, 0),
        source_end_exclusive=datetime(2020, 4, 10, 7, 0),
        report_relation="outside_reported_failure",
        failure_event_id=None,
        output_name="reference_20200410_0100_0700.csv",
    ),
    _WindowSelection(
        window_id="METROPT3-AIR-LEAK-20200418",
        source_start=datetime(2020, 4, 18, 1, 0),
        source_end_exclusive=datetime(2020, 4, 18, 7, 0),
        report_relation="within_reported_failure",
        failure_event_id="METROPT3-EVENT-01",
        output_name="air_leak_20200418_0100_0700.csv",
    ),
)


class MetroPT3Source(StrictModel):
    title: str
    doi: str
    uci_url: str
    download_url: str
    license: str
    license_url: str
    archive_sha256: Sha256
    csv_member: str
    csv_sha256: Sha256
    retrieved_on: date
    observed_row_count: int = Field(gt=0)
    observed_first_timestamp: datetime
    observed_last_timestamp: datetime
    dominant_observed_interval_seconds: int = Field(gt=0)
    source_timestamp_timezone: Literal["not provided"]
    timestamp_normalization: str


class MetroPT3Window(StrictModel):
    window_id: str = Field(pattern=r"^METROPT3-[A-Z0-9-]+$")
    path: str
    sha256: Sha256
    source_start: datetime
    source_end_exclusive: datetime
    normalized_start: datetime
    normalized_end_exclusive: datetime
    sample_interval_seconds: int = 60
    row_count: int = Field(gt=0)
    source_row_count: int = Field(gt=0)
    source_columns: list[str]
    sensor_columns: list[str]
    aggregation: Literal["one-minute arithmetic mean"]
    report_relation: Literal[
        "within_reported_failure", "outside_reported_failure"
    ]
    failure_event_id: str | None = None

    @field_validator("path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("window path must be a safe relative POSIX path")
        return value

    @model_validator(mode="after")
    def validate_window(self) -> "MetroPT3Window":
        if self.source_start.tzinfo is not None or self.source_end_exclusive.tzinfo is not None:
            raise ValueError("source timestamps must remain timezone-naive")
        if self.normalized_start.tzinfo is None or self.normalized_end_exclusive.tzinfo is None:
            raise ValueError("normalized timestamps must include a timezone")
        if self.source_start >= self.source_end_exclusive:
            raise ValueError("source window start must be earlier than end")
        expected_rows = int(
            (self.source_end_exclusive - self.source_start).total_seconds()
            / self.sample_interval_seconds
        )
        if self.row_count != expected_rows:
            raise ValueError("row count does not cover the complete selected minute window")
        if self.report_relation == "within_reported_failure" and not self.failure_event_id:
            raise ValueError("reported-failure window requires a failure event ID")
        if self.report_relation == "outside_reported_failure" and self.failure_event_id:
            raise ValueError("reference window cannot link a failure event")
        return self


class MetroPT3ProfileManifest(StrictModel):
    schema_version: str = "1.0"
    profile_id: Literal["metropt3-real-sensor-v1"]
    real_world_operational_data: Literal[True]
    factory_production_line_data: Literal[False]
    multimodal: Literal[False]
    asset_id: Literal["APU-001"]
    source: MetroPT3Source
    failure_events: list[MetroPT3FailureEvent]
    windows: list[MetroPT3Window]
    limitations: list[str]


class MetroPT3DownloadResult(StrictModel):
    archive_path: str
    archive_sha256: Sha256
    downloaded: bool
    bytes: int = Field(gt=0)


class MetroPT3PreparationResult(StrictModel):
    manifest_path: str
    window_ids: list[str]
    dataset_hashes: dict[str, Sha256]


class MetroPT3ValidationResult(StrictModel):
    valid: bool
    window_ids: list[str]
    checks: list[str]


class MetroPT3WindowEvaluation(StrictModel):
    window_id: str
    report_relation: str
    failure_event_id: str | None
    row_count: int
    data_quality_usable: bool
    anomaly_point_count: int
    anomaly_segment_count: int
    alerted_sensor_ids: list[str]
    alerted_timestamp_count: int
    alert_timestamp_rate: float = Field(ge=0.0, le=1.0)
    reported_failure_window_alert_present: bool | None


class MetroPT3EvaluationReport(StrictModel):
    schema_version: str = "1.0"
    metric_name: Literal["Real operational sensor event-window analysis"]
    source_doi: str
    license: str
    profile_id: str
    detector: str
    parameters: DetectorParameters
    window_count: int
    windows: list[MetroPT3WindowEvaluation]
    limitations: list[str]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_stream(stream: IO[bytes]) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def download_metropt3(settings: Settings, *, force: bool = False) -> MetroPT3DownloadResult:
    """Download the pinned UCI archive into ignored runtime storage."""

    target = settings.metropt3_archive_path
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and not force:
        digest = _sha256_file(target)
        if digest != METROPT3_ARCHIVE_SHA256:
            raise ValueError(
                "existing MetroPT-3 archive hash differs from the pinned source; "
                "use --force only after reviewing provenance"
            )
        return MetroPT3DownloadResult(
            archive_path=str(target),
            archive_sha256=digest,
            downloaded=False,
            bytes=target.stat().st_size,
        )

    partial = target.with_suffix(".zip.part")
    try:
        request = urllib.request.Request(
            METROPT3_DOWNLOAD_URL,
            headers={"User-Agent": "multimodal-industrial-inspection-agent/1.0"},
        )
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
            while block := response.read(1024 * 1024):
                output.write(block)
        digest = _sha256_file(partial)
        if digest != METROPT3_ARCHIVE_SHA256:
            raise ValueError(
                f"downloaded archive hash mismatch: expected {METROPT3_ARCHIVE_SHA256}, "
                f"got {digest}"
            )
        partial.replace(target)
    finally:
        if partial.exists():
            partial.unlink()
    return MetroPT3DownloadResult(
        archive_path=str(target),
        archive_sha256=METROPT3_ARCHIVE_SHA256,
        downloaded=True,
        bytes=target.stat().st_size,
    )


def _open_csv_member(archive: zipfile.ZipFile) -> Iterator[csv.DictReader]:
    with archive.open(METROPT3_CSV_MEMBER) as binary:
        with io.TextIOWrapper(binary, encoding="utf-8-sig", newline="") as text:
            reader = csv.DictReader(text)
            required = {"timestamp", *METROPT3_SOURCE_COLUMNS}
            missing = required - set(reader.fieldnames or [])
            if missing:
                raise ValueError(f"MetroPT-3 source CSV is missing columns: {sorted(missing)}")
            yield reader


def _normalized_timestamp(source: datetime) -> datetime:
    return source.replace(tzinfo=UTC)


def _window_overlap(
    left_start: datetime,
    left_end: datetime,
    right_start: datetime,
    right_end: datetime,
) -> bool:
    return left_start < right_end and right_start < left_end


def _validate_selection_relations() -> None:
    events = {item.event_id: item for item in METROPT3_FAILURE_EVENTS}
    for selection in METROPT3_WINDOW_SELECTIONS:
        overlaps = [
            event
            for event in METROPT3_FAILURE_EVENTS
            if _window_overlap(
                selection.source_start,
                selection.source_end_exclusive,
                event.start_time_source,
                event.end_time_source,
            )
        ]
        if selection.report_relation == "outside_reported_failure" and overlaps:
            raise ValueError(f"reference window overlaps a reported failure: {selection.window_id}")
        if selection.report_relation == "within_reported_failure":
            event = events.get(selection.failure_event_id or "")
            if event is None:
                raise ValueError(f"failure event is missing: {selection.window_id}")
            if not (
                event.start_time_source <= selection.source_start
                and selection.source_end_exclusive <= event.end_time_source
            ):
                raise ValueError(f"window is not contained in failure report: {selection.window_id}")


def prepare_metropt3(
    settings: Settings,
    *,
    archive_path: Path | None = None,
) -> MetroPT3PreparationResult:
    """Verify the official archive and write two deterministic one-minute windows."""

    _validate_selection_relations()
    archive_path = (archive_path or settings.metropt3_archive_path).resolve()
    if not archive_path.is_file():
        raise FileNotFoundError(
            f"MetroPT-3 archive not found: {archive_path}; run download-metropt3 first"
        )
    archive_digest = _sha256_file(archive_path)
    if archive_digest != METROPT3_ARCHIVE_SHA256:
        raise ValueError(
            f"MetroPT-3 archive hash mismatch: expected {METROPT3_ARCHIVE_SHA256}, "
            f"got {archive_digest}"
        )

    with zipfile.ZipFile(archive_path) as archive:
        try:
            member = archive.getinfo(METROPT3_CSV_MEMBER)
        except KeyError as exc:
            raise ValueError(f"archive does not contain {METROPT3_CSV_MEMBER}") from exc
        with archive.open(member) as source:
            csv_digest = _sha256_stream(source)
        if csv_digest != METROPT3_CSV_SHA256:
            raise ValueError(
                f"MetroPT-3 CSV hash mismatch: expected {METROPT3_CSV_SHA256}, "
                f"got {csv_digest}"
            )

        aggregates: dict[
            str, dict[datetime, dict[str, list[float]]]
        ] = {
            item.window_id: defaultdict(lambda: defaultdict(list))
            for item in METROPT3_WINDOW_SELECTIONS
        }
        source_row_counts = {item.window_id: 0 for item in METROPT3_WINDOW_SELECTIONS}
        latest_end = max(item.source_end_exclusive for item in METROPT3_WINDOW_SELECTIONS)
        for reader in _open_csv_member(archive):
            for row in reader:
                timestamp = datetime.fromisoformat(row["timestamp"])
                if timestamp >= latest_end:
                    break
                for selection in METROPT3_WINDOW_SELECTIONS:
                    if selection.source_start <= timestamp < selection.source_end_exclusive:
                        minute = timestamp.replace(second=0, microsecond=0)
                        source_row_counts[selection.window_id] += 1
                        for source_column in METROPT3_SOURCE_COLUMNS:
                            value = float(row[source_column])
                            if not math.isfinite(value):
                                raise ValueError(
                                    f"non-finite {source_column} value at {timestamp.isoformat()}"
                                )
                            aggregates[selection.window_id][minute][source_column].append(
                                value
                            )

    profile_dir = settings.metropt3_profile_dir
    windows_dir = profile_dir / "windows"
    windows_dir.mkdir(parents=True, exist_ok=True)
    windows: list[MetroPT3Window] = []
    for selection in METROPT3_WINDOW_SELECTIONS:
        expected_minutes = [
            selection.source_start + timedelta(minutes=index)
            for index in range(
                int(
                    (selection.source_end_exclusive - selection.source_start).total_seconds()
                    / 60
                )
            )
        ]
        observed = aggregates[selection.window_id]
        missing_minutes = [item for item in expected_minutes if item not in observed]
        if missing_minutes:
            raise ValueError(
                f"selected window contains {len(missing_minutes)} missing minute buckets: "
                f"{selection.window_id}"
            )
        output_path = windows_dir / selection.output_name
        with output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["timestamp", *METROPT3_CANONICAL_COLUMNS],
                lineterminator="\n",
            )
            writer.writeheader()
            for minute in expected_minutes:
                payload: dict[str, str] = {
                    "timestamp": _normalized_timestamp(minute).isoformat()
                }
                for source_column, canonical in METROPT3_COLUMN_MAP.items():
                    values = observed[minute][source_column]
                    payload[canonical] = f"{sum(values) / len(values):.6f}"
                writer.writerow(payload)
        relative_path = output_path.relative_to(profile_dir).as_posix()
        windows.append(
            MetroPT3Window(
                window_id=selection.window_id,
                path=relative_path,
                sha256=_sha256_file(output_path),
                source_start=selection.source_start,
                source_end_exclusive=selection.source_end_exclusive,
                normalized_start=_normalized_timestamp(selection.source_start),
                normalized_end_exclusive=_normalized_timestamp(
                    selection.source_end_exclusive
                ),
                row_count=len(expected_minutes),
                source_row_count=source_row_counts[selection.window_id],
                source_columns=list(METROPT3_SOURCE_COLUMNS),
                sensor_columns=list(METROPT3_CANONICAL_COLUMNS),
                aggregation="one-minute arithmetic mean",
                report_relation=selection.report_relation,
                failure_event_id=selection.failure_event_id,
            )
        )

    manifest = MetroPT3ProfileManifest(
        profile_id="metropt3-real-sensor-v1",
        real_world_operational_data=True,
        factory_production_line_data=False,
        multimodal=False,
        asset_id="APU-001",
        source=MetroPT3Source(
            title="MetroPT-3 Dataset",
            doi=METROPT3_DOI,
            uci_url=METROPT3_UCI_URL,
            download_url=METROPT3_DOWNLOAD_URL,
            license=METROPT3_LICENSE,
            license_url=METROPT3_LICENSE_URL,
            archive_sha256=METROPT3_ARCHIVE_SHA256,
            csv_member=METROPT3_CSV_MEMBER,
            csv_sha256=METROPT3_CSV_SHA256,
            retrieved_on=date(2026, 8, 16),
            observed_row_count=METROPT3_OBSERVED_ROW_COUNT,
            observed_first_timestamp=datetime(2020, 2, 1, 0, 0),
            observed_last_timestamp=datetime(2020, 9, 1, 3, 59, 50),
            dominant_observed_interval_seconds=10,
            source_timestamp_timezone="not provided",
            timestamp_normalization=METROPT3_TIMESTAMP_ASSUMPTION,
        ),
        failure_events=list(METROPT3_FAILURE_EVENTS),
        windows=windows,
        limitations=[
            "The source is real operational railway APU data, not factory production-line data.",
            "MetroPT-3 contains no synchronized inspection images or maintenance manuals.",
            "Failure reports provide event windows, not point-level sensor anomaly labels.",
            "A window outside published reports is not guaranteed to be healthy.",
            "One-minute aggregation is a project preprocessing choice, not part of the source dataset.",
        ],
    )
    profile_dir.mkdir(parents=True, exist_ok=True)
    settings.metropt3_manifest_path.write_text(
        manifest.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return MetroPT3PreparationResult(
        manifest_path=str(settings.metropt3_manifest_path),
        window_ids=[item.window_id for item in windows],
        dataset_hashes={item.window_id: item.sha256 for item in windows},
    )


def load_metropt3_manifest(settings: Settings) -> MetroPT3ProfileManifest:
    path = settings.metropt3_manifest_path
    if not path.is_file():
        raise FileNotFoundError(f"MetroPT-3 profile manifest not found: {path}")
    return MetroPT3ProfileManifest.model_validate_json(path.read_text(encoding="utf-8"))


def _read_window(path: Path) -> tuple[list[datetime], list[str], int]:
    timestamps: list[datetime] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = list(reader.fieldnames or [])
        if columns != ["timestamp", *METROPT3_CANONICAL_COLUMNS]:
            raise ValueError(f"unexpected MetroPT-3 window columns: {columns}")
        for row in reader:
            timestamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise ValueError("normalized MetroPT-3 timestamps must include a timezone")
            timestamps.append(timestamp)
            for column in METROPT3_CANONICAL_COLUMNS:
                value = float(row[column])
                if not math.isfinite(value):
                    raise ValueError(f"non-finite value in {column}")
    return timestamps, columns, len(timestamps)


def validate_metropt3(settings: Settings) -> MetroPT3ValidationResult:
    """Validate committed derived windows without requiring network access."""

    _validate_selection_relations()
    manifest = load_metropt3_manifest(settings)
    checks: list[str] = []
    if manifest.source.archive_sha256 != METROPT3_ARCHIVE_SHA256:
        raise ValueError("manifest archive hash does not match the pinned source")
    if manifest.source.csv_sha256 != METROPT3_CSV_SHA256:
        raise ValueError("manifest CSV hash does not match the pinned source")
    if manifest.source.doi != METROPT3_DOI or manifest.source.license != METROPT3_LICENSE:
        raise ValueError("manifest DOI/license attribution is inconsistent")
    checks.append("official UCI DOI, CC BY 4.0 attribution, and source hashes are pinned")

    expected = {item.window_id: item for item in METROPT3_WINDOW_SELECTIONS}
    if {item.window_id for item in manifest.windows} != set(expected):
        raise ValueError("manifest must contain exactly the two reviewed MetroPT-3 windows")
    for window in manifest.windows:
        selection = expected[window.window_id]
        if (
            window.source_start != selection.source_start
            or window.source_end_exclusive != selection.source_end_exclusive
            or window.report_relation != selection.report_relation
            or window.failure_event_id != selection.failure_event_id
        ):
            raise ValueError(f"window selection metadata changed: {window.window_id}")
        path = (settings.metropt3_profile_dir / window.path).resolve()
        if settings.metropt3_profile_dir.resolve() not in path.parents:
            raise ValueError("MetroPT-3 window escaped the profile directory")
        if not path.is_file() or _sha256_file(path) != window.sha256:
            raise ValueError(f"MetroPT-3 window hash mismatch: {window.window_id}")
        timestamps, _, row_count = _read_window(path)
        if row_count != window.row_count:
            raise ValueError(f"MetroPT-3 row count mismatch: {window.window_id}")
        if timestamps[0] != window.normalized_start:
            raise ValueError(f"MetroPT-3 start timestamp mismatch: {window.window_id}")
        if timestamps[-1] + timedelta(seconds=window.sample_interval_seconds) != window.normalized_end_exclusive:
            raise ValueError(f"MetroPT-3 end timestamp mismatch: {window.window_id}")
        intervals = [
            (right - left).total_seconds()
            for left, right in zip(timestamps, timestamps[1:], strict=False)
        ]
        if any(item != window.sample_interval_seconds for item in intervals):
            raise ValueError(f"MetroPT-3 sampling interval mismatch: {window.window_id}")
    checks.append("two derived real-data windows have valid hashes, columns, rows, and intervals")
    checks.append("reported-failure and outside-report window relations do not overlap")
    checks.append("real profile is explicitly sensor-only, non-factory, and non-multimodal")
    return MetroPT3ValidationResult(
        valid=True,
        window_ids=[item.window_id for item in manifest.windows],
        checks=checks,
    )


def metropt3_asset() -> Asset:
    """Return an APU compressor context with documented or broad analysis guardrails."""

    return Asset(
        asset_id="APU-001",
        name="MetroPT-3 Train Air Production Unit",
        asset_type=AssetType.COMPRESSOR,
        site="METRO-DO-PORTO-OPERATIONAL-CONTEXT",
        status=AssetStatus.ACTIVE,
        criticality=Criticality.HIGH,
        description=(
            "Real operational compressor sensor profile derived from MetroPT-3; "
            "not a factory production-line asset and not a synchronized visual dataset."
        ),
        sensors=[
            SensorDefinition(
                sensor_name="compressor_pressure",
                display_name="Compressor Pressure (TP2)",
                unit="bar",
                operating_min=-1.0,
                operating_max=12.0,
            ),
            SensorDefinition(
                sensor_name="pneumatic_panel_pressure",
                display_name="Pneumatic Panel Pressure (TP3)",
                unit="bar",
                operating_min=7.0,
                operating_max=10.2,
            ),
            SensorDefinition(
                sensor_name="oil_temperature",
                display_name="Compressor Oil Temperature",
                unit="degC",
                operating_min=0.0,
                operating_max=100.0,
            ),
            SensorDefinition(
                sensor_name="motor_current",
                display_name="Compressor Motor Current",
                unit="A",
                operating_min=-0.5,
                operating_max=10.0,
            ),
        ],
    )


def evaluate_metropt3(
    settings: Settings,
    *,
    output_dir: Path | None = None,
) -> MetroPT3EvaluationReport:
    """Run event-window analysis without inventing point-level real-data labels."""

    validate_metropt3(settings)
    manifest = load_metropt3_manifest(settings)
    detector = RuleBasedAndMADDetector()
    asset = metropt3_asset()
    evaluations: list[MetroPT3WindowEvaluation] = []
    for window in manifest.windows:
        path = settings.metropt3_profile_dir / window.path
        result = detector.detect(
            path,
            asset,
            window.window_id,
            expected_sampling_interval_seconds=window.sample_interval_seconds,
        )
        alerted_timestamps = {item.timestamp for item in result.anomaly_points}
        any_alert = bool(result.anomaly_points)
        evaluations.append(
            MetroPT3WindowEvaluation(
                window_id=window.window_id,
                report_relation=window.report_relation,
                failure_event_id=window.failure_event_id,
                row_count=window.row_count,
                data_quality_usable=result.quality.is_usable,
                anomaly_point_count=len(result.anomaly_points),
                anomaly_segment_count=len(result.segments),
                alerted_sensor_ids=sorted(
                    {item.sensor_id for item in result.anomaly_points}
                ),
                alerted_timestamp_count=len(alerted_timestamps),
                alert_timestamp_rate=len(alerted_timestamps) / window.row_count,
                reported_failure_window_alert_present=(
                    any_alert
                    if window.report_relation == "within_reported_failure"
                    else None
                ),
            )
        )
    report = MetroPT3EvaluationReport(
        metric_name="Real operational sensor event-window analysis",
        source_doi=manifest.source.doi,
        license=manifest.source.license,
        profile_id=manifest.profile_id,
        detector=detector.detector_name,
        parameters=detector.parameters,
        window_count=len(evaluations),
        windows=evaluations,
        limitations=[
            "No point-level precision, recall, or F1 is reported because MetroPT-3 provides event reports rather than point labels.",
            "The outside-report window is a reference window, not verified healthy ground truth.",
            "Alerts can reflect normal compressor operating-state transitions.",
            "Configured operating ranges are analysis guardrails, not validated manufacturer limits.",
            "The selected profile contains real sensors only; Vision, RAG, diagnosis, and WorkOrder remain synthetic-demo capabilities.",
        ],
    )
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "report.json").write_text(
            report.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        (output_dir / "report.md").write_text(
            render_metropt3_markdown(report), encoding="utf-8", newline="\n"
        )
    return report


def render_metropt3_markdown(report: MetroPT3EvaluationReport) -> str:
    lines = [
        "# MetroPT-3 Real Sensor Evaluation",
        "",
        "> Event-window analysis of real operational railway APU sensor data; not factory or multimodal validation.",
        "",
        f"- Source DOI: `{report.source_doi}`",
        f"- License: `{report.license}`",
        f"- Profile: `{report.profile_id}`",
        f"- Detector: `{report.detector}`",
        "",
        "| Window | Relation to company report | Points | Segments | Alerted timestamps | Alert rate |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for item in report.windows:
        lines.append(
            f"| {item.window_id} | {item.report_relation} | "
            f"{item.anomaly_point_count} | {item.anomaly_segment_count} | "
            f"{item.alerted_timestamp_count} | {item.alert_timestamp_rate:.2%} |"
        )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in report.limitations)
    lines.append("")
    return "\n".join(lines)


def measured_metropt3_evaluation(
    settings: Settings,
) -> tuple[MetroPT3EvaluationReport, float]:
    started = time.perf_counter()
    report = evaluate_metropt3(settings)
    return report, (time.perf_counter() - started) * 1000.0
