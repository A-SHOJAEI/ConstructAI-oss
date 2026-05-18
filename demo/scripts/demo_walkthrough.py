"""
Interactive CLI walkthrough for the ConstructAI demo.
Guides the presenter through each demo section with prompts.
"""
import sys
import time


def section(num: int, title: str, duration: str):
    print()
    print("=" * 60)
    print(f"  Section {num}: {title} ({duration})")
    print("=" * 60)


def step(text: str):
    print(f"\n  -> {text}")


def wait():
    input("\n  Press Enter to continue...")


def main():
    print()
    print("=" * 60)
    print("  ConstructAI Demo Walkthrough")
    print("  Riverside Mixed-Use Development")
    print("  Total duration: ~20 minutes")
    print("=" * 60)
    wait()

    # Section 1
    section(1, "Login & Overview", "2 min")
    step("Open http://localhost:3000")
    step("Log in as: pm@buildright.dev / Demo2026!")
    step("Show project dashboard:")
    step("  - SPI: 0.88 (behind schedule)")
    step("  - CPI: 0.91 (over budget)")
    step("  - 3 active change orders")
    step("  - 15 safety alerts in 30 days")
    step("  - 8 open punch list items")
    wait()

    # Section 2
    section(2, "Document Intelligence", "3 min")
    step("Navigate to Documents tab")
    step("Show 5 uploaded specifications (Div 03, 05, 07, 09, 26)")
    step("Click on Section 03 30 00 - Cast-in-Place Concrete")
    step('Ask: "What is the required compressive strength for foundation concrete?"')
    step("Show RAG answer: 5,000 PSI Class A per Section 2.01")
    step("Show citation to spec section with highlighted passage")
    step('Try conflict detection: "Is there a conflict between concrete and steel specs?"')
    wait()

    # Section 3
    section(3, "EVM & Project Controls", "3 min")
    step("Navigate to Controls dashboard")
    step("Show S-curve chart: PV, EV, AC lines diverging")
    step("Point out month 5 inflection (foundation issues)")
    step("Show current state: SPI 0.88, CPI 0.91")
    step("Show EAC forecast: ~$49.5M vs $45M budget")
    step("Show Monte Carlo histogram (if available)")
    step("Show critical risk drivers table")
    wait()

    # Section 4
    section(4, "Schedule Analysis", "2 min")
    step("Show schedule with 50 activities")
    step("Trigger DCMA 14-point check")
    step("Show failures found:")
    step("  - 3 missing predecessors (A012, A056, A066)")
    step("  - 2 excessive lags > 5 days (A024, A076)")
    step("  - 4 high float activities > 44 days (A012, A056, A066, A079)")
    step("  - 1 negative lag (A046)")
    step("Show critical path visualization")
    wait()

    # Section 5
    section(5, "Change Order Cascade", "3 min")
    step("Navigate to Change Orders")
    step("Show 3 COs: CO-001 (approved), CO-002 (awaiting), CO-003 (pending)")
    step("Open CO-003: Electrical Panel Upgrade ($95K)")
    step("Trigger analysis: run 'make demo-change-order'")
    step("Show orchestrator fanning out to 3 agents in parallel:")
    step("  - Estimating Agent: cost impact analysis")
    step("  - Scheduling Agent: schedule impact")
    step("  - Controls Agent: risk assessment")
    step("Show consolidated impact report")
    step("Show human-in-the-loop approval gate")
    wait()

    # Section 6
    section(6, "Safety Monitoring", "3 min")
    step("Navigate to Safety dashboard")
    step("Show camera grid (4 cameras)")
    step("Show alert timeline with P1/P2/P3/P4 badges")
    step("Show crane zone exclusion polygon on site map")
    step("Show false positive feedback workflow (2 marked)")
    step("Mention: temporal smoothing, alert deduplication")
    step("Switch login to safety@buildright.dev to show role-based access")
    wait()

    # Section 7
    section(7, "Quality & Punch List", "2 min")
    step("Navigate to Quality tab")
    step("Show 8 inspections (foundation, steel, MEP, drywall)")
    step("Show defect types: crack, misalignment, coordination conflict")
    step("Show punch list: 8 open, 4 in progress, 3 completed")
    step("Show compliance checks: IBC egress, ADA clearances")
    wait()

    # Section 8
    section(8, "Daily Reports & Communication", "2 min")
    step("Navigate to Reports")
    step("Show auto-generated daily report")
    step("Show meeting minutes with extracted action items")
    step("Show RFIs with AI-suggested responses")
    step("Show project memory / facts panel")
    wait()

    print()
    print("=" * 60)
    print("  Demo Complete!")
    print()
    print("  Additional explorations:")
    print("    pgAdmin:    http://localhost:5050  (54+ tables)")
    print("    Kafka UI:   http://localhost:8080  (event streams)")
    print("    MinIO:      http://localhost:9001  (document storage)")
    print("    API Docs:   http://localhost:8000/docs")
    print()
    print("  Trigger scripts:")
    print("    make demo-onboarding     # Full onboarding workflow")
    print("    make demo-change-order   # CO analysis cascade")
    print("    make demo-safety         # Safety incident response")
    print("=" * 60)


if __name__ == "__main__":
    main()
