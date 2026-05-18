"""Production hardening: RLS, tenant configs, feature flags, feedback, indexes

Revision ID: 007
Revises: 006
Create Date: 2026-02-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID, JSONB

revision: str = "007"
down_revision: str = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── current_tenant_id() SQL function ──────────────────────────────────
    op.execute(
        """
        CREATE OR REPLACE FUNCTION current_tenant_id()
        RETURNS uuid AS $$
            SELECT current_setting('app.current_tenant_id', TRUE)::uuid;
        $$ LANGUAGE sql STABLE;
        """
    )

    # ── Row-Level Security on projects ────────────────────────────────────
    op.execute("ALTER TABLE projects ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_projects ON projects
            USING (org_id = current_tenant_id());
        """
    )

    # ── Row-Level Security on users ───────────────────────────────────────
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_users ON users
            USING (org_id = current_tenant_id());
        """
    )

    # ── Tenant Configs ────────────────────────────────────────────────────
    op.create_table(
        "tenant_configs",
        sa.Column(
            "id",
            UUID,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "org_id",
            UUID,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            unique=True,
            nullable=False,
        ),
        sa.Column(
            "feature_flags",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "model_preferences",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "notification_settings",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "branding",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "billing_plan",
            sa.String,
            nullable=False,
            server_default="startup",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "billing_plan IN ('startup', 'growth', 'enterprise')",
            name="ck_tenant_configs_billing_plan",
        ),
    )

    # ── Usage Metrics (TimescaleDB hypertable) ────────────────────────────
    op.create_table(
        "usage_metrics",
        sa.Column(
            "id",
            UUID,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "org_id",
            UUID,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("metric_name", sa.Text, nullable=False),
        sa.Column(
            "metric_value",
            sa.Numeric(14, 4),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("dimensions", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "time",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "idx_usage_metrics_org_time",
        "usage_metrics",
        ["org_id", sa.text("time DESC")],
    )

    # Use SAVEPOINT so a failure doesn't poison the entire transaction
    op.execute("SAVEPOINT hypertable_usage_metrics")
    try:
        op.execute(
            "SELECT create_hypertable("
            "'usage_metrics', 'time', if_not_exists => TRUE, migrate_data => TRUE)"
        )
        op.execute("RELEASE SAVEPOINT hypertable_usage_metrics")
    except Exception:
        op.execute("ROLLBACK TO SAVEPOINT hypertable_usage_metrics")

    # ── Feature Flags ─────────────────────────────────────────────────────
    op.create_table(
        "feature_flags",
        sa.Column(
            "id",
            UUID,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "name",
            sa.String,
            unique=True,
            nullable=False,
        ),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "enabled",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "rollout_percentage",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "tenant_overrides",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "role_requirements",
            ARRAY(sa.String),
            nullable=False,
            server_default=sa.text("ARRAY[]::varchar[]"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "rollout_percentage >= 0 AND rollout_percentage <= 100",
            name="ck_feature_flags_rollout_pct",
        ),
    )

    # ── User Feedback ─────────────────────────────────────────────────────
    op.create_table(
        "user_feedback",
        sa.Column(
            "id",
            UUID,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            UUID,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("agent_name", sa.String, nullable=True),
        sa.Column("output_type", sa.String, nullable=True),
        sa.Column("rating", sa.Integer, nullable=True),
        sa.Column("feedback_text", sa.Text, nullable=True),
        sa.Column("agent_trace_id", sa.String, nullable=True),
        sa.Column(
            "browser_state",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "idx_user_feedback_agent_created",
        "user_feedback",
        ["agent_name", sa.text("created_at DESC")],
    )

    # ── Performance Indexes ───────────────────────────────────────────────
    op.execute("CREATE INDEX idx_evm_project_latest ON evm_snapshots (project_id, data_date DESC)")
    op.execute(
        "CREATE INDEX idx_alerts_project_recent ON safety_alerts (project_id, created_at DESC)"
    )
    op.execute("CREATE INDEX idx_docs_project_type ON documents (project_id, type)")
    # punch_list_items are stored as defect_reports with ai_classification source
    op.execute(
        "CREATE INDEX idx_defect_reports_open "
        "ON defect_reports (project_id) "
        "WHERE status IN ('open', 'in_progress')"
    )

    # ── Materialized View: project_health_summary ─────────────────────────
    op.execute(
        """
        CREATE MATERIALIZED VIEW project_health_summary AS
        SELECT
            p.id AS project_id,
            p.name AS project_name,
            p.org_id,
            (
                SELECT COUNT(*)
                FROM safety_alerts sa
                WHERE sa.project_id = p.id
                  AND sa.created_at > NOW() - INTERVAL '7 days'
            ) AS recent_safety_alerts,
            (
                SELECT COUNT(*)
                FROM documents d
                WHERE d.project_id = p.id
            ) AS total_documents,
            (
                SELECT e.cpi
                FROM evm_snapshots e
                WHERE e.project_id = p.id
                ORDER BY e.data_date DESC
                LIMIT 1
            ) AS latest_cpi,
            (
                SELECT e.spi
                FROM evm_snapshots e
                WHERE e.project_id = p.id
                ORDER BY e.data_date DESC
                LIMIT 1
            ) AS latest_spi,
            NOW() AS refreshed_at
        FROM projects p
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX idx_project_health_summary_pk ON project_health_summary (project_id)"
    )


def downgrade() -> None:
    # ── Drop materialized view ────────────────────────────────────────────
    op.execute("DROP MATERIALIZED VIEW IF EXISTS project_health_summary")

    # ── Drop performance indexes ──────────────────────────────────────────
    op.execute("DROP INDEX IF EXISTS idx_defect_reports_open")
    op.execute("DROP INDEX IF EXISTS idx_docs_project_type")
    op.execute("DROP INDEX IF EXISTS idx_alerts_project_recent")
    op.execute("DROP INDEX IF EXISTS idx_evm_project_latest")

    # ── Drop tables ───────────────────────────────────────────────────────
    op.drop_table("user_feedback")
    op.drop_table("feature_flags")
    op.drop_table("usage_metrics")
    op.drop_table("tenant_configs")

    # ── Drop RLS policies ─────────────────────────────────────────────────
    op.execute("DROP POLICY IF EXISTS tenant_isolation_users ON users")
    op.execute("ALTER TABLE users DISABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_projects ON projects")
    op.execute("ALTER TABLE projects DISABLE ROW LEVEL SECURITY")

    # ── Drop SQL function ─────────────────────────────────────────────────
    op.execute("DROP FUNCTION IF EXISTS current_tenant_id()")
