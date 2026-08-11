"""FastAPI API and server-rendered dashboard for the Portfolio MVP."""

from __future__ import annotations

import logging
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .application import ApplicationError, InspectionApplicationService
from .config import Settings, get_settings
from .logging_config import configure_logging, log_event
from .web_schemas import (
    ApprovalDecisionRequest,
    AssetDetailResponse,
    AssetListResponse,
    ErrorResponse,
    HealthResponse,
    InspectionCreateResponse,
    ReadyResponse,
    RunStartResponse,
    RunStatusResponse,
    WorkOrderListResponse,
    WorkOrderView,
)
from .workflow_schemas import ApprovalDecision, ApprovalDecisionInput

logger = logging.getLogger(__name__)
PACKAGE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "unknown"))


def _service(request: Request) -> InspectionApplicationService:
    return request.app.state.service


def _verify_api_csrf(request: Request) -> None:
    """For browser-originated API writes, require a matching double-submit token."""

    if request.headers.get("origin"):
        cookie = request.cookies.get("inspection_csrf")
        header = request.headers.get("x-csrf-token")
        if not cookie or not header or not secrets.compare_digest(cookie, header):
            raise ApplicationError(
                "CSRF_VALIDATION_FAILED", "CSRF token is missing or invalid.", status_code=403
            )


def _verify_form_csrf(request: Request, token: str) -> None:
    cookie = request.cookies.get("inspection_csrf")
    if not cookie or not token or not secrets.compare_digest(cookie, token):
        raise ApplicationError(
            "CSRF_VALIDATION_FAILED", "CSRF token is missing or invalid.", status_code=403
        )


def _template_context(request: Request, **values: Any) -> dict[str, Any]:
    return {
        "request": request,
        "app_mode": request.app.state.settings.app_mode,
        "csrf_token": request.state.csrf_token,
        **values,
    }


