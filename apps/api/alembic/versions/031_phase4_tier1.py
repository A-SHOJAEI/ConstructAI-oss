"""Phase 4 Tier 1 feature tables: ambient field intelligence, plan takeoff,
instant pay, offline sync engine.

Adds 13 new tables for features 4.1, 4.2, 4.3, and 4.4.

Revision ID: 031
Revises: 030
Create Date: 2026-03-15
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "031"
down_revision = "030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ==================================================================
    # Feature 4.1: Ambient Field Intelligence
    # ==================================================================

    # ------------------------------------------------------------------
    # 1. field_pings — GPS location pings from worker devices
    # ------------------------------------------------------------------
    op.create_table(
        "field_pings",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("worker_id", sa.Text(), nullable=False),
        sa.Column("latitude", sa.Numeric(10, 7), nullable=False),
        sa.Column("longitude", sa.Numeric(10, 7), nullable=False),
        sa.Column("accuracy_m", sa.Numeric(6, 1), nullable=True),
        sa.Column("altitude_m", sa.Numeric(8, 1), nullable=True),
        sa.Column("trade", sa.Text(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_field_pings_project_ts", "field_pings", ["project_id", "timestamp"])
    op.create_index("ix_field_pings_worker_ts", "field_pings", ["worker_id", "timestamp"])

    # ------------------------------------------------------------------
    # 2. ambient_equipment_telemetry — IoT data from heavy equipment
    # ------------------------------------------------------------------
    op.create_table(
        "ambient_equipment_telemetry",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("equipment_id", sa.Text(), nullable=False),
        sa.Column("equipment_type", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="idle"),
        sa.Column("fuel_level_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("engine_hours", sa.Numeric(10, 2), nullable=True),
        sa.Column("latitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("longitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("raw_payload", JSONB, nullable=False, server_default="{}"),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_ambient_equip_telem_project_ts",
        "ambient_equipment_telemetry",
        ["project_id", "timestamp"],
    )
    op.create_index(
        "ix_ambient_equip_telem_equip_ts",
        "ambient_equipment_telemetry",
        ["equipment_id", "timestamp"],
    )

    # ------------------------------------------------------------------
    # 3. badge_events — Check-in/out events from site gates
    # ------------------------------------------------------------------
    op.create_table(
        "badge_events",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("worker_id", sa.Text(), nullable=False),
        sa.Column("worker_name", sa.Text(), nullable=True),
        sa.Column("trade", sa.Text(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("gate_id", sa.Text(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_badge_events_project_ts",
        "badge_events",
        ["project_id", "timestamp"],
    )
    op.create_index(
        "ix_badge_events_worker_ts",
        "badge_events",
        ["worker_id", "timestamp"],
    )

    # ------------------------------------------------------------------
    # 4. ambient_daily_snapshots — Aggregated daily data
    # ------------------------------------------------------------------
    op.create_table(
        "ambient_daily_snapshots",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("workforce_summary", JSONB, nullable=False, server_default="{}"),
        sa.Column("equipment_summary", JSONB, nullable=False, server_default="{}"),
        sa.Column("site_activity", JSONB, nullable=False, server_default="{}"),
        sa.Column("zone_activity", JSONB, nullable=False, server_default="[]"),
        sa.Column("data_quality", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint(
            "project_id",
            "snapshot_date",
            name="uq_ambient_snapshot_project_date",
        ),
    )

    # ==================================================================
    # Feature 4.2: Plan Takeoff (tables created by other agent)
    # ==================================================================

    # ------------------------------------------------------------------
    # 5. plan_takeoffs — Takeoff sessions
    # ------------------------------------------------------------------
    op.create_table(
        "plan_takeoffs",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("drawing_id", UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("scale_factor", sa.Numeric(10, 4), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("metadata_", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_plan_takeoffs_project", "plan_takeoffs", ["project_id"])

    # ------------------------------------------------------------------
    # 6. takeoff_line_items — Individual takeoff measurements
    # ------------------------------------------------------------------
    op.create_table(
        "takeoff_line_items",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "takeoff_id",
            UUID(as_uuid=True),
            sa.ForeignKey("plan_takeoffs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("csi_code", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("measurement_type", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Numeric(14, 4), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column("unit_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("total_cost", sa.Numeric(14, 2), nullable=True),
        sa.Column("geometry", JSONB, nullable=False, server_default="{}"),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_takeoff_line_items_takeoff",
        "takeoff_line_items",
        ["takeoff_id"],
    )

    # ==================================================================
    # Feature 4.3: Instant Pay (tables created by other agent)
    # ==================================================================

    # ------------------------------------------------------------------
    # 7. payment_integration_configs — Payment provider configs
    # ------------------------------------------------------------------
    op.create_table(
        "payment_integration_configs",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "org_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("credentials_encrypted", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("config", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_payment_configs_org", "payment_integration_configs", ["org_id"])

    # ------------------------------------------------------------------
    # 8. payment_transactions — Individual payment records
    # ------------------------------------------------------------------
    op.create_table(
        "payment_transactions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pay_application_id",
            UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("payee_name", sa.Text(), nullable=False),
        sa.Column("payee_entity_id", UUID(as_uuid=True), nullable=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False, server_default="USD"),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("provider_transaction_id", sa.Text(), nullable=True),
        sa.Column("metadata_", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "initiated_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_payment_transactions_project_status",
        "payment_transactions",
        ["project_id", "status"],
    )

    # ------------------------------------------------------------------
    # 9. lien_waiver_packages — Lien waiver tracking for payments
    # ------------------------------------------------------------------
    op.create_table(
        "lien_waiver_packages",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "payment_transaction_id",
            UUID(as_uuid=True),
            sa.ForeignKey("payment_transactions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "waiver_type",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("document_url", sa.Text(), nullable=True),
        sa.Column("signed_by", sa.Text(), nullable=True),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("through_date", sa.Date(), nullable=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_lien_waiver_packages_payment",
        "lien_waiver_packages",
        ["payment_transaction_id"],
    )

    # ==================================================================
    # Feature 4.4: Offline-First Mobile Sync Engine
    # ==================================================================

    # ------------------------------------------------------------------
    # 10. device_sync_states — Per-device sync tracking
    # ------------------------------------------------------------------
    op.create_table(
        "device_sync_states",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("device_id", sa.Text(), nullable=False),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("last_push_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_pull_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_server_timestamp",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("device_info", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint(
            "device_id",
            "project_id",
            name="uq_device_sync_device_project",
        ),
    )
    op.create_index(
        "ix_device_sync_states_project",
        "device_sync_states",
        ["project_id"],
    )

    # ------------------------------------------------------------------
    # 11. conflict_logs — Sync conflict audit trail
    # ------------------------------------------------------------------
    op.create_table(
        "conflict_logs",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("device_id", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("client_data", JSONB, nullable=False, server_default="{}"),
        sa.Column("server_data", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "client_timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "server_timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("resolution", sa.Text(), nullable=False),
        sa.Column(
            "resolved_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "is_resolved",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_conflict_logs_project_device",
        "conflict_logs",
        ["project_id", "device_id"],
    )
    op.create_index(
        "ix_conflict_logs_entity",
        "conflict_logs",
        ["entity_type", "entity_id"],
    )

    # ------------------------------------------------------------------
    # 12. offline_queue_items — Pending server-side processing
    # ------------------------------------------------------------------
    op.create_table(
        "offline_queue_items",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("device_id", sa.Text(), nullable=False),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("payload", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "client_timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "retry_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_offline_queue_project_status",
        "offline_queue_items",
        ["project_id", "status"],
    )

    # ------------------------------------------------------------------
    # 13. photo_upload_queue — Deferred photo uploads
    # ------------------------------------------------------------------
    op.create_table(
        "photo_upload_queue",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("device_id", sa.Text(), nullable=False),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_type", sa.Text(), nullable=True),
        sa.Column("entity_id", sa.Text(), nullable=True),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column(
            "content_type",
            sa.Text(),
            nullable=False,
            server_default="image/jpeg",
        ),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("s3_key", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "client_timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_photo_upload_queue_project_status",
        "photo_upload_queue",
        ["project_id", "status"],
    )


def downgrade() -> None:
    # Drop in reverse order to respect FK constraints
    op.drop_table("photo_upload_queue")
    op.drop_table("offline_queue_items")
    op.drop_table("conflict_logs")
    op.drop_table("device_sync_states")
    op.drop_table("lien_waiver_packages")
    op.drop_table("payment_transactions")
    op.drop_table("payment_integration_configs")
    op.drop_table("takeoff_line_items")
    op.drop_table("plan_takeoffs")
    op.drop_table("ambient_daily_snapshots")
    op.drop_table("badge_events")
    op.drop_table("ambient_equipment_telemetry")
    op.drop_table("field_pings")
