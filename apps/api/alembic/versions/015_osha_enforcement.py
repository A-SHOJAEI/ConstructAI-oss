"""OSHA enforcement data tables.

Stores public OSHA inspection and violation records filtered to
construction (NAICS 23xx or SIC 1500-1799) for the last 5 years.
Used for vendor safety history lookup and compliance context.

Revision ID: 015
Revises: 014
Create Date: 2026-03-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "015"
down_revision: str = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---------------------------------------------------------------
    # osha_inspections
    # ---------------------------------------------------------------
    op.create_table(
        "osha_inspections",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("activity_nr", sa.Text, nullable=False),
        sa.Column("establishment_name", sa.Text, nullable=False),
        sa.Column("name_normalized", sa.Text, nullable=False),
        sa.Column("site_city", sa.Text, nullable=True),
        sa.Column("site_state", sa.Text, nullable=True),
        sa.Column("naics_code", sa.Text, nullable=True),
        sa.Column("sic_code", sa.Text, nullable=True),
        sa.Column("insp_type", sa.Text, nullable=True),
        sa.Column("open_date", sa.Date, nullable=True),
        sa.Column("close_date", sa.Date, nullable=True),
        sa.Column("total_penalty", sa.Numeric(14, 2), nullable=True),
        sa.Column("insp_scope", sa.Text, nullable=True),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_osha_insp_activity_nr", "osha_inspections", ["activity_nr"], unique=True)
    op.create_index("idx_osha_insp_naics_code", "osha_inspections", ["naics_code"])
    op.create_index("idx_osha_insp_site_state", "osha_inspections", ["site_state"])
    op.create_index("idx_osha_insp_open_date", "osha_inspections", ["open_date"])
    op.create_index("idx_osha_insp_name_normalized", "osha_inspections", ["name_normalized"])

    # ---------------------------------------------------------------
    # osha_violations
    # ---------------------------------------------------------------
    op.create_table(
        "osha_violations",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("activity_nr", sa.Text, nullable=False),
        sa.Column("citation_id", sa.Text, nullable=True),
        sa.Column("standard_cited", sa.Text, nullable=True),
        sa.Column("standard_parsed", sa.Text, nullable=True),
        sa.Column("violation_type", sa.Text, nullable=True),
        sa.Column("penalty", sa.Numeric(12, 2), nullable=True),
        sa.Column("abatement_date", sa.Date, nullable=True),
        sa.Column("issuance_date", sa.Date, nullable=True),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_osha_viol_activity_nr", "osha_violations", ["activity_nr"])
    op.create_index("idx_osha_viol_standard_cited", "osha_violations", ["standard_cited"])
    op.create_index("idx_osha_viol_standard_parsed", "osha_violations", ["standard_parsed"])
    op.create_index("idx_osha_viol_violation_type", "osha_violations", ["violation_type"])


def downgrade() -> None:
    op.drop_index("idx_osha_viol_violation_type")
    op.drop_index("idx_osha_viol_standard_parsed")
    op.drop_index("idx_osha_viol_standard_cited")
    op.drop_index("idx_osha_viol_activity_nr")
    op.drop_table("osha_violations")

    op.drop_index("idx_osha_insp_name_normalized")
    op.drop_index("idx_osha_insp_open_date")
    op.drop_index("idx_osha_insp_site_state")
    op.drop_index("idx_osha_insp_naics_code")
    op.drop_index("idx_osha_insp_activity_nr")
    op.drop_table("osha_inspections")
