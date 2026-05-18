"""
Master demo seeder. Creates a complete, realistic construction project
with 12 months of history for showcasing all ConstructAI capabilities.

Usage:
    cd apps/api && python -m demo.seed.seed_all

The demo project: "Riverside Mixed-Use Development"
- $45M commercial mixed-use (retail + office + residential)
- 18-month schedule, currently at month 10
- Trending slightly over budget (CPI = 0.91) and behind schedule (SPI = 0.88)
- Active change orders, safety incidents, quality issues
- 2 organizations: owner (Riverside Properties LLC) and GC (BuildRight Construction)
"""
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

from demo.seed.seed_tenants import seed_tenants
from demo.seed.seed_project import seed_project
from demo.seed.seed_documents import seed_documents
from demo.seed.seed_schedule import seed_schedule
from demo.seed.seed_evm import seed_evm
from demo.seed.seed_safety import seed_safety
from demo.seed.seed_quality import seed_quality
from demo.seed.seed_productivity import seed_productivity
from demo.seed.seed_communication import seed_communication
from demo.seed.seed_change_orders import seed_change_orders
from demo.seed.seed_memory import seed_memory
from demo.seed.seed_workflows import seed_workflows


async def seed_all():
    print("=" * 60)
    print("  ConstructAI Demo Seeder")
    print("  Project: Riverside Mixed-Use Development")
    print("=" * 60)
    print()

    ctx = {}  # Shared context (IDs) between seeders

    steps = [
        ("Organizations & Users", seed_tenants),
        ("Project", seed_project),
        ("Documents & Specs", seed_documents),
        ("Schedule Activities", seed_schedule),
        ("EVM History (12 months)", seed_evm),
        ("Safety Alerts & Incidents", seed_safety),
        ("Quality Inspections & Defects", seed_quality),
        ("Productivity & Equipment", seed_productivity),
        ("Daily Reports & Communications", seed_communication),
        ("Change Orders", seed_change_orders),
        ("Project Memory Facts", seed_memory),
        ("Workflow History", seed_workflows),
    ]

    for i, (name, func) in enumerate(steps, 1):
        print(f"[{i:2d}/{len(steps)}] Seeding {name}...")
        result = await func(ctx)
        ctx.update(result or {})
        print(f"       done")

    print()
    print("=" * 60)
    print("  Demo seeding complete!")
    print()
    print("  Demo credentials:")
    print("    PM login:      pm@buildright.dev / Demo2026!")
    print("    Safety login:  safety@buildright.dev / Demo2026!")
    print("    Owner login:   owner@riverside.dev / Demo2026!")
    print("    Admin login:   admin@constructai.dev / Demo2026!")
    print()
    print("  Quick start:")
    print("    Frontend:  http://localhost:3000")
    print("    API docs:  http://localhost:8000/docs")
    print("    pgAdmin:   http://localhost:5050")
    print("    Kafka UI:  http://localhost:8080")
    print("    MinIO:     http://localhost:9001")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(seed_all())
