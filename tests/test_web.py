from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from inspection_agent.application import InspectionApplicationService
from inspection_agent.config import Settings
from inspection_agent.services.knowledge import KnowledgeRetriever
from inspection_agent.web import create_app
from inspection_agent.workflow import WorkflowRuntime


class FailingVisionProvider:
    provider_name = "failing_web_vision"

    def analyze(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError("injected web vision failure")


class FailingSensorService:
    def detect(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError("injected web sensor failure")


def _png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (24, 16), color=(34, 155, 120)).save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
def web_client(seeded_demo: Settings):
    app = create_app(seeded_demo)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, app.state.service, seeded_demo


def _create(
    client: TestClient,
    scenario_id: str,
    asset_id: str,
    dataset_id: str,
) -> dict[str, Any]:
    response = client.post(
        "/api/inspections",
        data={
            "asset_id": asset_id,
            "scenario_id": scenario_id,
            "sensor_dataset_id": dataset_id,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["inspection"]


def _run(client: TestClient, inspection_id: str) -> dict[str, Any]:
    response = client.post(f"/api/inspections/{inspection_id}/run")
    assert response.status_code == 200, response.text
    return response.json()


def test_health_ready_and_asset_routes(web_client) -> None:
    client, _, _ = web_client
    health = client.get("/health")
    ready = client.get("/ready")
    assets = client.get("/api/assets")
    detail = client.get("/api/assets/PUMP-001")

    assert health.status_code == 200
    assert health.json()["status"] == "alive"
    assert health.headers["x-request-id"] == health.json()["request_id"]
    assert ready.status_code == 200
    assert all(check["ready"] for check in ready.json()["checks"])
    assert {item["asset_id"] for item in assets.json()["assets"]} == {
        "PUMP-001",
        "MOTOR-001",
    }
    assert detail.json()["asset"]["asset_id"] == "PUMP-001"


def test_api_errors_use_unified_schema(web_client) -> None:
    client, _, _ = web_client
    response = client.get("/api/assets/UNKNOWN-001")
    assert response.status_code == 404
    assert set(response.json()) == {"code", "message", "details", "request_id"}
    assert response.json()["code"] == "ASSET_NOT_FOUND"


def test_openapi_contains_all_required_phase4_routes(web_client) -> None:
    client, _, _ = web_client
    paths = client.get("/openapi.json").json()["paths"]
    assert {
        "/health",
        "/ready",
        "/api/assets",
        "/api/assets/{asset_id}",
        "/api/inspections",
        "/api/inspections/{inspection_id}/run",
        "/api/runs/{run_id}",
        "/api/approvals/{approval_id}/decision",
        "/api/work-orders/{work_order_id}",
        "/api/work-orders",
    } <= paths.keys()


def test_templates_render_synthetic_and_fixture_disclosures(web_client) -> None:
    client, _, _ = web_client
    home = client.get("/")
    asset = client.get("/assets/PUMP-001")
    new = client.get("/inspections/new")
    assert home.status_code == asset.status_code == new.status_code == 200
    assert "Synthetic industrial data" in home.text
    assert "PUMP-001" in home.text and "MOTOR-001" in home.text
    assert "Sensor definitions" in asset.text
    assert "Synthetic Demo Data" in new.text
    assert "Fixture Vision" in new.text
    assert "Load SCENARIO-001" in new.text


def test_valid_upload_uses_generated_safe_path(web_client) -> None:
    client, service, settings = web_client
    response = client.post(
        "/api/inspections",
        data={
            "asset_id": "PUMP-001",
            "scenario_id": "SCENARIO-001",
            "sensor_dataset_id": "DATASET-SCENARIO-001",
        },
        files={"image": ("../../untrusted.png", _png_bytes(), "image/png")},
    )
    assert response.status_code == 201, response.text
    inspection = response.json()["inspection"]
    artifact = service.get_artifact(inspection["image_artifact_id"])
    path = service.get_artifact_path(artifact.artifact_id)
    assert artifact.fixture is False
    assert artifact.content_hash in path.name
    assert "untrusted" not in path.name and ".." not in artifact.relative_path
    assert settings.uploads_dir.resolve() in path.parents


@pytest.mark.parametrize(
    ("filename", "media_type", "content", "code"),
    [
        ("bad.txt", "text/plain", b"not-image", "UNSUPPORTED_EXTENSION"),
        ("bad.png", "image/png", b"not-image", "INVALID_IMAGE"),
        ("bad.jpg", "image/png", _png_bytes(), "UNSUPPORTED_MEDIA_TYPE"),
    ],
)
def test_upload_validation_errors(
    web_client, filename: str, media_type: str, content: bytes, code: str
) -> None:
    client, _, _ = web_client
    response = client.post(
        "/api/inspections",
        data={
            "asset_id": "PUMP-001",
            "scenario_id": "SCENARIO-001",
            "sensor_dataset_id": "DATASET-SCENARIO-001",
        },
        files={"image": (filename, content, media_type)},
    )
    assert response.status_code == 422
    assert response.json()["code"] == code


def test_upload_size_limit(web_client) -> None:
    client, _, settings = web_client
    response = client.post(
        "/api/inspections",
        data={
            "asset_id": "PUMP-001",
            "scenario_id": "SCENARIO-001",
            "sensor_dataset_id": "DATASET-SCENARIO-001",
        },
        files={
            "image": (
                "large.png",
                b"x" * (settings.max_upload_bytes + 1),
                "image/png",
            )
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "UPLOAD_TOO_LARGE"


def test_asset_context_mismatch_is_rejected(web_client) -> None:
    client, _, _ = web_client
    response = client.post(
        "/api/inspections",
        data={
            "asset_id": "MOTOR-001",
            "scenario_id": "SCENARIO-001",
            "sensor_dataset_id": "DATASET-SCENARIO-001",
        },
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INSPECTION_CONTEXT_MISMATCH"


def test_browser_origin_api_write_requires_csrf(web_client) -> None:
    client, _, _ = web_client
    client.get("/")
    payload = {
        "asset_id": "PUMP-001",
        "scenario_id": "SCENARIO-003",
        "sensor_dataset_id": "DATASET-SCENARIO-003",
    }
    denied = client.post(
        "/api/inspections", data=payload, headers={"Origin": "http://testserver"}
    )
    token = client.cookies["inspection_csrf"]
    allowed = client.post(
        "/api/inspections",
        data=payload,
        headers={"Origin": "http://testserver", "X-CSRF-Token": token},
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "CSRF_VALIDATION_FAILED"
    assert allowed.status_code == 201


def test_scenario_001_api_approve_and_work_order_view(web_client) -> None:
    client, service, _ = web_client
    inspection = _create(
        client, "SCENARIO-001", "PUMP-001", "DATASET-SCENARIO-001"
    )
    run = _run(client, inspection["inspection_id"])
    assert run["status"] == "AWAITING_APPROVAL"
    status = client.get(f"/api/runs/{run['run_id']}").json()
    assert status["risk"]["risk_level"] == "CRITICAL"
    assert status["vision_summary"]["fixture"] is True
    awaiting_stages = {
        item["label"]: item["status"] for item in service.get_run_detail(run["run_id"]).stages
    }
    assert awaiting_stages["Approval"] == "active"
    assert awaiting_stages["WorkOrder"] == "pending"
    awaiting_page = client.get(f"/runs/{run['run_id']}")
    assert "Alternative candidates" in awaiting_page.text
    assert "WORKORDER DRAFT" in awaiting_page.text
    assert "DRAFT-" in awaiting_page.text
    approved = client.post(
        f"/api/approvals/{run['approval_id']}/decision",
        json={
            "decision": "APPROVE",
            "reviewer": "browser-reviewer",
            "reason": "Evidence reviewed",
        },
    )
    assert approved.status_code == 200
    assert approved.json()["work_order_status"] == "CREATED"
    work_order_id = approved.json()["work_order_id"]
    work_order = client.get(f"/api/work-orders/{work_order_id}")
    assert work_order.status_code == 200
    assert work_order.json()["asset_id"] == "PUMP-001"
    assert work_order.json()["evidence_ids"]
    assert client.get(f"/work-orders/{work_order_id}").status_code == 200
    completed_stages = {
        item["label"]: item["status"] for item in service.get_run_detail(run["run_id"]).stages
    }
    assert completed_stages["WorkOrder"] == "complete"


def test_scenario_002_api_reject_creates_no_work_order(web_client) -> None:
    client, _, _ = web_client
    inspection = _create(
        client, "SCENARIO-002", "MOTOR-001", "DATASET-SCENARIO-002"
    )
    run = _run(client, inspection["inspection_id"])
    status = client.get(f"/api/runs/{run['run_id']}").json()
    assert status["risk"]["risk_level"] == "HIGH"
    rejected = client.post(
        f"/api/approvals/{run['approval_id']}/decision",
        json={"decision": "REJECT", "reviewer": "browser-reviewer", "reason": "Reject demo"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["approval_status"] == "REJECT"
    assert rejected.json()["work_order_id"] is None


def test_scenario_003_ui_has_no_fault_draft_approval_or_work_order(web_client) -> None:
    client, service, _ = web_client
    inspection = _create(
        client, "SCENARIO-003", "PUMP-001", "DATASET-SCENARIO-003"
    )
    run = _run(client, inspection["inspection_id"])
    status = client.get(f"/api/runs/{run['run_id']}").json()
    record = service.get_run(run["run_id"])
    page = client.get(f"/runs/{run['run_id']}")
    assert status["diagnosis"]["primary_fault_candidate"] is None
    assert status["risk"]["risk_level"] == "LOW"
    assert record.state["work_order_draft_id"] is None
    assert record.approval_id is None and record.work_order_id is None
    assert "No actionable fault detected" in page.text
    assert "No WorkOrder Draft" in page.text
    assert page.status_code == 200


@pytest.mark.parametrize(
    ("fail_vision", "fail_sensor", "expected_status", "expected_text"),
    [
        (True, False, "AWAITING_APPROVAL", "vision analysis unavailable"),
        (False, True, "AWAITING_APPROVAL", "sensor analysis unavailable"),
        (True, True, "COMPLETED", "Evidence insufficient"),
    ],
)
def test_degraded_modes_are_visible_not_http_500(
    seeded_demo: Settings,
    fail_vision: bool,
    fail_sensor: bool,
    expected_status: str,
    expected_text: str,
) -> None:
    def runtime_factory() -> WorkflowRuntime:
        return WorkflowRuntime(
            seeded_demo,
            vision_provider=FailingVisionProvider() if fail_vision else None,
            sensor_service=FailingSensorService() if fail_sensor else None,
            retriever=KnowledgeRetriever(seeded_demo.knowledge_index_dir),
        )

    service = InspectionApplicationService(seeded_demo, runtime_factory=runtime_factory)
    app = create_app(seeded_demo, service)
    with TestClient(app, raise_server_exceptions=False) as client:
        inspection = _create(
            client, "SCENARIO-001", "PUMP-001", "DATASET-SCENARIO-001"
        )
        run = _run(client, inspection["inspection_id"])
        page = client.get(f"/runs/{run['run_id']}")
        assert run["status"] == expected_status
        assert page.status_code == 200
        assert "Warning / Degraded Mode" in page.text
        assert expected_text.lower() in page.text.lower()


def test_restart_then_resume_uses_persistent_run_and_checkpoint(seeded_demo: Settings) -> None:
    first_app = create_app(seeded_demo)
    with TestClient(first_app, raise_server_exceptions=False) as client:
        inspection = _create(
            client, "SCENARIO-001", "PUMP-001", "DATASET-SCENARIO-001"
        )
        run = _run(client, inspection["inspection_id"])

    rebuilt_app = create_app(seeded_demo)
    with TestClient(rebuilt_app, raise_server_exceptions=False) as client:
        response = client.post(
            f"/api/approvals/{run['approval_id']}/decision",
            json={"decision": "APPROVE", "reviewer": "restart-test", "reason": "resume"},
        )
        assert response.status_code == 200
        assert response.json()["work_order_status"] == "CREATED"


def test_app_mode_environment_alias(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APP_MODE", "demo")
    settings = Settings(data_dir=tmp_path / "data")
    assert settings.app_mode == "demo"


def test_readiness_reports_asset_repository_failure(web_client, monkeypatch) -> None:
    client, service, _ = web_client

    def fail_assets() -> list[object]:
        raise RuntimeError("injected readiness failure")

    monkeypatch.setattr(service.assets, "list_assets", fail_assets)
    response = client.get("/ready")
    checks = {item["name"]: item for item in response.json()["checks"]}

    assert response.status_code == 503
    assert checks["sqlite"]["ready"] is True
    assert checks["demo_assets"] == {
        "name": "demo_assets",
        "ready": False,
        "detail": "RuntimeError",
    }
