"""Add per-tenant Twilio phone number."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "0002_add_twilio_phone_number"
down_revision = "0001_sms_audit_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("businesses")}
    if "twilio_phone_number" not in columns:
        op.add_column(
            "businesses",
            sa.Column("twilio_phone_number", sa.String(length=255), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("businesses")}
    if "twilio_phone_number" in columns:
        op.drop_column("businesses", "twilio_phone_number")
