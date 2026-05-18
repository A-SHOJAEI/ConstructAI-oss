"""Orchestration, memory, guardrail, and evaluation tables for Phase 5

Revision ID: 006
Revises: 005
Create Date: 2026-02-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "006"
down_revision: str = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Project Facts ─────────────────────────────────────────────────────
    op.create_table(
        "project_facts",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fact_type", sa.Text, nullable=False),
        sa.Column("fact_text", sa.Text, nullable=False),
        sa.Column("source_type", sa.Text, nullable=False),
        sa.Column("source_id", sa.Text, nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), server_default=sa.text("1.0")),
        sa.Column(
            "valid_from",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
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
    )
    op.create_index("idx_project_facts_project", "project_facts", ["project_id"])
    op.create_index("idx_project_facts_type", "project_facts", ["fact_type"])

    # ── Learned Patterns ──────────────────────────────────────────────────
    op.create_table(
        "learned_patterns",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("pattern_type", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("parameters", JSONB, nullable=False, server_default="{}"),
        sa.Column("project_count", sa.Integer, server_default=sa.text("1")),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
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
    )

    # ── Guardrail Logs ────────────────────────────────────────────────────
    op.create_table(
        "guardrail_logs",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_name", sa.Text, nullable=False),
        sa.Column("stage", sa.Text, nullable=False),
        sa.Column("input_hash", sa.Text, nullable=True),
        sa.Column("passed", sa.Boolean, nullable=False),
        sa.Column("violations", JSONB, server_default="[]"),
        sa.Column("confidence_score", sa.Numeric(4, 3), nullable=True),
        sa.Column("routing_decision", sa.Text, nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("idx_guardrail_logs_agent_stage", "guardrail_logs", ["agent_name", "stage"])

    # ── Agent Evaluations ─────────────────────────────────────────────────
    op.create_table(
        "agent_evaluations",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_name", sa.Text, nullable=False),
        sa.Column("metric_name", sa.Text, nullable=False),
        sa.Column("metric_value", sa.Numeric(8, 4), nullable=False),
        sa.Column("benchmark_target", sa.Numeric(8, 4), nullable=True),
        sa.Column("dataset_name", sa.Text, nullable=True),
        sa.Column("dataset_size", sa.Integer, nullable=True),
        sa.Column(
            "evaluation_date", sa.Date, nullable=False, server_default=sa.text("CURRENT_DATE")
        ),
        sa.Column("details", JSONB, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "idx_agent_evaluations_agent_metric", "agent_evaluations", ["agent_name", "metric_name"]
    )

    # ── LLM Usage (TimescaleDB hypertable) ────────────────────────────────
    op.create_table(
        "llm_usage",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("agent_name", sa.Text, nullable=False),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("cached", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
    )
    op.create_index("idx_llm_usage_agent_time", "llm_usage", ["agent_name", sa.text("time DESC")])

    # Convert to TimescaleDB hypertable if extension is available
    # Use SAVEPOINT so a failure doesn't poison the entire transaction
    op.execute("SAVEPOINT hypertable_llm_usage")
    try:
        op.execute(
            "SELECT create_hypertable('llm_usage', 'time', "
            "if_not_exists => TRUE, migrate_data => TRUE)"
        )
        op.execute("RELEASE SAVEPOINT hypertable_llm_usage")
    except Exception:
        op.execute("ROLLBACK TO SAVEPOINT hypertable_llm_usage")

    # ── Workflow Executions ───────────────────────────────────────────────
    op.create_table(
        "workflow_executions",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workflow_type", sa.Text, nullable=False),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'running'")),
        sa.Column("current_step", sa.Text, nullable=True),
        sa.Column("steps_completed", JSONB, server_default="[]"),
        sa.Column("input_data", JSONB, nullable=False, server_default="{}"),
        sa.Column("output_data", JSONB, server_default="{}"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("langgraph_thread_id", sa.Text, nullable=True),
    )
    op.create_index("idx_workflow_executions_project", "workflow_executions", ["project_id"])
    op.create_index("idx_workflow_executions_status", "workflow_executions", ["status"])


def downgrade() -> None:
    op.drop_table("workflow_executions")
    op.drop_table("llm_usage")
    op.drop_table("agent_evaluations")
    op.drop_table("guardrail_logs")
    op.drop_table("learned_patterns")
    op.drop_table("project_facts")
