from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest

import inspection_agent.metropt3 as metropt3
from inspection_agent.config import PROJECT_ROOT, Settings


def _project_settings() -> Settings:
    return Settings(app_env="test", log_level="CRITICAL", data_dir=PROJECT_ROOT / "data")


def test_committed_real_profile_has_pinned_provenance_and_event_semantics(
    tmp_path: Path,
) -> None:
    settings = _project_settings()

    validation = metropt3.validate_metropt3(settings)
    manifest = metropt3.load_metropt3_manifest(settings)
    report = metropt3.evaluate_metropt3(settings, output_dir=tmp_path)

    assert validation.valid is True
    assert manifest.real_world_operational_data is True
    assert manifest.factory_production_line_data is False
    assert manifest.multimodal is False
    assert manifest.source.doi == "10.24432/C5VW3R"
    assert manifest.source.license == "CC BY 4.0"
    assert len(manifest.failure_events) == 4
    assert report.window_count == 2
    assert (tmp_path / "report.json").is_file()
    assert (tmp_path / "report.md").is_file()

    reference = next(
        item for item in report.windows if item.report_relation == "outside_reported_failure"
    )
    failure = next(
        item for item in report.windows if item.report_relation == "within_reported_failure"
    )
    assert reference.reported_failure_window_alert_present is None
    assert failure.reported_failure_window_alert_present is True
    assert reference.alert_timestamp_rate > failure.alert_timestamp_rate
    assert '"precision"' not in json.dumps(report.model_dump(mode="json"))


def test_validation_rejects_tampered_real_window(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    shutil.copytree(
        PROJECT_ROOT / "data" / "real" / "metropt3",
        data_dir / "real" / "metropt3",
    )
    settings = Settings(app_env="test", data_dir=data_dir)
    manifest = metropt3.load_metropt3_manifest(settings)
    path = settings.metropt3_profile_dir / manifest.windows[0].path
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="hash mismatch"):
        metropt3.validate_metropt3(settings)


def test_prepare_is_reproducible_from_a_verified_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=["timestamp", *metropt3.METROPT3_SOURCE_COLUMNS],
        lineterminator="\n",
    )
    writer.writeheader()
    for selection in metropt3.METROPT3_WINDOW_SELECTIONS:
        timestamp = selection.source_start
        while timestamp < selection.source_end_exclusive:
            writer.writerow(
                {
                    "timestamp": timestamp.isoformat(sep=" "),
                    "TP2": "1.0",
                    "TP3": "8.5",
                    "Oil_temperature": "60.0",
                    "Motor_current": "4.0",
                }
            )
            timestamp += timedelta(minutes=1)
    csv_bytes = buffer.getvalue().encode("utf-8")
    archive_path = tmp_path / "metropt3.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(metropt3.METROPT3_CSV_MEMBER, csv_bytes)
    monkeypatch.setattr(
        metropt3,
        "METROPT3_CSV_SHA256",
        hashlib.sha256(csv_bytes).hexdigest(),
    )
    monkeypatch.setattr(
        metropt3,
        "METROPT3_ARCHIVE_SHA256",
        hashlib.sha256(archive_path.read_bytes()).hexdigest(),
    )
    settings = Settings(app_env="test", data_dir=tmp_path / "profile-data")

    first = metropt3.prepare_metropt3(settings, archive_path=archive_path)
    first_hashes = dict(first.dataset_hashes)
    second = metropt3.prepare_metropt3(settings, archive_path=archive_path)

    assert second.dataset_hashes == first_hashes
    assert metropt3.validate_metropt3(settings).valid is True
    manifest = metropt3.load_metropt3_manifest(settings)
    assert [item.row_count for item in manifest.windows] == [360, 360]
    assert [item.source_row_count for item in manifest.windows] == [360, 360]