def create_app(
    settings: Settings | None = None,
    service: InspectionApplicationService | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    resolved_service = service or InspectionApplicationService(resolved_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging(resolved_settings.log_level)
        resolved_service.initialize()
        yield

    app = FastAPI(
        title=resolved_settings.app_name,
        version="1.0.0",
        description="Portfolio demo API for synthetic industrial inspections.",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.service = resolved_service
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        started = time.perf_counter()
        request.state.request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.csrf_token = request.cookies.get("inspection_csrf") or secrets.token_urlsafe(32)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        if "inspection_csrf" not in request.cookies:
            response.set_cookie(
                "inspection_csrf",
                request.state.csrf_token,
                httponly=True,
                samesite="strict",
                secure=False,
            )
        log_event(
            logger,
            logging.INFO,
            "http_request_completed",
            request_id=request.state.request_id,
            run_id=getattr(request.state, "run_id", None),
            endpoint=request.url.path,
            status=response.status_code,
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
        )
        return response

    @app.exception_handler(ApplicationError)
    async def application_error(request: Request, exc: ApplicationError):
        payload = ErrorResponse(
            code=exc.code,
            message=exc.message,
            details=exc.details,
            request_id=_request_id(request),
        )
        return JSONResponse(status_code=exc.status_code, content=payload.model_dump(mode="json"))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        payload = ErrorResponse(
            code="REQUEST_VALIDATION_FAILED",
            message="Request validation failed.",
            details=exc.errors(),
            request_id=_request_id(request),
        )
        return JSONResponse(status_code=422, content=payload.model_dump(mode="json"))

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        payload = ErrorResponse(
            code="HTTP_ERROR",
            message=str(exc.detail),
            details=None,
            request_id=_request_id(request),
        )
        return JSONResponse(status_code=exc.status_code, content=payload.model_dump(mode="json"))

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        log_event(
            logger,
            logging.ERROR,
            "http_request_failed",
            request_id=_request_id(request),
            endpoint=request.url.path,
            error_type=type(exc).__name__,
        )
        payload = ErrorResponse(
            code="INTERNAL_SERVER_ERROR",
            message="An unexpected server error occurred.",
            details=None,
            request_id=_request_id(request),
        )
        return JSONResponse(status_code=500, content=payload.model_dump(mode="json"))

    @app.get("/health", response_model=HealthResponse, tags=["operations"])
    def health(request: Request) -> HealthResponse:
        return HealthResponse(status="alive", request_id=_request_id(request))

    @app.get("/ready", response_model=ReadyResponse, tags=["operations"])
    def ready(request: Request, svc: Annotated[InspectionApplicationService, Depends(_service)]):
        checks = svc.readiness_checks()
        ready_status = all(item.ready for item in checks)
        payload = ReadyResponse(
            status="ready" if ready_status else "not_ready",
            checks=checks,
            request_id=_request_id(request),
        )
        return JSONResponse(
            status_code=200 if ready_status else 503,
            content=payload.model_dump(mode="json"),
        )

    @app.get("/api/assets", response_model=AssetListResponse, tags=["assets"])
    def api_assets(svc: Annotated[InspectionApplicationService, Depends(_service)]):
        return AssetListResponse(assets=svc.list_assets())

    @app.get("/api/assets/{asset_id}", response_model=AssetDetailResponse, tags=["assets"])
    def api_asset(asset_id: str, svc: Annotated[InspectionApplicationService, Depends(_service)]):
        return AssetDetailResponse(
            asset=svc.get_asset(asset_id),
            recent_inspections=svc.list_inspections(asset_id)[:10],
        )

    @app.post(
        "/api/inspections",
        response_model=InspectionCreateResponse,
        status_code=201,
        tags=["inspections"],
    )
    async def api_create_inspection(
        request: Request,
        asset_id: Annotated[str, Form()],
        scenario_id: Annotated[str, Form()],
        sensor_dataset_id: Annotated[str, Form()],
        svc: Annotated[InspectionApplicationService, Depends(_service)],
        image: Annotated[UploadFile | None, File()] = None,
    ):
        _verify_api_csrf(request)
        content = None
        filename = None
        media_type = None
        if image is not None:
            content = await image.read(resolved_settings.max_upload_bytes + 1)
            filename = image.filename
            media_type = image.content_type
        inspection = svc.create_inspection(
            asset_id=asset_id,
            scenario_id=scenario_id,
            sensor_dataset_id=sensor_dataset_id,
            image_content=content,
            image_filename=filename,
            image_media_type=media_type,
        )
        return InspectionCreateResponse(inspection=inspection)

    @app.post(
        "/api/inspections/{inspection_id}/run",
        response_model=RunStartResponse,
        tags=["runs"],
    )
    def api_run_inspection(
        inspection_id: str,
        request: Request,
        svc: Annotated[InspectionApplicationService, Depends(_service)],
    ):
        _verify_api_csrf(request)
        run = svc.run_inspection(inspection_id)
        request.state.run_id = run.run_id
        return RunStartResponse(
            run_id=run.run_id,
            status=run.status,
            current_stage=run.current_stage,
            approval_id=run.approval_id,
        )

    @app.get("/api/runs/{run_id}", response_model=RunStatusResponse, tags=["runs"])
    def api_run(run_id: str, svc: Annotated[InspectionApplicationService, Depends(_service)]):
        return svc.get_run_status(run_id)

    @app.post(
        "/api/approvals/{approval_id}/decision",
        response_model=RunStatusResponse,
        tags=["approvals"],
    )
    def api_decide(
        approval_id: str,
        payload: ApprovalDecisionRequest,
        request: Request,
        svc: Annotated[InspectionApplicationService, Depends(_service)],
    ):
        _verify_api_csrf(request)
        run = svc.decide_approval(
            approval_id,
            ApprovalDecisionInput(
                decision=payload.decision,
                reviewer=payload.reviewer,
                comment=payload.reason,
            ),
        )
        request.state.run_id = run.run_id
        return svc.get_run_status(run.run_id)

    @app.get("/api/work-orders", response_model=WorkOrderListResponse, tags=["work-orders"])
    def api_work_orders(svc: Annotated[InspectionApplicationService, Depends(_service)]):
        return WorkOrderListResponse(work_orders=svc.list_work_orders())

    @app.get("/api/work-orders/{work_order_id}", response_model=WorkOrderView, tags=["work-orders"])
    def api_work_order(
        work_order_id: str, svc: Annotated[InspectionApplicationService, Depends(_service)]
    ):
        return svc.get_work_order(work_order_id)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home(request: Request, svc: Annotated[InspectionApplicationService, Depends(_service)]):
        return templates.TemplateResponse(
            request,
            "home.html",
            _template_context(
                request,
                title="Asset Fleet",
                assets=svc.list_assets(),
                inspections=svc.list_inspections()[:6],
            ),
        )

    @app.get("/assets/{asset_id}", response_class=HTMLResponse, include_in_schema=False)
    def asset_page(
        asset_id: str, request: Request, svc: Annotated[InspectionApplicationService, Depends(_service)]
    ):
        return templates.TemplateResponse(
            request,
            "asset_detail.html",
            _template_context(
                request,
                title=asset_id,
                asset=svc.get_asset(asset_id),
                inspections=svc.list_inspections(asset_id)[:10],
            ),
        )

    @app.get("/inspections/new", response_class=HTMLResponse, include_in_schema=False)
    def new_inspection(
        request: Request,
        svc: Annotated[InspectionApplicationService, Depends(_service)],
        asset_id: str | None = None,
        scenario_id: str | None = None,
    ):
        scenarios = [
            {"id": "SCENARIO-001", "asset_id": "PUMP-001", "dataset_id": "DATASET-SCENARIO-001", "name": "Pump seal leakage"},
            {"id": "SCENARIO-002", "asset_id": "MOTOR-001", "dataset_id": "DATASET-SCENARIO-002", "name": "Motor bearing anomaly"},
            {"id": "SCENARIO-003", "asset_id": "PUMP-001", "dataset_id": "DATASET-SCENARIO-003", "name": "Normal equipment"},
        ]
        return templates.TemplateResponse(
            request,
            "new_inspection.html",
            _template_context(
                request,
                title="New Inspection",
                assets=svc.list_assets(),
                scenarios=scenarios,
                selected_asset=asset_id,
                selected_scenario=scenario_id,
            ),
        )

    @app.post("/ui/inspections/demo", include_in_schema=False)
    def create_demo_inspection(
        request: Request,
        asset_id: Annotated[str, Form()],
        scenario_id: Annotated[str, Form()],
        sensor_dataset_id: Annotated[str, Form()],
        csrf_token: Annotated[str, Form()],
        svc: Annotated[InspectionApplicationService, Depends(_service)],
    ):
        _verify_form_csrf(request, csrf_token)
        inspection = svc.create_inspection(
            asset_id=asset_id,
            scenario_id=scenario_id,
            sensor_dataset_id=sensor_dataset_id,
        )
        return RedirectResponse(f"/inspections/{inspection.inspection_id}", status_code=303)

    @app.post("/ui/inspections", include_in_schema=False)
    async def create_uploaded_inspection(
        request: Request,
        asset_id: Annotated[str, Form()],
        scenario_id: Annotated[str, Form()],
        sensor_dataset_id: Annotated[str, Form()],
        csrf_token: Annotated[str, Form()],
        image: Annotated[UploadFile, File()],
        svc: Annotated[InspectionApplicationService, Depends(_service)],
    ):
        _verify_form_csrf(request, csrf_token)
        content = await image.read(resolved_settings.max_upload_bytes + 1)
        inspection = svc.create_inspection(
            asset_id=asset_id,
            scenario_id=scenario_id,
            sensor_dataset_id=sensor_dataset_id,
            image_content=content,
            image_filename=image.filename,
            image_media_type=image.content_type,
        )
        return RedirectResponse(f"/inspections/{inspection.inspection_id}", status_code=303)

    @app.get("/inspections/{inspection_id}", response_class=HTMLResponse, include_in_schema=False)
    def inspection_page(
        inspection_id: str,
        request: Request,
        svc: Annotated[InspectionApplicationService, Depends(_service)],
    ):
        inspection = svc.get_inspection(inspection_id)
        return templates.TemplateResponse(
            request,
            "inspection.html",
            _template_context(
                request,
                title=inspection.inspection_id,
                inspection=inspection,
                asset=svc.get_asset(inspection.asset_id),
                artifact=svc.get_artifact(inspection.image_artifact_id),
                latest_run=svc.latest_run_for_inspection(inspection_id),
            ),
        )

    @app.post("/ui/inspections/{inspection_id}/run", include_in_schema=False)
    def start_run_page(
        inspection_id: str,
        request: Request,
        csrf_token: Annotated[str, Form()],
        svc: Annotated[InspectionApplicationService, Depends(_service)],
    ):
        _verify_form_csrf(request, csrf_token)
        run = svc.run_inspection(inspection_id)
        request.state.run_id = run.run_id
        return RedirectResponse(f"/runs/{run.run_id}", status_code=303)

    @app.get("/runs/{run_id}", response_class=HTMLResponse, include_in_schema=False)
    def run_page(
        run_id: str, request: Request, svc: Annotated[InspectionApplicationService, Depends(_service)]
    ):
        request.state.run_id = run_id
        return templates.TemplateResponse(
            request,
            "run_detail.html",
            _template_context(request, title=run_id, detail=svc.get_run_detail(run_id)),
        )

    @app.get("/partials/runs/{run_id}", response_class=HTMLResponse, include_in_schema=False)
    def run_partial(
        run_id: str, request: Request, svc: Annotated[InspectionApplicationService, Depends(_service)]
    ):
        return templates.TemplateResponse(
            request,
            "partials/run_status.html",
            _template_context(request, detail=svc.get_run_detail(run_id)),
        )

    @app.post("/ui/approvals/{approval_id}/decision", include_in_schema=False)
    def decide_page(
        approval_id: str,
        request: Request,
        decision: Annotated[ApprovalDecision, Form()],
        reviewer: Annotated[str, Form()],
        reason: Annotated[str, Form()],
        csrf_token: Annotated[str, Form()],
        svc: Annotated[InspectionApplicationService, Depends(_service)],
    ):
        _verify_form_csrf(request, csrf_token)
        run = svc.decide_approval(
            approval_id,
            ApprovalDecisionInput(decision=decision, reviewer=reviewer, comment=reason or None),
        )
        request.state.run_id = run.run_id
        return RedirectResponse(f"/runs/{run.run_id}", status_code=303)

    @app.get("/work-orders", response_class=HTMLResponse, include_in_schema=False)
    def work_orders_page(
        request: Request, svc: Annotated[InspectionApplicationService, Depends(_service)]
    ):
        return templates.TemplateResponse(
            request,
            "work_orders.html",
            _template_context(request, title="Work Orders", work_orders=svc.list_work_orders()),
        )

    @app.get("/work-orders/{work_order_id}", response_class=HTMLResponse, include_in_schema=False)
    def work_order_page(
        work_order_id: str,
        request: Request,
        svc: Annotated[InspectionApplicationService, Depends(_service)],
    ):
        return templates.TemplateResponse(
            request,
            "work_order.html",
            _template_context(
                request, title=work_order_id, work_order=svc.get_work_order(work_order_id)
            ),
        )

    @app.get("/artifacts/{artifact_id}", include_in_schema=False)
    def artifact_file(
        artifact_id: str, svc: Annotated[InspectionApplicationService, Depends(_service)]
    ):
        artifact = svc.get_artifact(artifact_id)
        return FileResponse(svc.get_artifact_path(artifact_id), media_type=artifact.media_type)

    return app


app = create_app()
