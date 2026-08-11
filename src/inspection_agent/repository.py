"""Minimal SQLite repository for Phase 1 demo metadata."""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .logging_config import log_event
from .schemas import Asset, SensorDatasetMetadata

logger = logging.getLogger(__name__)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS assets (
    asset_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    name TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    site TEXT NOT NULL,
    status TEXT NOT NULL,
    criticality TEXT NOT NULL,
    description TEXT NOT NULL,
    sensors_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sensor_datasets (
    dataset_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    scenario_id TEXT NOT NULL UNIQUE,
    asset_id TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    random_seed INTEGER NOT NULL,
    row_count INTEGER NOT NULL CHECK (row_count > 0),
    sample_interval_seconds INTEGER NOT NULL CHECK (sample_interval_seconds > 0),
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    sensor_columns_json TEXT NOT NULL,
    FOREIGN KEY (asset_id) REFERENCES assets(asset_id)
);

CREATE INDEX IF NOT EXISTS idx_sensor_datasets_asset_id
    ON sensor_datasets(asset_id);
"""


class SQLiteRepository:
    """Repository for the two pieces of metadata needed in Phase 1."""

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
            connection.executescript(SCHEMA_SQL)
        log_event(
            logger,
            logging.INFO,
            "database_schema_initialized",
            database_path=str(self.database_path),
        )

    def replace_demo_data(
        self,
        assets: list[Asset],
        datasets: list[SensorDatasetMetadata],
    ) -> None:
        """Upsert the fixed demo metadata without invalidating later-phase records."""

        with self.connection() as connection:
            connection.executemany(
                """
                INSERT INTO assets (
                    asset_id, schema_version, name, asset_type, site, status,
                    criticality, description, sensors_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(asset_id) DO UPDATE SET
                    schema_version = excluded.schema_version,
                    name = excluded.name,
                    asset_type = excluded.asset_type,
                    site = excluded.site,
                    status = excluded.status,
                    criticality = excluded.criticality,
                    description = excluded.description,
                    sensors_json = excluded.sensors_json
                """,
                [
                    (
                        asset.asset_id,
                        asset.schema_version,
                        asset.name,
                        asset.asset_type.value,
                        asset.site,
                        asset.status.value,
                        asset.criticality.value,
                        asset.description,
                        json.dumps(
                            [sensor.model_dump(mode="json") for sensor in asset.sensors],
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    )
                    for asset in assets
                ],
            )
            connection.executemany(
                """
                INSERT INTO sensor_datasets (
                    dataset_id, schema_version, scenario_id, asset_id,
                    relative_path, sha256, random_seed, row_count,
                    sample_interval_seconds, start_time, end_time,
                    sensor_columns_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dataset_id) DO UPDATE SET
                    schema_version = excluded.schema_version,
                    scenario_id = excluded.scenario_id,
                    asset_id = excluded.asset_id,
                    relative_path = excluded.relative_path,
                    sha256 = excluded.sha256,
                    random_seed = excluded.random_seed,
                    row_count = excluded.row_count,
                    sample_interval_seconds = excluded.sample_interval_seconds,
                    start_time = excluded.start_time,
                    end_time = excluded.end_time,
                    sensor_columns_json = excluded.sensor_columns_json
                """,
                [
                    (
                        dataset.dataset_id,
                        dataset.schema_version,
                        dataset.scenario_id,
                        dataset.asset_id,
                        dataset.relative_path,
                        dataset.sha256,
                        dataset.random_seed,
                        dataset.row_count,
                        dataset.sample_interval_seconds,
                        dataset.start_time.isoformat(),
                        dataset.end_time.isoformat(),
                        json.dumps(dataset.sensor_columns, sort_keys=True),
                    )
                    for dataset in datasets
                ],
            )
        log_event(
            logger,
            logging.INFO,
            "demo_metadata_replaced",
            assets=len(assets),
            sensor_datasets=len(datasets),
        )

    def get_asset(self, asset_id: str) -> Asset | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM assets WHERE asset_id = ?", (asset_id,)
            ).fetchone()
        return self._row_to_asset(row) if row is not None else None

    def list_assets(self) -> list[Asset]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM assets ORDER BY asset_id"
            ).fetchall()
        return [self._row_to_asset(row) for row in rows]

    def get_sensor_dataset(
        self, scenario_id: str
    ) -> SensorDatasetMetadata | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM sensor_datasets WHERE scenario_id = ?", (scenario_id,)
            ).fetchone()
        return self._row_to_dataset(row) if row is not None else None

    def list_sensor_datasets(self) -> list[SensorDatasetMetadata]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM sensor_datasets ORDER BY scenario_id"
            ).fetchall()
        return [self._row_to_dataset(row) for row in rows]

    def table_names(self) -> list[str]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            ).fetchall()
        return [str(row["name"]) for row in rows]

    @staticmethod
    def _row_to_asset(row: sqlite3.Row) -> Asset:
        return Asset.model_validate(
            {
                "schema_version": row["schema_version"],
                "asset_id": row["asset_id"],
                "name": row["name"],
                "asset_type": row["asset_type"],
                "site": row["site"],
                "status": row["status"],
                "criticality": row["criticality"],
                "description": row["description"],
                "sensors": json.loads(row["sensors_json"]),
            }
        )

    @staticmethod
    def _row_to_dataset(row: sqlite3.Row) -> SensorDatasetMetadata:
        return SensorDatasetMetadata.model_validate(
            {
                "schema_version": row["schema_version"],
                "dataset_id": row["dataset_id"],
                "scenario_id": row["scenario_id"],
                "asset_id": row["asset_id"],
                "relative_path": row["relative_path"],
                "sha256": row["sha256"],
                "random_seed": row["random_seed"],
                "row_count": row["row_count"],
                "sample_interval_seconds": row["sample_interval_seconds"],
                "start_time": row["start_time"],
                "end_time": row["end_time"],
                "sensor_columns": json.loads(row["sensor_columns_json"]),
            }
        )
