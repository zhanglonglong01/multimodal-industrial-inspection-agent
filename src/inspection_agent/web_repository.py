"""SQLite persistence for Phase 4 application metadata."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .web_schemas import ArtifactRecord, InspectionRecord, RunRecord

WEB_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS image_artifacts (
    artifact_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL,
    media_type TEXT NOT NULL,
    extension TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    fixture INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (asset_id) REFERENCES assets(asset_id)
);

CREATE TABLE IF NOT EXISTS inspections (
    inspection_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL,
    scenario_id TEXT NOT NULL,
    sensor_dataset_id TEXT NOT NULL,
    image_artifact_id TEXT NOT NULL,
    vision_fixture_id TEXT NOT NULL,
    synthetic INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (asset_id) REFERENCES assets(asset_id),
    FOREIGN KEY (image_artifact_id) REFERENCES image_artifacts(artifact_id)
);

CREATE TABLE IF NOT EXISTS inspection_runs (
    run_id TEXT PRIMARY KEY,
    inspection_id TEXT NOT NULL,
    status TEXT NOT NULL,
    current_stage TEXT NOT NULL,
    approval_id TEXT,
    work_order_id TEXT,
    state_json TEXT NOT NULL,
    interrupt_payload_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (inspection_id) REFERENCES inspections(inspection_id)
);

CREATE INDEX IF NOT EXISTS idx_inspections_asset ON inspections(asset_id, created_at);
CREATE INDEX IF NOT EXISTS idx_runs_inspection ON inspection_runs(inspection_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_approval
    ON inspection_runs(approval_id) WHERE approval_id IS NOT NULL;
"""


class WebRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path).resolve()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize_schema(self) -> None:
        with self.connection() as connection:
            connection.executescript(WEB_SCHEMA_SQL)

    def insert_artifact(self, artifact: ArtifactRecord) -> ArtifactRecord:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO image_artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact.artifact_id,
                    artifact.asset_id,
                    artifact.media_type,
                    artifact.extension,
                    artifact.relative_path,
                    artifact.content_hash,
                    artifact.size_bytes,
                    int(artifact.fixture),
                    artifact.created_at.isoformat(),
                ),
            )
        return artifact

    def get_artifact(self, artifact_id: str) -> ArtifactRecord | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM image_artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
        return ArtifactRecord.model_validate(dict(row)) if row else None

    def insert_inspection(self, inspection: InspectionRecord) -> InspectionRecord:
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO inspections VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    inspection.inspection_id,
                    inspection.asset_id,
                    inspection.scenario_id,
                    inspection.sensor_dataset_id,
                    inspection.image_artifact_id,
                    inspection.vision_fixture_id,
                    int(inspection.synthetic),
                    inspection.created_at.isoformat(),
                ),
            )
        return inspection

    def get_inspection(self, inspection_id: str) -> InspectionRecord | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM inspections WHERE inspection_id = ?", (inspection_id,)
            ).fetchone()
        return InspectionRecord.model_validate(dict(row)) if row else None

    def list_inspections(self, asset_id: str | None = None) -> list[InspectionRecord]:
        query = "SELECT * FROM inspections"
        params: tuple[str, ...] = ()
        if asset_id:
            query += " WHERE asset_id = ?"
            params = (asset_id,)
        query += " ORDER BY created_at DESC"
        with self.connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [InspectionRecord.model_validate(dict(row)) for row in rows]

    def insert_run(self, run: RunRecord) -> RunRecord:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO inspection_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._run_values(run),
            )
        return run

    def update_run(self, run: RunRecord) -> RunRecord:
        with self.connection() as connection:
            changed = connection.execute(
                """
                UPDATE inspection_runs SET
                    status = ?, current_stage = ?, approval_id = ?, work_order_id = ?,
                    state_json = ?, interrupt_payload_json = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (
                    run.status.value,
                    run.current_stage,
                    run.approval_id,
                    run.work_order_id,
                    json.dumps(run.state, ensure_ascii=False, sort_keys=True),
                    json.dumps(run.interrupt_payload, ensure_ascii=False, sort_keys=True)
                    if run.interrupt_payload
                    else None,
                    run.updated_at.isoformat(),
                    run.run_id,
                ),
            ).rowcount
            if changed != 1:
                raise KeyError(f"run not found: {run.run_id}")
        return run

    def get_run(self, run_id: str) -> RunRecord | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM inspection_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return self._row_to_run(row) if row else None

    def get_run_by_approval(self, approval_id: str) -> RunRecord | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM inspection_runs WHERE approval_id = ?", (approval_id,)
            ).fetchone()
        return self._row_to_run(row) if row else None

    def latest_run_for_inspection(self, inspection_id: str) -> RunRecord | None:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM inspection_runs WHERE inspection_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (inspection_id,),
            ).fetchone()
        return self._row_to_run(row) if row else None

    @staticmethod
    def _run_values(run: RunRecord) -> tuple[object, ...]:
        return (
            run.run_id,
            run.inspection_id,
            run.status.value,
            run.current_stage,
            run.approval_id,
            run.work_order_id,
            json.dumps(run.state, ensure_ascii=False, sort_keys=True),
            json.dumps(run.interrupt_payload, ensure_ascii=False, sort_keys=True)
            if run.interrupt_payload
            else None,
            run.created_at.isoformat(),
            run.updated_at.isoformat(),
        )

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> RunRecord:
        return RunRecord.model_validate(
            {
                "run_id": row["run_id"],
                "inspection_id": row["inspection_id"],
                "status": row["status"],
                "current_stage": row["current_stage"],
                "approval_id": row["approval_id"],
                "work_order_id": row["work_order_id"],
                "state": json.loads(row["state_json"]),
                "interrupt_payload": json.loads(row["interrupt_payload_json"])
                if row["interrupt_payload_json"]
                else None,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
