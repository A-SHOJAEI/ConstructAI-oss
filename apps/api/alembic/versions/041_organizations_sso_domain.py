"""Sync ORM columns missing from the alembic chain.

Revision ID: 041
Revises: 040
Create Date: 2026-04-25

Several columns declared on the SQLAlchemy models were never added in a
migration, so production-equivalent test runs (which use ``alembic upgrade
head`` rather than ``Base.metadata.create_all``) hit
``UndefinedColumnError`` on every query that touches them. Add the missing
columns to bring the schema in sync with the ORM.

Columns added:

- organizations.sso_domain (used by SSO email-domain org lookup)
- users.email_verified (auth gate before sensitive operations)
- users.token_version (forces JWT invalidation after password reset)
- users.mfa_pending (mfa secret stored but not yet verified by user)
- users.mfa_backup_salt (per-user salt for backup-code hashing)
"""

import sqlalchemy as sa
from alembic import op

revision = "041"
down_revision = "040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # organizations.sso_domain
    op.add_column(
        "organizations",
        sa.Column("sso_domain", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_organizations_sso_domain",
        "organizations",
        ["sso_domain"],
        unique=True,
    )

    # users: email_verified, token_version, mfa_pending, mfa_backup_salt
    op.add_column(
        "users",
        sa.Column(
            "email_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "token_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "mfa_pending",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "users",
        sa.Column("mfa_backup_salt", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "mfa_backup_salt")
    op.drop_column("users", "mfa_pending")
    op.drop_column("users", "token_version")
    op.drop_column("users", "email_verified")
    op.drop_index("ix_organizations_sso_domain", table_name="organizations")
    op.drop_column("organizations", "sso_domain")
