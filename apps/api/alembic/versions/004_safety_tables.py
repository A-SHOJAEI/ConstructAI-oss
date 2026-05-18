"""Safety monitoring tables for Phase 3 Vision

Revision ID: 004
Revises: 003
Create Date: 2026-02-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "004"
down_revision: str = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Cameras ──────────────────────────────────────────────────────────
    op.create_table(
        "cameras",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("stream_url", sa.Text, nullable=False),
        sa.Column("location_description", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("fps_setting", sa.Integer, nullable=False, server_default="5"),
        sa.Column("resolution", sa.Text, nullable=False, server_default="1080p"),
        sa.Column("config", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_cameras_project", "cameras", ["project_id"])

    # ── Safety Zones ─────────────────────────────────────────────────────
    op.create_table(
        "safety_zones",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "camera_id",
            UUID,
            sa.ForeignKey("cameras.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("zone_type", sa.Text, nullable=False),
        sa.Column("polygon_points", JSONB, nullable=False),
        sa.Column("ppe_requirements", JSONB, nullable=False, server_default="[]"),
        sa.Column("severity_override", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("schedule_active", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "zone_type IN ('restricted', 'ppe_required', 'equipment_only', "
            "'pedestrian_only', 'crane_swing', 'excavation', 'general')",
            name="ck_zone_type",
        ),
    )
    op.create_index("idx_safety_zones_camera", "safety_zones", ["camera_id"])
    op.create_index("idx_safety_zones_project", "safety_zones", ["project_id"])

    # ── Safety Alerts ────────────────────────────────────────────────────
    op.create_table(
        "safety_alerts",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("camera_id", UUID, sa.ForeignKey("cameras.id"), nullable=True),
        sa.Column("zone_id", UUID, sa.ForeignKey("safety_zones.id"), nullable=True),
        sa.Column("priority", sa.Text, nullable=False),
        sa.Column("alert_type", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("detections", JSONB, nullable=False, server_default="[]"),
        sa.Column("frame_s3_key", sa.Text, nullable=True),
        sa.Column("video_clip_s3_key", sa.Text, nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("is_acknowledged", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("is_false_positive", sa.Boolean, nullable=True),
        sa.Column("acknowledged_by", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("response_notes", sa.Text, nullable=True),
        sa.Column("osha_reference", sa.Text, nullable=True),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "priority IN ('P1_critical', 'P2_high', 'P3_medium', 'P4_low', 'P5_info')",
            name="ck_alert_priority",
        ),
        sa.CheckConstraint(
            "alert_type IN ('ppe_violation', 'zone_breach', 'fall_detected', 'near_miss', "
            "'unsafe_posture', 'equipment_proximity', 'unauthorized_access', 'other')",
            name="ck_alert_type",
        ),
    )
    op.create_index("idx_safety_alerts_project", "safety_alerts", ["project_id"])
    op.create_index("idx_safety_alerts_priority", "safety_alerts", ["priority"])
    op.create_index("idx_safety_alerts_created_at", "safety_alerts", [sa.text("created_at DESC")])
    op.create_index("idx_safety_alerts_camera", "safety_alerts", ["camera_id"])
    op.create_index("idx_safety_alerts_alert_type", "safety_alerts", ["alert_type"])

    # ── Alert Dedup Cache ────────────────────────────────────────────────
    op.create_table(
        "alert_dedup_cache",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("cache_key", sa.Text, nullable=False, unique=True),
        sa.Column("alert_id", UUID, sa.ForeignKey("safety_alerts.id"), nullable=True),
        sa.Column("last_seen", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("count", sa.Integer, nullable=False, server_default="1"),
    )
    op.create_index("idx_alert_dedup_cache_key", "alert_dedup_cache", ["cache_key"])
    op.create_index("idx_alert_dedup_last_seen", "alert_dedup_cache", ["last_seen"])

    # ── Detection Stats (TimescaleDB hypertable) ─────────────────────────
    op.create_table(
        "detection_stats",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("camera_id", UUID, nullable=False),
        sa.Column("total_persons", sa.Integer, nullable=False, server_default="0"),
        sa.Column("persons_with_hardhat", sa.Integer, nullable=False, server_default="0"),
        sa.Column("persons_with_vest", sa.Integer, nullable=False, server_default="0"),
        sa.Column("persons_without_ppe", sa.Integer, nullable=False, server_default="0"),
        sa.Column("equipment_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("zone_violations", sa.Integer, nullable=False, server_default="0"),
        sa.Column("inference_fps", sa.Numeric(6, 2), nullable=True),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
    )
    op.create_index(
        "idx_detection_stats_project_camera_time",
        "detection_stats",
        ["project_id", "camera_id", sa.text("time DESC")],
    )

    # Convert to TimescaleDB hypertable if extension is available
    # Use SAVEPOINT so a failure doesn't poison the entire transaction
    op.execute("SAVEPOINT hypertable_detection_stats")
    try:
        op.execute(
            "SELECT create_hypertable('detection_stats', 'time', "
            "if_not_exists => TRUE, migrate_data => TRUE)"
        )
        op.execute("RELEASE SAVEPOINT hypertable_detection_stats")
    except Exception:
        op.execute("ROLLBACK TO SAVEPOINT hypertable_detection_stats")

    # NOTE: daily_safety_summary materialized view is skipped here.
    # It requires TimescaleDB continuous aggregates and should be created
    # via a separate migration or manually in production environments.


def downgrade() -> None:
    op.drop_table("detection_stats")
    op.drop_table("alert_dedup_cache")
    op.drop_table("safety_alerts")
    op.drop_table("safety_zones")
    op.drop_table("cameras")
