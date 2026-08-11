"""Application use cases shared by FastAPI routes and HTML controllers."""

from __future__ import annotations

import csv
import sqlite3
import tempfile
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .analysis_schemas import EvidenceRef
from .config import Settings
from .demo import seed_demo
from .repository import SQLiteRepository
from .schemas import Asset, ImageFixture, ImageFixtureManifest
from .services.artifacts import ArtifactValidationError, ImageArtifactService
from .services.diagnosis import OpenAIDiagnosisProvider
from .services.knowledge import KnowledgeIndexBuilder, KnowledgeRetriever
from .web_repository import WebRepository
from .web_schemas import (
    ArtifactRecord,
    InspectionRecord,
    KnowledgeEvidenceView,
    ReadyCheck,
    RunDetailView,
    RunRecord,
    RunStatus,
    RunStatusResponse,
    SensorChartSeries,
    SensorSummary,
    VisionSummary,
    WorkOrderView,
)
from .workflow import WorkflowRuntime, build_initial_state, get_interrupt_payload
from .workflow_repository import WorkflowRepository
from .workflow_schemas import ApprovalDecisionInput


class ApplicationError(Exception):
    def __init__(
        self, code: str, message: str, *, status_code: int = 400, details: Any = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


class InspectionApplicationService:
    """The only entry point used by HTTP controllers and templates."""

    def __init__(
        self,
        settings: Settings,
        *,
        runtime_factory: Callable[[], WorkflowRuntime] | None = None,
    ) -> None:
        if settings.database_path is None:
            raise ValueError("database path is required")
        self.settings = settings
        self.assets = SQLiteRepository(settings.database_path)
        self.web_repository = WebRepository(settings.database_path)
        self.workflow_repository = WorkflowRepository(settings.database_path)
        self.artifacts = ImageArtifactService(settings)
        self._runtime_factory = runtime_factory
        self._retriever: KnowledgeRetriever | None = None

    def initialize(self) -> None:
        self.settings.ensure_directories()
        self.assets.initialize_schema()
        if not self.assets.list_assets():
            seed_demo(self.settings)
        self.web_repository.initialize_schema()
        self.workflow_repository.initialize_schema()
        KnowledgeIndexBuilder(self.settings).build()
        self._retriever = KnowledgeRetriever(self.settings.knowledge_index_dir)

    def list_assets(self) -> list[Asset]:
        return self.assets.list_assets()

    def get_asset(self, asset_id: str) -> Asset:
        asset = self.assets.get_asset(asset_id)
        if asset is None:
            raise ApplicationError(
                "ASSET_NOT_FOUND", f"Asset {asset_id} was not found.", status_code=404
            )
        return asset

    def list_inspections(self, asset_id: str | None = None) -> list[InspectionRecord]:
        return self.web_repository.list_inspections(asset_id)

    def latest_run_for_inspection(self, inspection_id: str) -> RunRecord | None:
        return self.web_repository.latest_run_for_inspection(inspection_id)

    def get_inspection(self, inspection_id: str) -> InspectionRecord:
        inspection = self.web_repository.get_inspection(inspection_id)
        if inspection is None:
            raise ApplicationError(
                "INSPECTION_NOT_FOUND",
                f"Inspection {inspection_id} was not found.",
                status_code=404,
            )
        return inspection

    def create_inspection(
        self,
        *,
        asset_id: str,
        scenario_id: str,
        sensor_dataset_id: str,
        image_content: bytes | None = None,
        image_filename: str | None = None,
        image_media_type: str | None = None,
    ) -> InspectionRecord:
        asset = self.get_asset(asset_id)
        dataset = self.assets.get_sensor_dataset(scenario_id)
        if dataset is None:
            raise ApplicationError(
                "DATASET_NOT_FOUND", f"No dataset is registered for {scenario_id}.", status_code=404
            )
        if dataset.dataset_id != sensor_dataset_id or dataset.asset_id != asset.asset_id:
            raise ApplicationError(
                "INSPECTION_CONTEXT_MISMATCH",
                "Asset, scenario, and sensor dataset do not belong to the same demo context.",
            )
        fixture = self._fixture_for_scenario(scenario_id)
        try:
            artifact = (
                self.artifacts.save_upload(
                    asset_id=asset_id,
                    content=image_content,
                    original_filename=image_filename or "",
                    media_type=image_media_type or "application/octet-stream",
                )
                if image_content is not None
                else self.artifacts.fixture_record(fixture)
            )
        except ArtifactValidationError as exc:
            raise ApplicationError(exc.code, str(exc), status_code=422) from exc
        self.web_repository.insert_artifact(artifact)
        inspection = InspectionRecord(
            inspection_id=f"INSPECTION-{uuid.uuid4().hex.upper()}",
            asset_id=asset_id,
            scenario_id=scenario_id,
            sensor_dataset_id=sensor_dataset_id,
            image_artifact_id=artifact.artifact_id,
            vision_fixture_id=fixture.fixture_id,
            synthetic=True,
            created_at=datetime.now(UTC),
        )
        return self.web_repository.insert_inspection(inspection)

    def run_inspection(self, inspection_id: str) -> RunRecord:
        inspection = self.get_inspection(inspection_id)
        run_id = f"RUN-{uuid.uuid4().hex.upper()}"
        now = datetime.now(UTC)
        record = RunRecord(
            run_id=run_id,
            inspection_id=inspection_id,
            status=RunStatus.RUNNING,
            current_stage="validate",
            state={},
            created_at=now,
            updated_at=now,
        )
        self.web_repository.insert_run(record)
        runtime = self._new_runtime()
        try:
            state = build_initial_state(
                self.settings,
                inspection.scenario_id,
                run_id=run_id,
                inspection_id=inspection.inspection_id,
            )
            # Demo vision intentionally uses the registered fixture while the uploaded
            # artifact remains available for visual display and later live providers.
            state["image_artifact_id"] = inspection.vision_fixture_id
            result = runtime.invoke(state)
            return self._save_result(record, result)
        except Exception as exc:
            failed = record.model_copy(
                update={
                    "status": RunStatus.FAILED,
                    "current_stage": "failed",
                    "state": {"warnings": [], "errors": [f"{type(exc).__name__}: {exc}"]},
                    "updated_at": datetime.now(UTC),
                }
            )
            self.web_repository.update_run(failed)
            raise ApplicationError(
                "WORKFLOW_RUN_FAILED",
                "Inspection workflow failed.",
                status_code=500,
                details={"run_id": run_id, "error_type": type(exc).__name__},
            ) from exc
        finally:
            runtime.close()

    def decide_approval(
        self, approval_id: str, decision: ApprovalDecisionInput
    ) -> RunRecord:
        record = self.web_repository.get_run_by_approval(approval_id)
        if record is None:
            raise ApplicationError(
                "APPROVAL_NOT_FOUND",
                f"Approval {approval_id} is not associated with a web run.",
                status_code=404,
            )
        if record.status is not RunStatus.AWAITING_APPROVAL:
            raise ApplicationError(
                "APPROVAL_ALREADY_DECIDED",
                "This run is no longer awaiting approval.",
                status_code=409,
            )
        runtime = self._new_runtime()
        try:
            result = runtime.resume(record.run_id, decision)
            return self._save_result(record, result)
        finally:
            runtime.close()

    def get_run(self, run_id: str) -> RunRecord:
        run = self.web_repository.get_run(run_id)
        if run is None:
            raise ApplicationError(
                "RUN_NOT_FOUND", f"Run {run_id} was not found.", status_code=404
            )
        return run

    def get_run_status(self, run_id: str) -> RunStatusResponse:
        run = self.get_run(run_id)
        state = run.state
        vision = state.get("vision_result")
        sensor = state.get("sensor_result")
        approval_status = (
            "AWAITING_DECISION"
            if run.status is RunStatus.AWAITING_APPROVAL
            else (state.get("approval_decision") or {}).get("decision", "NOT_REQUIRED")
        )
        return RunStatusResponse(
            run_id=run.run_id,
            inspection_id=run.inspection_id,
            status=run.status,
            current_stage=run.current_stage,
            warnings=state.get("warnings", []),
            errors=state.get("errors", []),
            vision_summary=VisionSummary(
                available=vision is not None,
                provider=vision.get("provider") if vision else None,
                fixture=bool(vision and vision.get("fixture")),
                findings=vision.get("findings", []) if vision else [],
            ),
            sensor_summary=SensorSummary(
                available=sensor is not None,
                quality_usable=sensor.get("quality_usable") if sensor else None,
                anomaly_point_count=sensor.get("anomaly_point_count", 0) if sensor else 0,
                segments=sensor.get("segments", []) if sensor else [],
            ),
            diagnosis=state.get("diagnosis_report"),
            risk=state.get("risk_assessment"),
            approval_status=approval_status,
            approval_id=run.approval_id,
            work_order_status="CREATED" if run.work_order_id else "NOT_CREATED",
            work_order_id=run.work_order_id,
        )

    def get_run_detail(self, run_id: str) -> RunDetailView:
        run = self.get_run(run_id)
        inspection = self.get_inspection(run.inspection_id)
        asset = self.get_asset(inspection.asset_id)
        artifact = self.get_artifact(inspection.image_artifact_id)
        work_order = (
            self.get_work_order(run.work_order_id) if run.work_order_id else None
        )
        return RunDetailView(
            run=run,
            inspection=inspection,
            asset=asset,
            artifact=artifact,
            status=self.get_run_status(run_id),
            sensor_series=self._sensor_chart(inspection, asset, run.state),
            knowledge_evidence=self._knowledge_views(run.state),
            stages=self._stage_views(run),
            work_order=work_order,
        )

    def get_artifact(self, artifact_id: str) -> ArtifactRecord:
        artifact = self.web_repository.get_artifact(artifact_id)
        if artifact is None:
            raise ApplicationError(
                "ARTIFACT_NOT_FOUND", f"Artifact {artifact_id} was not found.", status_code=404
            )
        return artifact

    def get_artifact_path(self, artifact_id: str) -> Path:
        artifact = self.get_artifact(artifact_id)
        path = self.artifacts.resolve(artifact.relative_path)
        if not path.is_file():
            raise ApplicationError("ARTIFACT_NOT_FOUND", "Artifact file is missing.", status_code=404)
        return path

    def list_work_orders(self) -> list[WorkOrderView]:
        return [self._work_order_view(item.work_order_id) for item in self.workflow_repository.list_work_orders()]

    def get_work_order(self, work_order_id: str) -> WorkOrderView:
        return self._work_order_view(work_order_id)

    def _work_order_view(self, work_order_id: str) -> WorkOrderView:
        work_order = self.workflow_repository.get_work_order(work_order_id)
        if work_order is None:
            raise ApplicationError(
                "WORK_ORDER_NOT_FOUND",
                f"Work order {work_order_id} was not found.",
                status_code=404,
            )
        draft = self.workflow_repository.get_draft(work_order.draft_id)
        if draft is None:
            raise ApplicationError(
                "WORK_ORDER_DRAFT_NOT_FOUND",
                "The work-order draft is missing.",
                status_code=500,
            )
        return WorkOrderView(
            work_order_id=work_order.work_order_id,
            draft_id=work_order.draft_id,
            asset_id=work_order.asset_id,
            title=draft.title,
            description=draft.description,
            priority=work_order.risk_level,
            status="CREATED",
            recommended_actions=work_order.recommended_actions,
            evidence_ids=draft.evidence_ids,
            approval_id=work_order.approval_id,
            idempotency_key=work_order.idempotency_key,
            created_at=work_order.created_at,
        )

    def readiness_checks(self) -> list[ReadyCheck]:
        checks: list[ReadyCheck] = []
        try:
            assert self.settings.database_path is not None
            with sqlite3.connect(self.settings.database_path) as connection:
                connection.execute("SELECT 1").fetchone()
            checks.append(ReadyCheck(name="sqlite", ready=True, detail="accessible"))
        except Exception as exc:
            checks.append(ReadyCheck(name="sqlite", ready=False, detail=type(exc).__name__))
        try:
            assets = self.assets.list_assets()
            checks.append(
                ReadyCheck(
                    name="demo_assets",
                    ready={asset.asset_id for asset in assets} == {"PUMP-001", "MOTOR-001"},
                    detail=f"{len(assets)} assets",
                )
            )
        except Exception as exc:
            checks.append(
                ReadyCheck(name="demo_assets", ready=False, detail=type(exc).__name__)
            )
        try:
            KnowledgeRetriever(self.settings.knowledge_index_dir)
            checks.append(ReadyCheck(name="knowledge_index", ready=True, detail="loadable"))
        except Exception as exc:
            checks.append(
                ReadyCheck(name="knowledge_index", ready=False, detail=type(exc).__name__)
            )
        try:
            with tempfile.NamedTemporaryFile(dir=self.settings.uploads_dir, delete=True):
                pass
            checks.append(ReadyCheck(name="runtime_storage", ready=True, detail="writable"))
        except OSError as exc:
            checks.append(
                ReadyCheck(name="runtime_storage", ready=False, detail=type(exc).__name__)
            )
        return checks

    def _new_runtime(self) -> WorkflowRuntime:
        if self._runtime_factory is not None:
            return self._runtime_factory()
        if self._retriever is None:
            self._retriever = KnowledgeRetriever(self.settings.knowledge_index_dir)
        if self.settings.app_mode == "live":
            return WorkflowRuntime(
                self.settings,
                diagnosis_provider=OpenAIDiagnosisProvider(self.settings),
                retriever=self._retriever,
            )
        return WorkflowRuntime(self.settings, retriever=self._retriever)

    def _save_result(self, prior: RunRecord, result: dict[str, Any]) -> RunRecord:
        interrupt_payload = get_interrupt_payload(result)
        state = {key: value for key, value in result.items() if key != "__interrupt__"}
        awaiting = interrupt_payload is not None
        updated = prior.model_copy(
            update={
                "status": RunStatus.AWAITING_APPROVAL if awaiting else RunStatus.COMPLETED,
                "current_stage": "approval" if awaiting else "finalize",
                "approval_id": state.get("approval_request_id"),
                "work_order_id": state.get("work_order_id"),
                "state": state,
                "interrupt_payload": interrupt_payload,
                "updated_at": datetime.now(UTC),
            }
        )
        return self.web_repository.update_run(updated)

    def _fixture_for_scenario(self, scenario_id: str) -> ImageFixture:
        manifest = ImageFixtureManifest.model_validate_json(
            self.settings.image_manifest_path.read_text(encoding="utf-8")
        )
        fixture = next(
            (item for item in manifest.fixtures if item.scenario_id == scenario_id), None
        )
        if fixture is None:
            raise ApplicationError(
                "SCENARIO_NOT_FOUND", f"Scenario {scenario_id} was not found.", status_code=404
            )
        return fixture

    def _sensor_chart(
        self, inspection: InspectionRecord, asset: Asset, state: dict[str, Any]
    ) -> list[SensorChartSeries]:
        dataset = self.assets.get_sensor_dataset(inspection.scenario_id)
        if dataset is None:
            return []
        path = (self.settings.data_dir / dataset.relative_path).resolve()
        data_root = self.settings.data_dir.resolve()
        if data_root not in path.parents or not path.is_file():
            return []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        segments = (state.get("sensor_result") or {}).get("segments", [])
        output: list[SensorChartSeries] = []
        for sensor in asset.sensors:
            output.append(
                SensorChartSeries(
                    sensor_id=sensor.sensor_name,
                    display_name=sensor.display_name,
                    unit=sensor.unit,
                    operating_min=sensor.operating_min,
                    operating_max=sensor.operating_max,
                    timestamps=[str(row["timestamp"]) for row in rows],
                    values=[float(row[sensor.sensor_name]) for row in rows],
                    anomaly_segments=[
                        item for item in segments if item["sensor_id"] == sensor.sensor_name
                    ],
                )
            )
        return output

    def _knowledge_views(self, state: dict[str, Any]) -> list[KnowledgeEvidenceView]:
        if self._retriever is None:
            self._retriever = KnowledgeRetriever(self.settings.knowledge_index_dir)
        evidence = [
            EvidenceRef.model_validate(item) for item in state.get("knowledge_evidence", [])
        ]
        scores: dict[str, float] = {}
        for query in state.get("retrieval_queries", []):
            for result in self._retriever.search(
                query, top_k=self._retriever.metadata.chunk_count
            ):
                scores[result.chunk_id] = max(scores.get(result.chunk_id, -1.0), result.score)
        views: list[KnowledgeEvidenceView] = []
        for item in evidence:
            chunk = self._retriever.get_chunk(item.source_id)
            if chunk is None:
                continue
            views.append(
                KnowledgeEvidenceView(
                    evidence_id=item.evidence_id,
                    chunk_id=chunk.chunk_id,
                    title=chunk.title,
                    section=chunk.section,
                    excerpt=chunk.text[:300],
                    score=scores.get(chunk.chunk_id),
                    source=chunk.source,
                )
            )
        return views

    @staticmethod
    def _stage_views(run: RunRecord) -> list[dict[str, Any]]:
        trace_names = {
            item.get("node_name") for item in run.state.get("tool_trace", [])
        }
        definitions = [
            ("Validate", {"validate_request"}),
            ("Asset Context", {"load_asset_context"}),
            ("Vision", {"analyze_image"}),
            ("Sensor Analysis", {"analyze_sensors"}),
            ("Failure Mode", {"lookup_failure_modes"}),
            ("RAG", {"build_retrieval_queries", "retrieve_knowledge"}),
            ("Diagnosis", {"synthesize_diagnosis"}),
            ("Risk", {"apply_risk_policy"}),
            ("Approval", {"approval_gate", "validate_approval"}),
            ("WorkOrder", {"draft_work_order", "create_work_order"}),
        ]
        stages = []
        for label, nodes in definitions:
            if label == "WorkOrder":
                status = (
                    "complete"
                    if run.work_order_id
                    else "pending"
                    if run.status is RunStatus.AWAITING_APPROVAL
                    else "skipped"
                )
            elif nodes & trace_names:
                status = "complete"
            elif label == "Approval" and run.status is RunStatus.AWAITING_APPROVAL:
                status = "active"
            else:
                status = "skipped" if run.status is RunStatus.COMPLETED else "pending"
            stages.append({"label": label, "status": status})
        return stages
