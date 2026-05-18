"""Add indexes on frequently-queried foreign key columns.

Most FK columns in PostgreSQL are not automatically indexed.
These indexes improve JOIN and WHERE performance on the most
commonly queried relationships.

Revision ID: 032
Revises: 031
Create Date: 2026-03-26
"""

from alembic import op
import contextlib

revision = "032"
down_revision = "031"
branch_labels = None
depends_on = None

# (index_name, table_name, column_list)
_INDEXES = [
    ("ix_daily_reports_project_id", "daily_reports", ["project_id"]),
    ("ix_meeting_minutes_project_id", "meeting_minutes", ["project_id"]),
    ("ix_change_orders_project_id", "change_orders", ["project_id"]),
    ("ix_project_members_project_id", "project_members", ["project_id"]),
    ("ix_project_members_user_id", "project_members", ["user_id"]),
    ("ix_audit_logs_user_id", "audit_logs", ["user_id"]),
    ("ix_audit_logs_org_id", "audit_logs", ["org_id"]),
    ("ix_submittals_project_id", "submittals", ["project_id"]),
    ("ix_drawings_project_id", "drawings", ["project_id"]),
    ("ix_drawing_sets_project_id", "drawing_sets", ["project_id"]),
    ("ix_pay_applications_project_id", "pay_applications", ["project_id"]),
    ("ix_potential_change_orders_project_id", "potential_change_orders", ["project_id"]),
    # cost_items has no project_id — it's global reference data
    ("ix_schedule_baselines_project_id", "schedule_baselines", ["project_id"]),
    ("ix_sync_logs_org_id", "sync_logs", ["org_id"]),
]


def upgrade() -> None:
    import sqlalchemy as sa

    for index_name, table_name, columns in _INDEXES:
        op.execute(
            sa.text(
                f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} ({', '.join(columns)})"
            )
        )


def downgrade() -> None:
    for index_name, table_name, _columns in reversed(_INDEXES):
        with contextlib.suppress(Exception):
            op.drop_index(index_name, table_name=table_name)
