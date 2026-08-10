"""Deterministic sensor data-quality checks and anomaly detection."""

from __future__ import annotations

import csv
import math
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Protocol, runtime_checkable

from ..analysis_schemas import (
    AnomalyMethod,
    AnomalyPoint,
    AnomalySegment,
    DataQualityReport,
    DetectorParameters,
    SensorAnalysisResult,
    Severity,
)
from ..schemas import Asset, SensorDefinition


_MISSING_TOKENS = {"", "na", "nan", "null", "none"}


def _parse_timestamp(raw: str) -> datetime:
    value = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return value


class SensorDataQualityService:
    """Inspect a CSV without sorting, imputing, or hiding malformed input."""

    def analyze(
        self,
        csv_path: Path,
        required_sensor_columns: list[str],
        *,
        expected_sampling_interval_seconds: float,
        timestamp_column: str = "timestamp",
    ) -> DataQualityReport:
        path = Path(csv_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"sensor CSV not found: {path}")
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = list(reader.fieldnames or [])
            rows = list(reader)

        missing_columns = [
            column
            for column in [timestamp_column, *required_sensor_columns]
            if column not in columns
        ]
        parsed_timestamps: list[datetime] = []
        timestamp_parse_errors = 0
        if timestamp_column in columns:
            for row in rows:
                try:
                    parsed_timestamps.append(
                        _parse_timestamp(row.get(timestamp_column, "") or "")
                    )
                except (TypeError, ValueError):
                    timestamp_parse_errors += 1

        timestamp_counts = Counter(parsed_timestamps)
        duplicate_timestamp_count = sum(
            count - 1 for count in timestamp_counts.values() if count > 1
        )
        timestamps_strictly_increasing = (
            len(parsed_timestamps) == len(rows)
            and all(
                left < right
                for left, right in zip(
                    parsed_timestamps, parsed_timestamps[1:], strict=False
                )
            )
        )

        intervals = [
            (right - left).total_seconds()
            for left, right in zip(
                parsed_timestamps, parsed_timestamps[1:], strict=False
            )
        ]
        observed_interval = median(intervals) if intervals else None
        tolerance = max(1e-6, expected_sampling_interval_seconds * 0.01)
        irregular_interval_count = sum(
            abs(interval - expected_sampling_interval_seconds) > tolerance
            for interval in intervals
        )
        sampling_consistent = bool(intervals) and irregular_interval_count == 0

        missing_counts: dict[str, int] = {}
        missing_rates: dict[str, float] = {}
        non_numeric_counts: dict[str, int] = {}
        for column in required_sensor_columns:
            if column not in columns:
                missing_count = len(rows)
                non_numeric_count = 0
            else:
                missing_count = 0
                non_numeric_count = 0
                for row in rows:
                    raw = (row.get(column) or "").strip()
                    if raw.lower() in _MISSING_TOKENS:
                        missing_count += 1
                        continue
                    try:
                        numeric = float(raw)
                    except ValueError:
                        non_numeric_count += 1
                        continue
                    if not math.isfinite(numeric):
                        non_numeric_count += 1
            missing_counts[column] = missing_count
            missing_rates[column] = missing_count / len(rows) if rows else 0.0
            non_numeric_counts[column] = non_numeric_count

        errors: list[str] = []
        warnings: list[str] = []
        if not rows:
            errors.append("sensor CSV contains no data rows")
        if missing_columns:
            errors.append(f"missing required columns: {', '.join(missing_columns)}")
        if timestamp_parse_errors:
            errors.append(f"{timestamp_parse_errors} timestamp values are invalid")
        if duplicate_timestamp_count:
            errors.append(f"{duplicate_timestamp_count} duplicate timestamps found")
        if rows and not timestamps_strictly_increasing:
            errors.append("timestamps are not strictly increasing")
        if irregular_interval_count:
            errors.append(
                f"{irregular_interval_count} sampling intervals differ from expected"
            )
        for column in required_sensor_columns:
            if missing_counts[column]:
                errors.append(
                    f"{column} has {missing_counts[column]} missing values "
                    f"({missing_rates[column]:.2%})"
                )
            if non_numeric_counts[column]:
                errors.append(
                    f"{column} has {non_numeric_counts[column]} non-numeric values"
                )
        if len(rows) == 1:
            warnings.append("sampling interval cannot be estimated from one row")

        start_time = min(parsed_timestamps) if parsed_timestamps else None
        end_time = max(parsed_timestamps) if parsed_timestamps else None
        time_span = (
            (end_time - start_time).total_seconds()
            if start_time is not None and end_time is not None
            else None
        )
        return DataQualityReport(
            source=str(path),
            row_count=len(rows),
            timestamp_column=timestamp_column,
            timestamp_parse_errors=timestamp_parse_errors,
            timestamps_strictly_increasing=timestamps_strictly_increasing,
            duplicate_timestamp_count=duplicate_timestamp_count,
            missing_columns=missing_columns,
            missing_counts=missing_counts,
            missing_rates=missing_rates,
            non_numeric_counts=non_numeric_counts,
            expected_sampling_interval_seconds=expected_sampling_interval_seconds,
            observed_sampling_interval_seconds=observed_interval,
            sampling_interval_consistent=sampling_consistent,
            irregular_interval_count=irregular_interval_count,
            start_time=start_time,
            end_time=end_time,
            time_span_seconds=time_span,
            is_usable=not errors,
            warnings=warnings,
            errors=errors,
        )


