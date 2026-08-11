"""SQLite persistence for Phase 3 drafts, approvals, work orders, and traces."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .workflow_schemas import (
    ApprovalRequest,
    DraftStatus,
    ToolTrace,
    WorkOrder,
    WorkOrderDraft,
)


WORKFLOW_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS work_order_drafts (
    draft_id TEXT PRIMARY KEY,
    inspection_id TEXT NOT NULL UNIQUE,
    asset_id TEXT NOT NULL,
    diagnosis_id TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    priority TEXT NOT NULL,
    summary TEXT NOT NULL,
    recommended_actions_json TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approval_requests (
    approval_id TEXT PRIMARY KEY,
    draft_id TEXT NOT NULL UNIQUE,
    draft_content_hash TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    decision TEXT NOT NULL,
    reviewer TEXT,
    reason TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    FOREIGN KEY (draft_id) REFERENCES work_order_drafts(draft_id)
);

CREATE TABLE IF NOT EXISTS work_orders (
    work_order_id TEXT PRIMARY KEY,
    draft_id TEXT NOT NULL UNIQUE,
    approval_id TEXT,
    asset_id TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    summary TEXT NOT NULL,
    recommended_actions_json TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    FOREIGN KEY (draft_id) REFERENCES work_order_drafts(draft_id),
    FOREIGN KEY (approval_id) REFERENCES approval_requests(approval_id)
);

CREATE TABLE IF NOT EXISTS tool_traces (
    trace_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    node_name TEXT NOT NULL,
    attempt_index INTEGER NOT NULL CHECK (attempt_index > 0),
    tool_name TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    duration_ms REAL NOT NULL,
    error_code TEXT,
    timestamp TEXT NOT NULL,
    details_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tool_traces_run_id ON tool_traces(run_id);
"""


