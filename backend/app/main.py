import logging
import time

from fastapi import FastAPI, HTTPException, Request, Response

from .config import get_settings
from .db import SQLALCHEMY_AVAILABLE, SessionLocal, init_db
from .logging_config import configure_logging
from .metrics import RouteMetrics, metrics
from .services.audit import record_audit_event
from .routers import (
    business_admin,
    chat_widget,
    crm,
    owner,
    owner_export,
    reminders,
    retention,
    telephony,
    twilio_integration,
    voice,
)


def create_app() -> FastAPI:
    configure_logging()
    init_db()

    app = FastAPI(
        title="AI Telephony Backend",
        description="Backend for AI voice assistant, scheduling, and basic CRM.",
        version="0.1.0",
    )

    # Log a brief configuration summary for operational visibility.
    settings = get_settings()
    logger = logging.getLogger(__name__)

    multi_tenant = False
    business_count = None
    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        try:
            from .db_models import Business  # local import to avoid cycles

            session_db = SessionLocal()
            try:
                business_count = session_db.query(Business).count()
                multi_tenant = business_count > 1
            finally:
                session_db.close()
        except Exception:
            multi_tenant = False
            business_count = None

    logger.info(
        "app_config_summary",
        extra={
            "calendar_use_stub": settings.calendar.use_stub,
            "speech_provider": settings.speech.provider,
            "sms_provider": settings.sms.provider,
            "require_business_api_key": settings.require_business_api_key,
            "admin_api_key_configured": bool(settings.admin_api_key),
            "owner_dashboard_token_configured": bool(settings.owner_dashboard_token),
            "verify_twilio_signatures": settings.sms.verify_twilio_signatures,
            "multi_tenant_mode": multi_tenant,
            "business_count": business_count,
        },
    )
    # Warn when running with weak tenant auth while database/multi-tenant support is available.
    if (
        SQLALCHEMY_AVAILABLE
        and SessionLocal is not None
        and not settings.require_business_api_key
    ):
        extra = {
            "require_business_api_key": False,
            "database_url_configured": True,
            "multi_tenant_mode": multi_tenant,
            "business_count": business_count,
        }
        logger.warning("tenant_auth_require_business_api_key_false", extra=extra)
        if multi_tenant:
            logger.warning(
                "multi_tenant_weak_auth_configuration",
                extra=extra,
            )

    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next):
        metrics.total_requests += 1
        path = request.url.path
        route_metrics = metrics.route_metrics.setdefault(path, RouteMetrics())
        route_metrics.request_count += 1
        start = time.time()
        try:
            response = await call_next(request)
        except HTTPException as exc:
            metrics.total_errors += 1
            route_metrics.error_count += 1
            # Record audit information for rejected requests as well.
            await record_audit_event(request, exc.status_code)
            raise
        except Exception:
            metrics.total_errors += 1
            route_metrics.error_count += 1
            await record_audit_event(request, 500)
            raise
        latency_ms = (time.time() - start) * 1000.0
        route_metrics.total_latency_ms += latency_ms
        if latency_ms > route_metrics.max_latency_ms:
            route_metrics.max_latency_ms = latency_ms
        if response.status_code >= 500:
            metrics.total_errors += 1
            route_metrics.error_count += 1
        # Successful or handled responses are also audited.
        await record_audit_event(request, response.status_code)
        return response

    app.include_router(voice.router, prefix="/v1/voice", tags=["voice"])
    app.include_router(telephony.router, prefix="/telephony", tags=["telephony"])
    app.include_router(crm.router, prefix="/v1/crm", tags=["crm"])
    app.include_router(owner.router, prefix="/v1/owner", tags=["owner"])
    app.include_router(
        owner_export.router, prefix="/v1/owner/export", tags=["owner-export"]
    )
    app.include_router(reminders.router, prefix="/v1/reminders", tags=["reminders"])
    app.include_router(retention.router, prefix="/v1/retention", tags=["retention"])
    app.include_router(chat_widget.router, prefix="/v1/widget", tags=["widget"])
    app.include_router(business_admin.router, prefix="/v1/admin", tags=["admin"])
    app.include_router(twilio_integration.router, prefix="/twilio", tags=["twilio"])

    @app.get("/healthz", tags=["health"])
    async def health_check() -> dict:
        return {"status": "ok"}

    @app.get("/metrics", tags=["metrics"])
    async def get_metrics() -> dict:
        return metrics.as_dict()

    @app.get("/metrics/prometheus", tags=["metrics"])
    async def get_metrics_prometheus() -> Response:
        """Expose a minimal Prometheus text-format view of key metrics.

        This is intentionally small and focuses on the most useful counters.
        """
        lines: list[str] = []

        def emit(name: str, value: float) -> None:
            lines.append(f"{name} {value}")

        emit("ai_telephony_total_requests", float(metrics.total_requests))
        emit("ai_telephony_total_errors", float(metrics.total_errors))
        emit("ai_telephony_appointments_scheduled", float(metrics.appointments_scheduled))
        emit("ai_telephony_sms_sent_total", float(metrics.sms_sent_total))
        emit("ai_telephony_twilio_voice_requests", float(metrics.twilio_voice_requests))
        emit("ai_telephony_twilio_voice_errors", float(metrics.twilio_voice_errors))
        emit("ai_telephony_twilio_sms_requests", float(metrics.twilio_sms_requests))
        emit("ai_telephony_twilio_sms_errors", float(metrics.twilio_sms_errors))
        emit("ai_telephony_voice_session_requests", float(metrics.voice_session_requests))
        emit("ai_telephony_voice_session_errors", float(metrics.voice_session_errors))

        # Per-route request/error counts with a path label.
        for path, rm in metrics.route_metrics.items():
            label_path = path.replace("\\", "\\\\").replace('"', r"\"")
            lines.append(
                f'ai_telephony_route_request_count{{path="{label_path}"}} {rm.request_count}'
            )
            lines.append(
                f'ai_telephony_route_error_count{{path="{label_path}"}} {rm.error_count}'
            )

        body = "\n".join(lines) + "\n"
        return Response(content=body, media_type="text/plain; version=0.0.4")

    return app


app = create_app()
