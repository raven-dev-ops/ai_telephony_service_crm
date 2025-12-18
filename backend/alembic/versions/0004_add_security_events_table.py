"""Add security_events table for security-relevant audit signals."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "0004_add_security_events_table"
down_revision = "0003_align_core_schema"
branch_labels = None
depends_on = None


def _table_names(inspector: sa.Inspector) -> set[str]:
    return set(inspector.get_table_names())


def _index_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    try:
        return {idx["name"] for idx in inspector.get_indexes(table_name)}
    except sa.exc.NoSuchTableError:
        return set()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if "security_events" not in _table_names(inspector):
        op.create_table(
            "security_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("event_type", sa.String(), nullable=False),
            sa.Column(
                "severity",
                sa.String(),
                nullable=False,
                server_default=sa.text("'warning'"),
            ),
            sa.Column("actor_type", sa.String(), nullable=False),
            sa.Column("business_id", sa.String(), nullable=True),
            sa.Column("path", sa.String(), nullable=False),
            sa.Column("method", sa.String(), nullable=False),
            sa.Column("status_code", sa.Integer(), nullable=False),
            sa.Column("ip_hash", sa.String(), nullable=True),
            sa.Column("user_agent_hash", sa.String(), nullable=True),
            sa.Column("request_id", sa.String(), nullable=True),
            sa.Column("meta", sa.Text(), nullable=True),
        )

    indexes = _index_names(inspector, "security_events")
    index_specs: list[tuple[str, list[str]]] = [
        ("ix_security_events_created_at", ["created_at"]),
        ("ix_security_events_event_type", ["event_type"]),
        ("ix_security_events_business_id", ["business_id"]),
        ("ix_security_events_ip_hash", ["ip_hash"]),
        ("ix_security_events_request_id", ["request_id"]),
    ]
    for name, cols in index_specs:
        if name not in indexes:
            op.create_index(name, "security_events", cols)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if "security_events" not in _table_names(inspector):
        return

    indexes = _index_names(inspector, "security_events")
    for name in [
        "ix_security_events_request_id",
        "ix_security_events_ip_hash",
        "ix_security_events_business_id",
        "ix_security_events_event_type",
        "ix_security_events_created_at",
    ]:
        if name in indexes:
            op.drop_index(name, table_name="security_events")
    op.drop_table("security_events")
