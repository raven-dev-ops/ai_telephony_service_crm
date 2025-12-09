import logging
import os
import sys
import time

from fastapi import FastAPI, HTTPException, Request, Response
from sqlalchemy import text

from .config import get_settings
from .db import SQLALCHEMY_AVAILABLE, SessionLocal, init_db
from .logging_config import configure_logging
from .metrics import RouteMetrics, metrics
from .services.audit import record_audit_event
from .services.retention_purge import start_retention_scheduler
from .services.rate_limit import RateLimiter, RateLimitError
from .routers import (
    business_admin,
    chat_widget,
    crm,
    auth_integration,
    chat_api,
    contacts_import,
    billing,
    owner,
    owner_assistant,
    owner_export,
    public_signup,
    reminders,
    retention,
    qbo_integration,
    auth_accounts,
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
    testing_mode = (
        bool(os.getenv("PYTEST_CURRENT_TEST"))
        or os.getenv("TESTING", "false").lower() == "true"
        or "pytest" in sys.modules
    )
    if testing_mode:
        os.environ.setdefault("TESTING", "true")

    rate_limit_disabled = os.getenv("RATE_LIMIT_DISABLED", "false").lower() == "true"
    rate_limit_per_minute = settings.rate_limit_per_minute
    rate_limit_burst = settings.rate_limit_burst
    if testing_mode:
        # Keep rate limits effectively disabled during tests unless explicitly tightened.
        if rate_limit_per_minute == 120 and rate_limit_burst == 20:
            rate_limit_per_minute = 1_000_000
            rate_limit_burst = 100_000
    rate_limiter = RateLimiter(
        per_minute=rate_limit_per_minute,
        burst=rate_limit_burst,
        whitelist_ips=set(settings.rate_limit_whitelist_ips),
        disabled=rate_limit_disabled,
    )
    security_headers_enabled = settings.security_headers_enabled
    security_csp = settings.security_csp
    security_hsts_enabled = settings.security_hsts_enabled
    security_hsts_max_age = settings.security_hsts_max_age

    multi_tenant = False
    business_count = None
    if SQLALCHEMY_AVAILABLE and SessionLocal is not None:
        try:
            from .db_models import BusinessDB  # local import to avoid cycles

            session_db = SessionLocal()
            try:
                business_count = session_db.query(BusinessDB).count()
                multi_tenant = business_count > 1
            finally:
                session_db.close()
        except Exception:
            multi_tenant = False
            business_count = None

    # Log only high-level, non-sensitive configuration to avoid leaking secrets in logs.
    logger.info(
        "app_config_summary_sanitized",
        extra={
            "config_sanitized": True,
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

    purge_interval_hours = getattr(settings, "retention_purge_interval_hours", 24)
    if (
        SQLALCHEMY_AVAILABLE
        and SessionLocal is not None
        and purge_interval_hours
        and purge_interval_hours > 0
    ):
        try:
            start_retention_scheduler(int(purge_interval_hours * 3600))
        except Exception:
            metrics.background_job_errors += 1
            logger.exception("retention_purge_scheduler_failed")

    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next):
        path = request.url.path
        exempt_paths = {"/healthz", "/readyz", "/metrics", "/metrics/prometheus"}

        metrics.total_requests += 1
        route_metrics = metrics.route_metrics.setdefault(path, RouteMetrics())
        route_metrics.request_count += 1
        start = time.time()

        # Rate limiting on auth/chat/webhooks/voice paths unless explicitly exempted.
        if path not in exempt_paths and path.startswith(
            (
                "/v1/auth",
                "/v1/chat",
                "/twilio/",
                "/v1/twilio/",
                "/telephony/",
                "/v1/telephony/",
                "/v1/voice/",
            )
        ):
            client_ip = request.client.host if request.client else "unknown"
            api_key = request.headers.get("X-API-Key") or request.headers.get(
                "X-Widget-Token"
            )
            try:
                rate_limiter.check(key=f"{client_ip}:{api_key or 'anon'}")
            except RateLimitError as exc:
                metrics.total_errors += 1
                route_metrics.error_count += 1
                response = Response(
                    status_code=429,
                    content="Rate limit exceeded. Please retry later.",
                    headers={"Retry-After": str(exc.retry_after_seconds)},
                )
                # Record audit information for rejected requests as well.
                await record_audit_event(request, response.status_code)
                if security_headers_enabled:
                    _apply_security_headers(
                        response,
                        security_csp,
                        security_hsts_enabled,
                        security_hsts_max_age,
                    )
                return response

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
        if security_headers_enabled:
            _apply_security_headers(
                response, security_csp, security_hsts_enabled, security_hsts_max_age
            )
        return response

    app.include_router(voice.router, prefix="/v1/voice", tags=["voice"])
    # Support both legacy and versioned prefixes for telephony and Twilio
    # endpoints so existing integrations continue to function while new
    # clients can adopt /v1/* routes.
    app.include_router(telephony.router, prefix="/telephony", tags=["telephony"])
    app.include_router(telephony.router, prefix="/v1/telephony", tags=["telephony"])
    app.include_router(crm.router, prefix="/v1/crm", tags=["crm"])
    app.include_router(
        auth_integration.router,
        prefix="/auth",
        tags=["auth-integrations"],
    )
    app.include_router(owner.router, prefix="/v1/owner", tags=["owner"])
    app.include_router(
        owner_export.router, prefix="/v1/owner/export", tags=["owner-export"]
    )
    app.include_router(
        owner_assistant.router,
        prefix="/v1/owner/assistant",
        tags=["owner-assistant"],
    )
    app.include_router(reminders.router, prefix="/v1/reminders", tags=["reminders"])
    app.include_router(retention.router, prefix="/v1/retention", tags=["retention"])
    app.include_router(chat_widget.router, prefix="/v1/widget", tags=["widget"])
    app.include_router(chat_api.router, prefix="/v1/chat", tags=["chat"])
    app.include_router(contacts_import.router, prefix="/v1/contacts", tags=["contacts"])
    app.include_router(
        qbo_integration.router, prefix="/v1/integrations/qbo", tags=["integrations"]
    )
    app.include_router(billing.router, prefix="/v1/billing", tags=["billing"])
    app.include_router(auth_accounts.router, prefix="/v1/auth", tags=["auth"])
    app.include_router(business_admin.router, prefix="/v1/admin", tags=["admin"])
    app.include_router(twilio_integration.router, prefix="/twilio", tags=["twilio"])
    app.include_router(twilio_integration.router, prefix="/v1/twilio", tags=["twilio"])
    app.include_router(public_signup.router, tags=["public-signup"])
    # Fallback endpoint without prefix to satisfy external callback requirements.
    app.add_api_route(
        "/fallback",
        twilio_integration.twilio_fallback,
        methods=["GET", "POST"],
        tags=["twilio"],
    )

    @app.get("/healthz", tags=["health"])
    async def health_check() -> dict:
        return {"status": "ok"}

    @app.get("/readyz", tags=["health"])
    async def readiness_check() -> dict:
        """Readiness probe that includes basic dependency checks.

        Currently verifies database connectivity when SQLAlchemy support is
        enabled; other external dependency checks can be added over time.
        """
        db_available = SQLALCHEMY_AVAILABLE and SessionLocal is not None
        db_healthy = False
        if db_available:
            session = SessionLocal()
            try:
                session.execute(text("SELECT 1"))
                db_healthy = True
            except Exception:
                db_healthy = False
            finally:
                session.close()
        status_value = "ok" if db_healthy or not db_available else "degraded"
        return {
            "status": status_value,
            "database": {
                "available": db_available,
                "healthy": db_healthy,
            },
        }

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
        emit(
            "ai_telephony_appointments_scheduled", float(metrics.appointments_scheduled)
        )
        emit("ai_telephony_users_registered", float(metrics.users_registered))
        emit("ai_telephony_sms_sent_total", float(metrics.sms_sent_total))
        emit("ai_telephony_twilio_voice_requests", float(metrics.twilio_voice_requests))
        emit("ai_telephony_twilio_voice_errors", float(metrics.twilio_voice_errors))
        emit("ai_telephony_twilio_sms_requests", float(metrics.twilio_sms_requests))
        emit("ai_telephony_twilio_sms_errors", float(metrics.twilio_sms_errors))
        emit(
            "ai_telephony_voice_session_requests", float(metrics.voice_session_requests)
        )
        emit("ai_telephony_voice_session_errors", float(metrics.voice_session_errors))
        emit(
            "ai_telephony_subscription_activations",
            float(metrics.subscription_activations),
        )
        emit(
            "ai_telephony_subscription_failures",
            float(metrics.subscription_failures),
        )
        emit("ai_telephony_qbo_connections", float(metrics.qbo_connections))
        emit("ai_telephony_qbo_sync_errors", float(metrics.qbo_sync_errors))
        emit("ai_telephony_contacts_imported", float(metrics.contacts_imported))
        emit(
            "ai_telephony_contacts_import_errors", float(metrics.contacts_import_errors)
        )
        emit("ai_telephony_chat_messages", float(metrics.chat_messages))
        emit("ai_telephony_chat_failures", float(metrics.chat_failures))
        emit(
            "ai_telephony_chat_latency_ms_total",
            float(metrics.chat_latency_ms_total),
        )
        emit("ai_telephony_chat_latency_ms_max", float(metrics.chat_latency_ms_max))
        emit(
            "ai_telephony_chat_latency_samples",
            float(metrics.chat_latency_samples),
        )
        # Chat latency histogram buckets (cumulative)
        bucket_bounds = [100, 250, 500, 1000, 2000, 5000, 10000]
        cumulative = 0.0
        for bound in bucket_bounds:
            cumulative += float(metrics.chat_latency_bucket_counts.get(bound, 0))
            lines.append(
                f'ai_telephony_chat_latency_bucket{{le="{bound/1000:.3f}"}} {cumulative}'
            )
        cumulative += float(metrics.chat_latency_bucket_counts.get(float("inf"), 0))
        lines.append(f'ai_telephony_chat_latency_bucket{{le="+Inf"}} {cumulative}')
        emit("ai_telephony_chat_latency_count", float(metrics.chat_latency_samples))
        emit("ai_telephony_chat_latency_sum", float(metrics.chat_latency_ms_total))

        # Percentiles from rolling window
        if metrics.chat_latency_values:
            sorted_vals = sorted(metrics.chat_latency_values)
            count = len(sorted_vals)

            def pct(p: float) -> float:
                if count == 0:
                    return 0.0
                idx = min(count - 1, int(round(p * (count - 1))))
                return sorted_vals[idx]

            emit("ai_telephony_chat_latency_p50_ms", pct(0.50))
            emit("ai_telephony_chat_latency_p95_ms", pct(0.95))
            emit("ai_telephony_chat_latency_p99_ms", pct(0.99))
        else:
            emit("ai_telephony_chat_latency_p50_ms", 0.0)
            emit("ai_telephony_chat_latency_p95_ms", 0.0)
            emit("ai_telephony_chat_latency_p99_ms", 0.0)

        # Conversation latency/profile metrics
        emit(
            "ai_telephony_conversation_messages",
            float(metrics.conversation_messages),
        )
        emit(
            "ai_telephony_conversation_failures",
            float(metrics.conversation_failures),
        )
        emit(
            "ai_telephony_conversation_latency_ms_total",
            float(metrics.conversation_latency_ms_total),
        )
        emit(
            "ai_telephony_conversation_latency_ms_max",
            float(metrics.conversation_latency_ms_max),
        )
        emit(
            "ai_telephony_conversation_latency_samples",
            float(metrics.conversation_latency_samples),
        )
        conv_bucket_bounds = [250, 500, 1000, 2000, 4000, 8000, 12000]
        cumulative = 0.0
        for bound in conv_bucket_bounds:
            cumulative += float(
                metrics.conversation_latency_bucket_counts.get(bound, 0)
            )
            lines.append(
                f'ai_telephony_conversation_latency_bucket{{le="{bound/1000:.3f}"}} {cumulative}'
            )
        cumulative += float(
            metrics.conversation_latency_bucket_counts.get(float("inf"), 0)
        )
        lines.append(
            f'ai_telephony_conversation_latency_bucket{{le="+Inf"}} {cumulative}'
        )
        emit(
            "ai_telephony_conversation_latency_count",
            float(metrics.conversation_latency_samples),
        )
        emit(
            "ai_telephony_conversation_latency_sum",
            float(metrics.conversation_latency_ms_total),
        )
        if metrics.conversation_latency_values:
            sorted_vals = sorted(metrics.conversation_latency_values)
            count = len(sorted_vals)

            def pct_conv(p: float) -> float:
                if count == 0:
                    return 0.0
                idx = min(count - 1, int(round(p * (count - 1))))
                return sorted_vals[idx]

            emit("ai_telephony_conversation_latency_p50_ms", pct_conv(0.50))
            emit("ai_telephony_conversation_latency_p95_ms", pct_conv(0.95))
            emit("ai_telephony_conversation_latency_p99_ms", pct_conv(0.99))
        else:
            emit("ai_telephony_conversation_latency_p50_ms", 0.0)
            emit("ai_telephony_conversation_latency_p95_ms", 0.0)
            emit("ai_telephony_conversation_latency_p99_ms", 0.0)

        emit("ai_telephony_job_queue_enqueued", float(metrics.job_queue_enqueued))
        emit("ai_telephony_job_queue_completed", float(metrics.job_queue_completed))
        emit("ai_telephony_job_queue_failed", float(metrics.job_queue_failed))
        emit(
            "ai_telephony_billing_webhook_failures",
            float(metrics.billing_webhook_failures),
        )
        emit(
            "ai_telephony_background_job_errors",
            float(metrics.background_job_errors),
        )
        emit(
            "ai_telephony_retention_purge_runs",
            float(metrics.retention_purge_runs),
        )
        emit(
            "ai_telephony_retention_appointments_deleted",
            float(metrics.retention_appointments_deleted),
        )
        emit(
            "ai_telephony_retention_conversations_deleted",
            float(metrics.retention_conversations_deleted),
        )
        emit(
            "ai_telephony_retention_messages_deleted",
            float(metrics.retention_messages_deleted),
        )

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


def _apply_security_headers(
    response: Response,
    csp: str,
    hsts_enabled: bool,
    hsts_max_age: int,
) -> None:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
    )
    if csp:
        response.headers.setdefault("Content-Security-Policy", csp)
    if hsts_enabled:
        response.headers.setdefault(
            "Strict-Transport-Security", f"max-age={hsts_max_age}; includeSubDomains"
        )
