from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict


@dataclass
class BusinessSmsMetrics:
    sms_sent_total: int = 0
    sms_sent_owner: int = 0
    sms_sent_customer: int = 0
    lead_followups_sent: int = 0
    retention_messages_sent: int = 0
    sms_confirmations_via_sms: int = 0
    sms_cancellations_via_sms: int = 0
    sms_reschedules_via_sms: int = 0
    sms_opt_out_events: int = 0
    sms_opt_in_events: int = 0


@dataclass
class BusinessTwilioMetrics:
    voice_requests: int = 0
    voice_errors: int = 0
    sms_requests: int = 0
    sms_errors: int = 0


@dataclass
class BusinessVoiceSessionMetrics:
    requests: int = 0
    errors: int = 0


@dataclass
class RouteMetrics:
    request_count: int = 0
    error_count: int = 0
    total_latency_ms: float = 0.0
    max_latency_ms: float = 0.0


@dataclass
class CallbackItem:
    phone: str
    first_seen: datetime
    last_seen: datetime
    count: int = 0
    channel: str = "phone"
    lead_source: str | None = None
    status: str = "PENDING"
    last_result: str | None = None
    reason: str = "MISSED_CALL"
    voicemail_url: str | None = None


@dataclass
class Metrics:
    total_requests: int = 0
    total_errors: int = 0
    alert_events_total: int = 0
    alerts_open: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    alert_last_fired: Dict[str, str] = field(default_factory=dict)
    appointments_scheduled: int = 0
    users_registered: int = 0
    sms_sent_total: int = 0
    sms_sent_owner: int = 0
    sms_sent_customer: int = 0
    notification_attempts: int = 0
    notification_failures: int = 0
    lead_followups_sent: int = 0
    subscription_activations: int = 0
    subscription_failures: int = 0
    qbo_connections: int = 0
    qbo_sync_errors: int = 0
    contacts_imported: int = 0
    contacts_import_errors: int = 0
    chat_messages: int = 0
    chat_failures: int = 0
    chat_latency_ms_total: float = 0.0
    chat_latency_ms_max: float = 0.0
    chat_latency_samples: int = 0
    chat_latency_values: list[float] = field(default_factory=list)
    chat_latency_bucket_counts: Dict[float, int] = field(default_factory=dict)
    conversation_messages: int = 0
    conversation_failures: int = 0
    conversation_latency_ms_total: float = 0.0
    conversation_latency_ms_max: float = 0.0
    conversation_latency_samples: int = 0
    conversation_latency_values: list[float] = field(default_factory=list)
    conversation_latency_bucket_counts: Dict[float, int] = field(default_factory=dict)
    billing_webhook_failures: int = 0
    background_job_errors: int = 0
    retention_purge_runs: int = 0
    retention_appointments_deleted: int = 0
    retention_conversations_deleted: int = 0
    retention_messages_deleted: int = 0
    job_queue_enqueued: int = 0
    job_queue_completed: int = 0
    job_queue_failed: int = 0
    speech_circuit_trips: int = 0
    speech_alerted_businesses: set[str] = field(default_factory=set)
    rate_limit_blocks_total: int = 0
    rate_limit_blocks_by_business: Dict[str, int] = field(default_factory=dict)
    rate_limit_blocks_by_ip: Dict[str, int] = field(default_factory=dict)
    sms_by_business: Dict[str, BusinessSmsMetrics] = field(default_factory=dict)
    twilio_voice_requests: int = 0
    twilio_voice_errors: int = 0
    twilio_sms_requests: int = 0
    twilio_sms_errors: int = 0
    twilio_webhook_failures: int = 0
    calendar_webhook_failures: int = 0
    twilio_by_business: Dict[str, BusinessTwilioMetrics] = field(default_factory=dict)
    voice_session_requests: int = 0
    voice_session_errors: int = 0
    voice_sessions_by_business: Dict[str, BusinessVoiceSessionMetrics] = field(
        default_factory=dict
    )
    route_metrics: Dict[str, RouteMetrics] = field(default_factory=dict)
    callbacks_by_business: Dict[str, Dict[str, CallbackItem]] = field(
        default_factory=dict
    )
    retention_by_business: Dict[str, Dict[str, int]] = field(default_factory=dict)

    def record_chat_latency(self, latency_ms: float) -> None:
        """Track chat latency with buckets and rolling samples."""
        self.chat_latency_ms_total += latency_ms
        if latency_ms > self.chat_latency_ms_max:
            self.chat_latency_ms_max = latency_ms
        self.chat_latency_samples += 1

        self.chat_latency_values.append(latency_ms)
        # Keep a rolling window to bound memory.
        if len(self.chat_latency_values) > 500:
            del self.chat_latency_values[: len(self.chat_latency_values) - 500]

        buckets = [100, 250, 500, 1000, 2000, 5000, 10000]
        bucket_hit = False
        for b in buckets:
            if latency_ms <= b:
                self.chat_latency_bucket_counts[b] = (
                    self.chat_latency_bucket_counts.get(b, 0) + 1
                )
                bucket_hit = True
                break
        if not bucket_hit:
            self.chat_latency_bucket_counts[float("inf")] = (
                self.chat_latency_bucket_counts.get(float("inf"), 0) + 1
            )

    def record_conversation_latency(self, latency_ms: float) -> None:
        """Track conversation latency with buckets and rolling samples."""
        self.conversation_latency_ms_total += latency_ms
        if latency_ms > self.conversation_latency_ms_max:
            self.conversation_latency_ms_max = latency_ms
        self.conversation_latency_samples += 1

        self.conversation_latency_values.append(latency_ms)
        if len(self.conversation_latency_values) > 500:
            del self.conversation_latency_values[
                : len(self.conversation_latency_values) - 500
            ]

        buckets = [250, 500, 1000, 2000, 4000, 8000, 12000]
        bucket_hit = False
        for b in buckets:
            if latency_ms <= b:
                self.conversation_latency_bucket_counts[b] = (
                    self.conversation_latency_bucket_counts.get(b, 0) + 1
                )
                bucket_hit = True
                break
        if not bucket_hit:
            self.conversation_latency_bucket_counts[float("inf")] = (
                self.conversation_latency_bucket_counts.get(float("inf"), 0) + 1
            )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "total_requests": self.total_requests,
            "total_errors": self.total_errors,
            "alert_events_total": self.alert_events_total,
            "alerts_open": dict(self.alerts_open),
            "alert_last_fired": dict(self.alert_last_fired),
            "appointments_scheduled": self.appointments_scheduled,
            "users_registered": self.users_registered,
            "sms_sent_total": self.sms_sent_total,
            "sms_sent_owner": self.sms_sent_owner,
            "sms_sent_customer": self.sms_sent_customer,
            "notification_attempts": self.notification_attempts,
            "notification_failures": self.notification_failures,
            "lead_followups_sent": self.lead_followups_sent,
            "subscription_activations": self.subscription_activations,
            "subscription_failures": self.subscription_failures,
            "qbo_connections": self.qbo_connections,
            "qbo_sync_errors": self.qbo_sync_errors,
            "contacts_imported": self.contacts_imported,
            "contacts_import_errors": self.contacts_import_errors,
            "chat_messages": self.chat_messages,
            "chat_failures": self.chat_failures,
            "chat_latency_ms_total": self.chat_latency_ms_total,
            "chat_latency_ms_max": self.chat_latency_ms_max,
            "chat_latency_samples": self.chat_latency_samples,
            "chat_latency_bucket_counts": {
                str(k): v for k, v in self.chat_latency_bucket_counts.items()
            },
            "conversation_messages": self.conversation_messages,
            "conversation_failures": self.conversation_failures,
            "conversation_latency_ms_total": self.conversation_latency_ms_total,
            "conversation_latency_ms_max": self.conversation_latency_ms_max,
            "conversation_latency_samples": self.conversation_latency_samples,
            "conversation_latency_bucket_counts": {
                str(k): v for k, v in self.conversation_latency_bucket_counts.items()
            },
            "job_queue_enqueued": self.job_queue_enqueued,
            "job_queue_completed": self.job_queue_completed,
            "job_queue_failed": self.job_queue_failed,
            "speech_circuit_trips": self.speech_circuit_trips,
            "speech_alerted_businesses": list(self.speech_alerted_businesses),
            "rate_limit_blocks_total": self.rate_limit_blocks_total,
            "rate_limit_blocks_by_business": dict(self.rate_limit_blocks_by_business),
            "rate_limit_blocks_by_ip": dict(self.rate_limit_blocks_by_ip),
            "billing_webhook_failures": self.billing_webhook_failures,
            "background_job_errors": self.background_job_errors,
            "retention_purge_runs": self.retention_purge_runs,
            "retention_appointments_deleted": self.retention_appointments_deleted,
            "retention_conversations_deleted": self.retention_conversations_deleted,
            "retention_messages_deleted": self.retention_messages_deleted,
            "sms_by_business": {
                business_id: {
                    "sms_sent_total": m.sms_sent_total,
                    "sms_sent_owner": m.sms_sent_owner,
                    "sms_sent_customer": m.sms_sent_customer,
                    "lead_followups_sent": m.lead_followups_sent,
                    "retention_messages_sent": m.retention_messages_sent,
                    "sms_confirmations_via_sms": m.sms_confirmations_via_sms,
                    "sms_cancellations_via_sms": m.sms_cancellations_via_sms,
                    "sms_reschedules_via_sms": m.sms_reschedules_via_sms,
                    "sms_opt_out_events": m.sms_opt_out_events,
                    "sms_opt_in_events": m.sms_opt_in_events,
                }
                for business_id, m in self.sms_by_business.items()
            },
            "twilio_voice_requests": self.twilio_voice_requests,
            "twilio_voice_errors": self.twilio_voice_errors,
            "twilio_sms_requests": self.twilio_sms_requests,
            "twilio_sms_errors": self.twilio_sms_errors,
            "twilio_webhook_failures": self.twilio_webhook_failures,
            "calendar_webhook_failures": self.calendar_webhook_failures,
            "twilio_by_business": {
                business_id: {
                    "voice_requests": m.voice_requests,
                    "voice_errors": m.voice_errors,
                    "sms_requests": m.sms_requests,
                    "sms_errors": m.sms_errors,
                }
                for business_id, m in self.twilio_by_business.items()
            },
            "voice_session_requests": self.voice_session_requests,
            "voice_session_errors": self.voice_session_errors,
            "voice_sessions_by_business": {
                business_id: {
                    "requests": m.requests,
                    "errors": m.errors,
                }
                for business_id, m in self.voice_sessions_by_business.items()
            },
            "route_metrics": {
                path: {
                    "request_count": rm.request_count,
                    "error_count": rm.error_count,
                    "total_latency_ms": rm.total_latency_ms,
                    "max_latency_ms": rm.max_latency_ms,
                }
                for path, rm in self.route_metrics.items()
            },
            "callbacks_by_business": {
                business_id: {
                    phone: {
                        "phone": item.phone,
                        "first_seen": item.first_seen.isoformat(),
                        "last_seen": item.last_seen.isoformat(),
                        "count": item.count,
                        "channel": item.channel,
                        "lead_source": item.lead_source or "",
                        "status": item.status,
                        "last_result": item.last_result or "",
                        "reason": item.reason,
                    }
                    for phone, item in queue.items()
                }
                for business_id, queue in self.callbacks_by_business.items()
            },
            "retention_by_business": {
                business_id: dict(campaigns)
                for business_id, campaigns in self.retention_by_business.items()
            },
        }


metrics = Metrics()