class WorkflowRepository:
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
            connection.executescript(WORKFLOW_SCHEMA_SQL)
            self._migrate_pre_release_schema(connection)

    @staticmethod
    def _migrate_pre_release_schema(connection: sqlite3.Connection) -> None:
        """Keep local Phase 3 smoke databases usable while the schema is developed."""

        migrations = {
            "work_order_drafts": {
                "title": "TEXT NOT NULL DEFAULT 'Inspection maintenance draft'",
                "description": "TEXT NOT NULL DEFAULT 'Inspection recommendation'",
                "priority": "TEXT NOT NULL DEFAULT 'LOW'",
                "evidence_ids_json": "TEXT NOT NULL DEFAULT '[]'",
            },
            "approval_requests": {
                "reviewer": "TEXT",
                "reason": "TEXT",
            },
            "work_orders": {
                "idempotency_key": "TEXT",
            },
            "tool_traces": {
                "attempt_index": "INTEGER NOT NULL DEFAULT 1",
            },
        }
        for table, columns in migrations.items():
            existing = {
                row[1] for row in connection.execute(f"PRAGMA table_info({table})")
            }
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(
                        f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                    )
        connection.execute(
            """
            UPDATE work_orders SET idempotency_key = draft_id
            WHERE idempotency_key IS NULL
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_work_orders_idempotency
            ON work_orders(idempotency_key)
            """
        )
        # Pre-attempt-index databases can contain several historical rows for the
        # same run/node. Preserve every row and assign its execution order before
        # enforcing uniqueness for future attempts.
        duplicate_attempts = connection.execute(
            """
            SELECT 1 FROM tool_traces
            GROUP BY run_id, node_name, attempt_index
            HAVING COUNT(*) > 1 LIMIT 1
            """
        ).fetchone()
        if duplicate_attempts is not None:
            connection.execute("DROP INDEX IF EXISTS idx_tool_traces_attempt")
            connection.execute(
                """
                WITH ranked AS (
                    SELECT rowid AS trace_rowid,
                           ROW_NUMBER() OVER (
                               PARTITION BY run_id, node_name
                               ORDER BY timestamp, rowid
                           ) AS new_attempt_index
                    FROM tool_traces
                )
                UPDATE tool_traces
                SET attempt_index = (
                    SELECT new_attempt_index FROM ranked
                    WHERE ranked.trace_rowid = tool_traces.rowid
                )
                """
            )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_tool_traces_attempt
            ON tool_traces(run_id, node_name, attempt_index)
            """
        )

    def insert_draft(self, draft: WorkOrderDraft) -> WorkOrderDraft:
        with self.connection() as connection:
            existing = connection.execute(
                "SELECT * FROM work_order_drafts WHERE inspection_id = ?",
                (draft.inspection_id,),
            ).fetchone()
            if existing is not None:
                return self._row_to_draft(existing)
            connection.execute(
                """
                INSERT INTO work_order_drafts (
                    draft_id, inspection_id, asset_id, diagnosis_id, risk_level,
                    title, description, priority, summary, recommended_actions_json,
                    evidence_ids_json, content_hash, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft.draft_id,
                    draft.inspection_id,
                    draft.asset_id,
                    draft.diagnosis_id,
                    draft.risk_level.value,
                    draft.title,
                    draft.description,
                    draft.priority.value,
                    draft.summary,
                    json.dumps(draft.recommended_actions, ensure_ascii=False),
                    json.dumps(draft.evidence_ids, ensure_ascii=False),
                    draft.content_hash,
                    draft.status.value,
                    draft.created_at.isoformat(),
                ),
            )
        return draft

    def get_draft(self, draft_id: str) -> WorkOrderDraft | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM work_order_drafts WHERE draft_id = ?", (draft_id,)
            ).fetchone()
        return self._row_to_draft(row) if row else None

    def set_draft_status(self, draft_id: str, status: DraftStatus) -> None:
        with self.connection() as connection:
            changed = connection.execute(
                "UPDATE work_order_drafts SET status = ? WHERE draft_id = ?",
                (status.value, draft_id),
            ).rowcount
            if changed != 1:
                raise KeyError(f"work-order draft not found: {draft_id}")

    def insert_approval(self, approval: ApprovalRequest) -> ApprovalRequest:
        with self.connection() as connection:
            existing = connection.execute(
                "SELECT * FROM approval_requests WHERE draft_id = ?",
                (approval.draft_id,),
            ).fetchone()
            if existing is not None:
                return self._row_to_approval(existing)
            connection.execute(
                """
                INSERT INTO approval_requests (
                    approval_id, draft_id, draft_content_hash, risk_level,
                    decision, reviewer, reason, created_at, decided_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval.approval_id,
                    approval.draft_id,
                    approval.draft_hash,
                    approval.risk_level.value,
                    approval.decision.value,
                    approval.reviewer,
                    approval.reason,
                    approval.created_at.isoformat(),
                    approval.decided_at.isoformat() if approval.decided_at else None,
                ),
            )
        return approval

    def get_approval(self, approval_id: str) -> ApprovalRequest | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM approval_requests WHERE approval_id = ?", (approval_id,)
            ).fetchone()
        return self._row_to_approval(row) if row else None

    def decide_approval(self, approval: ApprovalRequest) -> ApprovalRequest:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM approval_requests WHERE approval_id = ?",
                (approval.approval_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"approval request not found: {approval.approval_id}")
            current = self._row_to_approval(row)
            if current.decision.value != "PENDING":
                if current.decision is approval.decision:
                    return current
                raise ValueError("approval decision is immutable once recorded")
            connection.execute(
                """
                UPDATE approval_requests
                SET decision = ?, reviewer = ?, reason = ?, decided_at = ?
                WHERE approval_id = ?
                """,
                (
                    approval.decision.value,
                    approval.reviewer,
                    approval.reason,
                    approval.decided_at.isoformat() if approval.decided_at else None,
                    approval.approval_id,
                ),
            )
        return approval

    def insert_work_order(self, work_order: WorkOrder) -> WorkOrder:
        with self.connection() as connection:
            existing = connection.execute(
                "SELECT * FROM work_orders WHERE draft_id = ?", (work_order.draft_id,)
            ).fetchone()
            if existing is not None:
                return self._row_to_work_order(existing)
            connection.execute(
                """
                INSERT INTO work_orders (
                    work_order_id, draft_id, approval_id, asset_id, risk_level,
                    summary, recommended_actions_json, idempotency_key, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    work_order.work_order_id,
                    work_order.draft_id,
                    work_order.approval_id,
                    work_order.asset_id,
                    work_order.risk_level.value,
                    work_order.summary,
                    json.dumps(work_order.recommended_actions, ensure_ascii=False),
                    work_order.idempotency_key,
                    work_order.created_at.isoformat(),
                ),
            )
        return work_order

    def get_work_order_for_draft(self, draft_id: str) -> WorkOrder | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM work_orders WHERE draft_id = ?", (draft_id,)
            ).fetchone()
        return self._row_to_work_order(row) if row else None

    def get_work_order(self, work_order_id: str) -> WorkOrder | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM work_orders WHERE work_order_id = ?", (work_order_id,)
            ).fetchone()
        return self._row_to_work_order(row) if row else None

    def list_work_orders(self) -> list[WorkOrder]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM work_orders ORDER BY created_at DESC"
            ).fetchall()
        return [self._row_to_work_order(row) for row in rows]

    def count_work_orders(self, draft_id: str | None = None) -> int:
        query = "SELECT COUNT(*) AS count FROM work_orders"
        params: tuple[str, ...] = ()
        if draft_id:
            query += " WHERE draft_id = ?"
            params = (draft_id,)
        with self.connection() as connection:
            return int(connection.execute(query, params).fetchone()["count"])

    def next_trace_attempt(self, run_id: str, node_name: str) -> int:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(MAX(attempt_index), 0) + 1 AS next_attempt
                FROM tool_traces WHERE run_id = ? AND node_name = ?
                """,
                (run_id, node_name),
            ).fetchone()
        return int(row["next_attempt"])

    def insert_trace(self, trace: ToolTrace) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO tool_traces (
                    trace_id, run_id, node_name, attempt_index, tool_name,
                    input_hash, status, duration_ms, error_code, timestamp, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace.trace_id,
                    trace.run_id,
                    trace.node_name,
                    trace.attempt_index,
                    trace.tool_name,
                    trace.input_hash,
                    trace.status.value,
                    trace.duration_ms,
                    trace.error_code,
                    trace.timestamp.isoformat(),
                    json.dumps(trace.details, ensure_ascii=False, sort_keys=True),
                ),
            )

    def list_traces(self, run_id: str, node_name: str) -> list[ToolTrace]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM tool_traces
                WHERE run_id = ? AND node_name = ?
                ORDER BY attempt_index
                """,
                (run_id, node_name),
            ).fetchall()
        return [
            ToolTrace.model_validate(
                {
                    "trace_id": row["trace_id"],
                    "run_id": row["run_id"],
                    "node_name": row["node_name"],
                    "attempt_index": row["attempt_index"],
                    "tool_name": row["tool_name"],
                    "input_hash": row["input_hash"],
                    "status": row["status"],
                    "duration_ms": row["duration_ms"],
                    "error_code": row["error_code"],
                    "timestamp": row["timestamp"],
                    "details": json.loads(row["details_json"]),
                }
            )
            for row in rows
        ]

    @staticmethod
    def _row_to_draft(row: sqlite3.Row) -> WorkOrderDraft:
        return WorkOrderDraft.model_validate(
            {
                "draft_id": row["draft_id"],
                "inspection_id": row["inspection_id"],
                "asset_id": row["asset_id"],
                "diagnosis_id": row["diagnosis_id"],
                "risk_level": row["risk_level"],
                "title": row["title"],
                "description": row["description"],
                "priority": row["priority"],
                "summary": row["summary"],
                "recommended_actions": json.loads(row["recommended_actions_json"]),
                "evidence_ids": json.loads(row["evidence_ids_json"]),
                "content_hash": row["content_hash"],
                "status": row["status"],
                "created_at": row["created_at"],
            }
        )

    @staticmethod
    def _row_to_approval(row: sqlite3.Row) -> ApprovalRequest:
        return ApprovalRequest.model_validate(
            {
                "approval_id": row["approval_id"],
                "draft_id": row["draft_id"],
                "draft_hash": row["draft_content_hash"],
                "risk_level": row["risk_level"],
                "decision": row["decision"],
                "reviewer": row["reviewer"],
                "reason": row["reason"],
                "created_at": row["created_at"],
                "decided_at": row["decided_at"],
            }
        )

    @staticmethod
    def _row_to_work_order(row: sqlite3.Row) -> WorkOrder:
        return WorkOrder.model_validate(
            {
                "work_order_id": row["work_order_id"],
                "draft_id": row["draft_id"],
                "approval_id": row["approval_id"],
                "asset_id": row["asset_id"],
                "risk_level": row["risk_level"],
                "summary": row["summary"],
                "recommended_actions": json.loads(row["recommended_actions_json"]),
                "idempotency_key": row["idempotency_key"],
                "created_at": row["created_at"],
            }
        )
