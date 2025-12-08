"""Baseline schema for core tables + SMS audit log."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "0001_sms_audit_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "businesses" not in existing_tables:
        op.create_table(
            "businesses",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("vertical", sa.String(), nullable=True),
            sa.Column("api_key", sa.String(), nullable=True),
            sa.Column("calendar_id", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False, server_default="ACTIVE"),
            sa.Column("owner_phone", sa.String(), nullable=True),
            sa.Column("emergency_keywords", sa.String(), nullable=True),
            sa.Column("default_reminder_hours", sa.Integer(), nullable=True),
            sa.Column("service_duration_config", sa.String(), nullable=True),
            sa.Column("open_hour", sa.Integer(), nullable=True),
            sa.Column("close_hour", sa.Integer(), nullable=True),
            sa.Column("closed_days", sa.String(), nullable=True),
            sa.Column("appointment_retention_days", sa.Integer(), nullable=True),
            sa.Column("conversation_retention_days", sa.Integer(), nullable=True),
            sa.Column("language_code", sa.String(), nullable=True),
            sa.Column("max_jobs_per_day", sa.Integer(), nullable=True),
            sa.Column(
                "reserve_mornings_for_emergencies",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
            sa.Column("travel_buffer_minutes", sa.Integer(), nullable=True),
            sa.Column("twilio_missed_statuses", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("widget_token", sa.String(), nullable=True),
            sa.Column(
                "retention_enabled",
                sa.Boolean(),
                nullable=True,
                server_default=sa.true(),
            ),
            sa.Column("retention_sms_template", sa.Text(), nullable=True),
            sa.Column("zip_code", sa.String(length=255), nullable=True),
            sa.Column("median_household_income", sa.Integer(), nullable=True),
            sa.Column("owner_name", sa.String(length=255), nullable=True),
            sa.Column("owner_email", sa.String(length=255), nullable=True),
            sa.Column("owner_profile_image_url", sa.String(length=1024), nullable=True),
            sa.Column("service_tier", sa.String(length=64), nullable=True),
            sa.Column("tts_voice", sa.String(length=64), nullable=True),
            sa.Column("terms_accepted_at", sa.DateTime(), nullable=True),
            sa.Column("privacy_accepted_at", sa.DateTime(), nullable=True),
            sa.Column(
                "integration_linkedin_status", sa.String(length=32), nullable=True
            ),
            sa.Column("integration_gmail_status", sa.String(length=32), nullable=True),
            sa.Column(
                "integration_gcalendar_status", sa.String(length=32), nullable=True
            ),
            sa.Column("integration_openai_status", sa.String(length=32), nullable=True),
            sa.Column("integration_twilio_status", sa.String(length=32), nullable=True),
            sa.Column("integration_qbo_status", sa.String(length=32), nullable=True),
            sa.Column("qbo_realm_id", sa.String(length=128), nullable=True),
            sa.Column("qbo_access_token", sa.Text(), nullable=True),
            sa.Column("qbo_refresh_token", sa.Text(), nullable=True),
            sa.Column("qbo_token_expires_at", sa.DateTime(), nullable=True),
            sa.Column("onboarding_step", sa.String(length=64), nullable=True),
            sa.Column(
                "onboarding_completed",
                sa.Boolean(),
                nullable=True,
                server_default=sa.false(),
            ),
            sa.Column("stripe_customer_id", sa.String(length=255), nullable=True),
            sa.Column("stripe_subscription_id", sa.String(length=255), nullable=True),
            sa.Column("subscription_status", sa.String(length=64), nullable=True),
            sa.Column("subscription_current_period_end", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_businesses_api_key", "businesses", ["api_key"])
        op.create_index("ix_businesses_widget_token", "businesses", ["widget_token"])
        op.create_index("ix_businesses_created_at", "businesses", ["created_at"])

    if "users" not in existing_tables:
        op.create_table(
            "users",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("email", sa.String(), nullable=False),
            sa.Column("password_hash", sa.String(), nullable=True),
            sa.Column("name", sa.String(), nullable=True),
            sa.Column("active_business_id", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_users_email", "users", ["email"], unique=True)
        op.create_index("ix_users_created_at", "users", ["created_at"])

    if "business_users" not in existing_tables:
        op.create_table(
            "business_users",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("business_id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("role", sa.String(), nullable=False, server_default="owner"),
        )
        op.create_index(
            "ix_business_users_business_id", "business_users", ["business_id"]
        )
        op.create_index("ix_business_users_user_id", "business_users", ["user_id"])

    if "customers" not in existing_tables:
        op.create_table(
            "customers",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("phone", sa.String(), nullable=False),
            sa.Column("email", sa.String(), nullable=True),
            sa.Column("address", sa.String(), nullable=True),
            sa.Column("business_id", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column(
                "sms_opt_out", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column("tags", sa.String(), nullable=True),
        )
        op.create_index("ix_customers_business_id", "customers", ["business_id"])
        op.create_index("ix_customers_phone", "customers", ["phone"])
        op.create_index("ix_customers_created_at", "customers", ["created_at"])

    if "appointments" not in existing_tables:
        op.create_table(
            "appointments",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("customer_id", sa.String(), nullable=False),
            sa.Column("start_time", sa.DateTime(), nullable=False),
            sa.Column("end_time", sa.DateTime(), nullable=False),
            sa.Column("service_type", sa.String(), nullable=True),
            sa.Column("description", sa.String(), nullable=True),
            sa.Column(
                "is_emergency", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column(
                "status", sa.String(), nullable=False, server_default="SCHEDULED"
            ),
            sa.Column("lead_source", sa.String(), nullable=True),
            sa.Column("estimated_value", sa.Integer(), nullable=True),
            sa.Column("job_stage", sa.String(), nullable=True),
            sa.Column("quoted_value", sa.Integer(), nullable=True),
            sa.Column("quote_status", sa.String(), nullable=True),
            sa.Column("business_id", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column(
                "reminder_sent", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column("calendar_event_id", sa.String(), nullable=True),
            sa.Column("tags", sa.String(), nullable=True),
            sa.Column("technician_id", sa.String(), nullable=True),
        )
        op.create_index("ix_appointments_customer_id", "appointments", ["customer_id"])
        op.create_index("ix_appointments_business_id", "appointments", ["business_id"])
        op.create_index("ix_appointments_start_time", "appointments", ["start_time"])
        op.create_index("ix_appointments_created_at", "appointments", ["created_at"])

    if "conversations" not in existing_tables:
        op.create_table(
            "conversations",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("channel", sa.String(), nullable=False),
            sa.Column("customer_id", sa.String(), nullable=True),
            sa.Column("session_id", sa.String(), nullable=True),
            sa.Column("business_id", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "ix_conversations_customer_id", "conversations", ["customer_id"]
        )
        op.create_index("ix_conversations_session_id", "conversations", ["session_id"])
        op.create_index(
            "ix_conversations_business_id", "conversations", ["business_id"]
        )
        op.create_index("ix_conversations_created_at", "conversations", ["created_at"])

    if "conversation_messages" not in existing_tables:
        op.create_table(
            "conversation_messages",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("conversation_id", sa.String(), nullable=False),
            sa.Column("role", sa.String(), nullable=False),
            sa.Column("text", sa.String(), nullable=False),
            sa.Column("timestamp", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "ix_conversation_messages_conversation_id",
            "conversation_messages",
            ["conversation_id"],
        )
        op.create_index(
            "ix_conversation_messages_timestamp", "conversation_messages", ["timestamp"]
        )

    if "retention_purge_logs" not in existing_tables:
        op.create_table(
            "retention_purge_logs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("actor_type", sa.String(), nullable=False),
            sa.Column("trigger", sa.String(), nullable=False),
            sa.Column(
                "appointments_deleted", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column(
                "conversations_deleted",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "conversation_messages_deleted",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )
        op.create_index(
            "ix_retention_logs_created_at", "retention_purge_logs", ["created_at"]
        )

    if "technicians" not in existing_tables:
        op.create_table(
            "technicians",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("business_id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("color", sa.String(), nullable=True),
            sa.Column(
                "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
            ),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_technicians_business_id", "technicians", ["business_id"])
        op.create_index("ix_technicians_created_at", "technicians", ["created_at"])

    if "audit_events" not in existing_tables:
        op.create_table(
            "audit_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("actor_type", sa.String(), nullable=False),
            sa.Column("business_id", sa.String(), nullable=True),
            sa.Column("path", sa.String(), nullable=False),
            sa.Column("method", sa.String(), nullable=False),
            sa.Column("status_code", sa.Integer(), nullable=False),
        )
        op.create_index("ix_audit_events_business_id", "audit_events", ["business_id"])
        op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])

    if "sms_audit" not in existing_tables:
        op.create_table(
            "sms_audit",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("business_id", sa.String(), nullable=True),
            sa.Column("phone", sa.String(), nullable=False),
            sa.Column("direction", sa.String(), nullable=False),
            sa.Column("message", sa.String(), nullable=True),
            sa.Column("event", sa.String(), nullable=False),
        )
        op.create_index("ix_sms_audit_created_at", "sms_audit", ["created_at"])
        op.create_index("ix_sms_audit_business_id", "sms_audit", ["business_id"])
        op.create_index("ix_sms_audit_phone", "sms_audit", ["phone"])


def downgrade() -> None:
    # Downgrade removes audit + core tables in reverse order (if present).
    op.drop_index("ix_sms_audit_phone", table_name="sms_audit")
    op.drop_index("ix_sms_audit_business_id", table_name="sms_audit")
    op.drop_index("ix_sms_audit_created_at", table_name="sms_audit")
    op.drop_table("sms_audit")

    op.drop_index("ix_audit_events_created_at", table_name="audit_events")
    op.drop_index("ix_audit_events_business_id", table_name="audit_events")
    op.drop_table("audit_events")

    op.drop_index("ix_technicians_created_at", table_name="technicians")
    op.drop_index("ix_technicians_business_id", table_name="technicians")
    op.drop_table("technicians")

    op.drop_index("ix_retention_logs_created_at", table_name="retention_purge_logs")
    op.drop_table("retention_purge_logs")

    op.drop_index(
        "ix_conversation_messages_timestamp", table_name="conversation_messages"
    )
    op.drop_index(
        "ix_conversation_messages_conversation_id", table_name="conversation_messages"
    )
    op.drop_table("conversation_messages")

    op.drop_index("ix_conversations_created_at", table_name="conversations")
    op.drop_index("ix_conversations_business_id", table_name="conversations")
    op.drop_index("ix_conversations_session_id", table_name="conversations")
    op.drop_index("ix_conversations_customer_id", table_name="conversations")
    op.drop_table("conversations")

    op.drop_index("ix_appointments_created_at", table_name="appointments")
    op.drop_index("ix_appointments_start_time", table_name="appointments")
    op.drop_index("ix_appointments_business_id", table_name="appointments")
    op.drop_index("ix_appointments_customer_id", table_name="appointments")
    op.drop_table("appointments")

    op.drop_index("ix_customers_created_at", table_name="customers")
    op.drop_index("ix_customers_phone", table_name="customers")
    op.drop_index("ix_customers_business_id", table_name="customers")
    op.drop_table("customers")

    op.drop_index("ix_business_users_user_id", table_name="business_users")
    op.drop_index("ix_business_users_business_id", table_name="business_users")
    op.drop_table("business_users")

    op.drop_index("ix_users_created_at", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")

    op.drop_index("ix_businesses_created_at", table_name="businesses")
    op.drop_index("ix_businesses_widget_token", table_name="businesses")
    op.drop_index("ix_businesses_api_key", table_name="businesses")
    op.drop_table("businesses")
