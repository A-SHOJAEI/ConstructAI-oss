"""RBAC enforcement, MFA, DB-backed audit logging, email verification.

Adds:
- users: mfa_secret, mfa_enabled, mfa_backup_codes, mfa_enforced_at
- audit_logs table with indexes
- project_members: invited_by column, updated role default
- Data migration: map old role names to new 9-role system

Revision ID: 027
Revises: 026
Create Date: 2026-03-08
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "027"
down_revision = "026"
branch_labels = None
depends_on = None

# Old role name → new role name
_ROLE_MIGRATION = {
    "platform_admin": "org_admin",
    "owner_developer": "owner_rep",
    "general_contractor": "project_admin",
    "architect_engineer": "field_engineer",
    "inspector": "field_engineer",
    "read_only": "readonly",
    "member": "field_engineer",
    # These stay the same:
    # project_manager, subcontractor, safety_manager
}


def upgrade() -> None:
    # 1a. Users — MFA columns
    op.add_column("users", sa.Column("mfa_secret", sa.Text(), nullable=True))
    op.add_column(
        "users",
        sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("users", sa.Column("mfa_backup_codes", JSONB(), nullable=True))
    op.add_column("users", sa.Column("mfa_enforced_at", sa.DateTime(timezone=True), nullable=True))

    # 1b. Audit logs table
    op.create_table(
        "audit_logs",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "org_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("resource_type", sa.Text(), nullable=True),
        sa.Column("resource_id", UUID(as_uuid=True), nullable=True),
        sa.Column("ip_address", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("details", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_audit_logs_timestamp", "audit_logs", ["timestamp"])
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_org_id", "audit_logs", ["org_id"])

    # 1c. Project members — invited_by + update default role
    op.add_column(
        "project_members",
        sa.Column(
            "invited_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.alter_column(
        "project_members",
        "role",
        server_default="field_engineer",
    )

    # 1d. Drop old role CHECK constraint, migrate data, add new constraint
    op.execute(sa.text("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_user_role"))

    for old_role, new_role in _ROLE_MIGRATION.items():
        op.execute(
            sa.text("UPDATE users SET role = :new_role WHERE role = :old_role").bindparams(
                new_role=new_role, old_role=old_role
            )
        )
        op.execute(
            sa.text(
                "UPDATE project_members SET role = :new_role WHERE role = :old_role"
            ).bindparams(new_role=new_role, old_role=old_role)
        )

    # Add new CHECK constraint with updated role names
    op.execute(
        sa.text(
            "ALTER TABLE users ADD CONSTRAINT ck_user_role "
            "CHECK (role IN ('org_admin','owner_rep','project_admin',"
            "'project_manager','field_engineer','subcontractor',"
            "'safety_manager','readonly'))"
        )
    )


def downgrade() -> None:
    # Reverse role migration
    reverse = {v: k for k, v in _ROLE_MIGRATION.items()}
    # Handle field_engineer → project_manager (best approximation for multiple sources)
    reverse["field_engineer"] = "architect_engineer"
    for new_role, old_role in reverse.items():
        op.execute(
            sa.text("UPDATE users SET role = :old_role WHERE role = :new_role").bindparams(
                old_role=old_role, new_role=new_role
            )
        )
        op.execute(
            sa.text(
                "UPDATE project_members SET role = :old_role WHERE role = :new_role"
            ).bindparams(old_role=old_role, new_role=new_role)
        )

    op.alter_column("project_members", "role", server_default="member")
    op.drop_column("project_members", "invited_by")

    op.drop_index("ix_audit_logs_org_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_action", table_name="audit_logs")
    op.drop_index("ix_audit_logs_user_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_timestamp", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_column("users", "mfa_enforced_at")
    op.drop_column("users", "mfa_backup_codes")
    op.drop_column("users", "mfa_enabled")
    op.drop_column("users", "mfa_secret")