@runtime_checkable
class AnomalyDetector(Protocol):
    """Common contract for deterministic and future detector implementations."""

    def detect(
        self,
        csv_path: Path,
        asset_context: Asset,
        dataset_id: str,
        *,
        expected_sampling_interval_seconds: float,
    ) -> SensorAnalysisResult:
        """Return quality, point flags, and merged anomaly segments."""


class RuleBasedAndMADDetector:
    """Operating-limit rules plus centered rolling median/MAD detection."""

    detector_name = "rule_based_and_rolling_mad"

    def __init__(
        self,
        parameters: DetectorParameters | None = None,
        quality_service: SensorDataQualityService | None = None,
    ) -> None:
        self.parameters = parameters or DetectorParameters()
        self.quality_service = quality_service or SensorDataQualityService()

    def detect(
        self,
        csv_path: Path,
        asset_context: Asset,
        dataset_id: str,
        *,
        expected_sampling_interval_seconds: float,
    ) -> SensorAnalysisResult:
        sensor_ids = [sensor.sensor_name for sensor in asset_context.sensors]
        quality = self.quality_service.analyze(
            csv_path,
            sensor_ids,
            expected_sampling_interval_seconds=expected_sampling_interval_seconds,
        )
        if not quality.is_usable:
            raise ValueError(
                "sensor data failed quality checks: " + "; ".join(quality.errors)
            )
        timestamps, values_by_sensor = self._load_values(csv_path, sensor_ids)
        definitions = {
            sensor.sensor_name: sensor for sensor in asset_context.sensors
        }

        raw_points: dict[str, list[AnomalyPoint]] = {}
        mad_zero_count = 0
        for sensor_id in sensor_ids:
            points, zero_count = self._detect_sensor_points(
                sensor_id,
                timestamps,
                values_by_sensor[sensor_id],
                definitions[sensor_id],
            )
            raw_points[sensor_id] = points
            mad_zero_count += zero_count

        segments, retained_points = self._merge_segments(
            dataset_id,
            raw_points,
            expected_sampling_interval_seconds,
        )
        warnings = list(quality.warnings)
        if mad_zero_count:
            warnings.append(
                f"MAD was zero at {mad_zero_count} evaluated points; explicit finite "
                "fallback handling was applied"
            )
        return SensorAnalysisResult(
            dataset_id=dataset_id,
            detector=self.detector_name,
            parameters=self.parameters,
            quality=quality,
            evaluated_sensor_ids=sensor_ids,
            anomaly_points=sorted(
                retained_points,
                key=lambda point: (point.timestamp, point.sensor_id),
            ),
            segments=segments,
            warnings=warnings,
        )

    @staticmethod
    def _load_values(
        csv_path: Path, sensor_ids: list[str]
    ) -> tuple[list[datetime], dict[str, list[float]]]:
        timestamps: list[datetime] = []
        values = {sensor_id: [] for sensor_id in sensor_ids}
        with Path(csv_path).open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                timestamps.append(_parse_timestamp(row["timestamp"]))
                for sensor_id in sensor_ids:
                    values[sensor_id].append(float(row[sensor_id]))
        return timestamps, values

    def _detect_sensor_points(
        self,
        sensor_id: str,
        timestamps: list[datetime],
        values: list[float],
        definition: SensorDefinition,
    ) -> tuple[list[AnomalyPoint], int]:
        points: list[AnomalyPoint] = []
        mad_zero_count = 0
        half_window = self.parameters.window_size // 2
        operating_span = definition.operating_max - definition.operating_min

        for index, value in enumerate(values):
            start = max(0, index - half_window)
            stop = min(len(values), index + half_window + 1)
            window = values[start:stop]
            rolling_median = median(window) if len(window) >= self.parameters.min_periods else None
            signed_score = 0.0
            methods: list[AnomalyMethod] = []

            if value < definition.operating_min:
                breach = (definition.operating_min - value) / operating_span
                signed_score = -(
                    self.parameters.mad_threshold + max(breach, 1e-6)
                )
                methods.append(AnomalyMethod.OPERATING_LIMIT)
            elif value > definition.operating_max:
                breach = (value - definition.operating_max) / operating_span
                signed_score = self.parameters.mad_threshold + max(breach, 1e-6)
                methods.append(AnomalyMethod.OPERATING_LIMIT)

            if rolling_median is not None:
                deviations = [abs(item - rolling_median) for item in window]
                mad = median(deviations)
                difference = value - rolling_median
                if mad <= 1e-12:
                    mad_zero_count += 1
                    if abs(difference) > 1e-12:
                        robust_z = math.copysign(
                            self.parameters.mad_zero_fallback_score,
                            difference,
                        )
                        methods.append(AnomalyMethod.MAD_ZERO_FALLBACK)
                    else:
                        robust_z = 0.0
                else:
                    robust_z = self.parameters.robust_z_constant * difference / mad
                    if abs(robust_z) >= self.parameters.mad_threshold:
                        methods.append(AnomalyMethod.ROLLING_MEDIAN_MAD)
                if abs(robust_z) >= self.parameters.mad_threshold and abs(robust_z) > abs(
                    signed_score
                ):
                    signed_score = robust_z

            if methods:
                points.append(
                    AnomalyPoint(
                        sensor_id=sensor_id,
                        timestamp=timestamps[index],
                        direction="increase" if signed_score >= 0 else "decrease",
                        score=abs(signed_score),
                        methods=list(dict.fromkeys(methods)),
                    )
                )
        return points, mad_zero_count

    def _merge_segments(
        self,
        dataset_id: str,
        points_by_sensor: dict[str, list[AnomalyPoint]],
        sampling_interval_seconds: float,
    ) -> tuple[list[AnomalySegment], list[AnomalyPoint]]:
        segments: list[AnomalySegment] = []
        retained_points: list[AnomalyPoint] = []
        maximum_gap = timedelta(
            seconds=sampling_interval_seconds * self.parameters.max_gap_intervals
        )
        end_step = timedelta(seconds=sampling_interval_seconds)

        for sensor_id, sensor_points in points_by_sensor.items():
            if not sensor_points:
                continue
            groups: list[list[AnomalyPoint]] = [[sensor_points[0]]]
            for point in sensor_points[1:]:
                previous = groups[-1][-1]
                if (
                    point.direction == previous.direction
                    and point.timestamp - previous.timestamp <= maximum_gap
                ):
                    groups[-1].append(point)
                else:
                    groups.append([point])

            accepted = [
                group
                for group in groups
                if len(group) >= self.parameters.min_segment_points
            ]
            for segment_number, group in enumerate(accepted, start=1):
                retained_points.extend(group)
                peak_score = max(point.score for point in group)
                method_names = sorted(
                    {method.value for point in group for method in point.methods}
                )
                normalized_sensor = sensor_id.upper().replace("_", "-")
                segment_id = (
                    f"SEGMENT-{dataset_id}-{normalized_sensor}-{segment_number:03d}"
                )
                if peak_score >= self.parameters.high_score_threshold:
                    severity = Severity.HIGH
                elif peak_score >= self.parameters.medium_score_threshold:
                    severity = Severity.MEDIUM
                else:
                    severity = Severity.LOW
                segments.append(
                    AnomalySegment(
                        segment_id=segment_id,
                        sensor_id=sensor_id,
                        start_time=group[0].timestamp,
                        end_time=group[-1].timestamp + end_step,
                        direction=group[0].direction,
                        peak_score=peak_score,
                        severity=severity,
                        method="+".join(method_names),
                        parameters=self.parameters.model_dump(mode="json"),
                        evidence_id=f"EVIDENCE-SENSOR-{segment_id}",
                        point_count=len(group),
                    )
                )
        segments.sort(key=lambda item: (item.start_time, item.sensor_id))
        return segments, retained_points
