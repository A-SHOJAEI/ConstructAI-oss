"""
Create 12 months of EVM snapshots showing a realistic project trajectory:

Months 1-4:  Slightly ahead of schedule, on budget (SPI ~1.02, CPI ~1.01)
Months 5-6:  Foundation issues cause delay (SPI drops to 0.95, CPI drops to 0.96)
Months 7-8:  Recovery attempt, partial success (SPI 0.90, CPI 0.93)
Months 9-10: Change order impacts accumulate (SPI 0.88, CPI 0.91)

Creates a compelling S-curve with diverging PV/EV/AC lines.
"""
from datetime import date, timedelta
from decimal import Decimal

from app.database import async_session
from app.models import EVMSnapshot

BAC = Decimal("45000000.00")

# (month, pv_cumulative_pct, ev_cumulative_pct, ac_cumulative_pct)
MONTHLY_DATA = [
    (1,  0.04,  0.042, 0.041),
    (2,  0.09,  0.093, 0.090),
    (3,  0.15,  0.154, 0.150),
    (4,  0.22,  0.225, 0.218),
    (5,  0.30,  0.290, 0.305),
    (6,  0.38,  0.360, 0.385),
    (7,  0.46,  0.425, 0.460),
    (8,  0.53,  0.490, 0.530),
    (9,  0.60,  0.540, 0.590),
    (10, 0.66,  0.580, 0.640),
]


async def seed_evm(ctx: dict) -> dict:
    project_start = date(2025, 5, 1)

    async with async_session() as db:
        for month, pv_pct, ev_pct, ac_pct in MONTHLY_DATA:
            data_date = project_start + timedelta(days=30 * month)
            pv = BAC * Decimal(str(pv_pct))
            ev = BAC * Decimal(str(ev_pct))
            ac = BAC * Decimal(str(ac_pct))
            sv = ev - pv
            cv = ev - ac
            spi = ev / pv if pv > 0 else Decimal("1.0")
            cpi = ev / ac if ac > 0 else Decimal("1.0")
            eac_cpi = BAC / cpi if cpi > 0 else BAC
            etc_cpi = eac_cpi - ac
            vac = BAC - eac_cpi
            tcpi = (BAC - ev) / (BAC - ac) if (BAC - ac) > 0 else Decimal("1.0")
            pct_complete = ev / BAC * 100

            snapshot = EVMSnapshot(
                project_id=ctx["project_id"],
                snapshot_date=data_date,
                data_date=data_date,
                bac=BAC,
                pv=pv,
                ev=ev,
                ac=ac,
                sv=sv,
                cv=cv,
                spi=round(spi, 4),
                cpi=round(cpi, 4),
                eac=round(eac_cpi, 2),
                etc=round(etc_cpi, 2),
                vac=round(vac, 2),
                tcpi=round(tcpi, 4),
                percent_complete=round(pct_complete, 2),
                metadata={"seeded": True, "month": month},
            )
            db.add(snapshot)

        await db.commit()

    return {}
