"""Add unique constraint on EVMSnapshot (project_id, snapshot_date)."""

from alembic import op
from sqlalchemy.exc import IntegrityError, ProgrammingError

revision = "035"
down_revision = "034"


def upgrade():
    # M-13: Narrow the exception surface. Previously `except Exception: pass`
    # masked permission errors, deadlocks, disk-full, everything. Now only
    # the specific "constraint already exists" error is tolerated.
    try:
        op.create_unique_constraint(
            "uq_evm_snapshots_project_date",
            "evm_snapshots",
            ["project_id", "snapshot_date"],
        )
    except (IntegrityError, ProgrammingError) as exc:
        msg = str(exc).lower()
        if "already exists" in msg or "duplicate" in msg:
            return
        raise


def downgrade():
    # Idempotent drop: tolerate only "does not exist" errors, re-raise others.
    try:
        op.drop_constraint("uq_evm_snapshots_project_date", "evm_snapshots")
    except (IntegrityError, ProgrammingError) as exc:
        msg = str(exc).lower()
        if "does not exist" not in msg:
            raise
