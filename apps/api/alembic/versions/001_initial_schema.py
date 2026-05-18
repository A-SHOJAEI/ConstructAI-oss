"""Initial schema with orgs, users, projects

Revision ID: 001
Revises:
Create Date: 2026-02-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Required PostgreSQL extensions
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")  # for gen_random_uuid()
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")  # pgvector for embeddings
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")  # GIN trigram indexes
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")  # hypertables, continuous aggs

    # Organizations
    op.create_table(
        "organizations",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("slug", sa.Text, unique=True, nullable=False),
        sa.Column(
            "type",
            sa.Text,
            nullable=False,
            server_default="gc",
        ),
        sa.Column("subscription_tier", sa.Text, nullable=False, server_default="startup"),
        sa.Column("settings", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "type IN ('owner', 'gc', 'subcontractor', 'architect', 'engineer')",
            name="ck_org_type",
        ),
        sa.CheckConstraint(
            "subscription_tier IN ('startup', 'growth', 'enterprise')",
            name="ck_org_tier",
        ),
    )

    # Users
    op.create_table(
        "users",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "org_id", UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("email", sa.Text, unique=True, nullable=False),
        sa.Column("hashed_password", sa.Text, nullable=False),
        sa.Column("full_name", sa.Text, nullable=False),
        sa.Column("role", sa.Text, nullable=False, server_default="read_only"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("settings", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "role IN ('platform_admin', 'owner_developer', 'general_contractor', "
            "'project_manager', 'architect_engineer', 'subcontractor', "
            "'inspector', 'safety_manager', 'read_only')",
            name="ck_user_role",
        ),
    )
    op.create_index("idx_users_org", "users", ["org_id"])
    op.create_index("idx_users_email", "users", ["email"])

    # Projects
    op.create_table(
        "projects",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "org_id", UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("project_number", sa.Text, nullable=True),
        sa.Column("type", sa.Text, nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default="preconstruction"),
        sa.Column("address", sa.Text, nullable=True),
        sa.Column("contract_value", sa.Numeric(14, 2), nullable=True),
        sa.Column("start_date", sa.Date, nullable=True),
        sa.Column("end_date", sa.Date, nullable=True),
        sa.Column("settings", JSONB, nullable=False, server_default="{}"),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "type IN ('commercial', 'residential', 'infrastructure', 'industrial')",
            name="ck_project_type",
        ),
        sa.CheckConstraint(
            "status IN ('preconstruction', 'active', 'closeout', 'archived')",
            name="ck_project_status",
        ),
    )
    op.create_index("idx_projects_org", "projects", ["org_id"])
    op.create_index("idx_projects_status", "projects", ["status"])

    op.execute("ALTER TABLE projects ADD COLUMN location GEOGRAPHY(POINT, 4326)")
    op.create_index("idx_projects_location", "projects", ["location"], postgresql_using="gist")

    # Project Members
    op.create_table(
        "project_members",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id", UUID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.Text, nullable=False, server_default="member"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.UniqueConstraint("project_id", "user_id", name="uq_project_member"),
    )
    op.create_index("idx_pm_project", "project_members", ["project_id"])
    op.create_index("idx_pm_user", "project_members", ["user_id"])

    op.execute("ALTER TABLE projects ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("project_members")
    op.drop_table("projects")
    op.drop_table("users")
    op.drop_table("organizations")
