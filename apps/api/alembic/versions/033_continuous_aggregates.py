"""Create TimescaleDB continuous aggregates for safety alerts and EVM snapshots.

Revision ID: 033
Revises: 032
Create Date: 2026-03-27
"""

from alembic import op

revision = "033"
down_revision = "032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create continuous aggregates only if tables are hypertables.
    # In dev, tables may not be hypertables — fall back to regular materialized views.
    op.execute("""
    DO $$
    DECLARE
        _is_hypertable BOOLEAN;
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
            RETURN;
        END IF;

        -- Safety alerts hourly aggregate
        IF NOT EXISTS (SELECT 1 FROM pg_matviews WHERE matviewname = 'safety_alerts_hourly') THEN
            SELECT EXISTS(
                SELECT 1 FROM timescaledb_information.hypertables
                WHERE hypertable_name = 'safety_alerts'
            ) INTO _is_hypertable;

            IF _is_hypertable THEN
                EXECUTE $agg$
                CREATE MATERIALIZED VIEW safety_alerts_hourly
                WITH (timescaledb.continuous) AS
                SELECT
                    time_bucket('1 hour', created_at) AS bucket,
                    project_id,
                    alert_type,
                    COUNT(*) as alert_count,
                    AVG(confidence) as avg_confidence
                FROM safety_alerts
                GROUP BY bucket, project_id, alert_type
                WITH NO DATA
                $agg$;

                PERFORM add_continuous_aggregate_policy('safety_alerts_hourly',
                    start_offset => INTERVAL '3 days',
                    end_offset => INTERVAL '1 hour',
                    schedule_interval => INTERVAL '1 hour');
            ELSE
                RAISE NOTICE 'safety_alerts is not a hypertable — skipping continuous aggregate';
            END IF;
        END IF;

        -- EVM snapshots daily aggregate
        IF NOT EXISTS (SELECT 1 FROM pg_matviews WHERE matviewname = 'evm_snapshots_daily') THEN
            SELECT EXISTS(
                SELECT 1 FROM timescaledb_information.hypertables
                WHERE hypertable_name = 'evm_snapshots'
            ) INTO _is_hypertable;

            IF _is_hypertable THEN
                EXECUTE $agg$
                CREATE MATERIALIZED VIEW evm_snapshots_daily
                WITH (timescaledb.continuous) AS
                SELECT
                    time_bucket('1 day', snapshot_date) AS bucket,
                    project_id,
                    AVG(cpi) as avg_cpi,
                    AVG(spi) as avg_spi,
                    MAX(eac) as latest_eac
                FROM evm_snapshots
                GROUP BY bucket, project_id
                WITH NO DATA
                $agg$;

                PERFORM add_continuous_aggregate_policy('evm_snapshots_daily',
                    start_offset => INTERVAL '30 days',
                    end_offset => INTERVAL '1 day',
                    schedule_interval => INTERVAL '1 day');
            ELSE
                RAISE NOTICE 'evm_snapshots is not a hypertable — skipping continuous aggregate';
            END IF;
        END IF;
    END
    $$;
    """)


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS evm_snapshots_daily CASCADE")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS safety_alerts_hourly CASCADE")
