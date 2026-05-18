"""
Generate a simplified P6 XER-like schedule export.

This creates a tab-delimited file mimicking Primavera P6 XER format
for demo/import purposes. A full XER requires PyP6Xer or similar.

Usage:
    python -m demo.generators.generate_schedule_xer [output_path]
"""
import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT_START = date(2025, 5, 1)

# Subset of schedule activities for XER export
ACTIVITIES = [
    ("A010", "Mobilization", 10, "2025-05-01", "2025-05-11"),
    ("A020", "Site Clearing", 8, "2025-05-12", "2025-05-20"),
    ("A022", "Excavation - Parking Garage", 25, "2025-05-21", "2025-06-15"),
    ("A030", "Foundation Formwork", 15, "2025-06-16", "2025-07-01"),
    ("A031", "Foundation Rebar", 12, "2025-07-02", "2025-07-14"),
    ("A032", "Foundation Concrete Pour", 8, "2025-07-15", "2025-07-23"),
    ("A042", "Steel Erection - Level 1", 15, "2025-07-24", "2025-08-08"),
    ("A043", "Steel Erection - Level 2", 12, "2025-08-09", "2025-08-21"),
    ("A044", "Steel Erection - Level 3", 12, "2025-08-22", "2025-09-03"),
    ("A045", "Steel Erection - Level 4-5", 15, "2025-09-04", "2025-09-19"),
    ("A050", "Metal Deck - Level 1", 10, "2025-08-09", "2025-08-19"),
    ("A052", "Concrete on Deck", 20, "2025-08-20", "2025-09-09"),
    ("A060", "Electrical Rough-in L1-L2", 25, "2025-09-10", "2025-10-05"),
    ("A063", "Fire Sprinkler", 20, "2025-10-06", "2025-10-26"),
    ("A070", "Drywall & Framing L1-L2", 20, "2025-10-27", "2025-11-16"),
    ("A073", "Painting", 20, "2025-12-01", "2025-12-21"),
    ("A075", "Flooring - Office", 15, "2025-12-22", "2026-01-06"),
    ("A080", "MEP Systems Testing", 15, "2026-01-07", "2026-01-22"),
    ("A083", "Punch List", 15, "2026-02-01", "2026-02-16"),
    ("A085", "Certificate of Occupancy", 10, "2026-02-20", "2026-03-02"),
]


def generate_xer(output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = []
    # XER header
    lines.append("ERMHDR\t12.0\t2026-02-23\tConstructAI Demo Export")
    lines.append("")

    # Project table
    lines.append("%T\tPROJECT")
    lines.append("%F\tproj_id\tproj_short_name\tplan_start_date\tplan_end_date")
    lines.append(f"%R\t1\tRMD-2025-001\t{PROJECT_START}\t2026-10-31")
    lines.append("")

    # Calendar
    lines.append("%T\tCALENDAR")
    lines.append("%F\tclndr_id\tclndr_name\tday_hr_cnt")
    lines.append("%R\t1\tStandard 5-Day\t8.0")
    lines.append("")

    # Activities
    lines.append("%T\tTASK")
    lines.append("%F\ttask_id\ttask_code\ttask_name\ttarget_drtn_hr_cnt\tearly_start_date\tearly_end_date")
    for i, (code, name, dur, es, ef) in enumerate(ACTIVITIES, 1):
        lines.append(f"%R\t{i}\t{code}\t{name}\t{dur * 8}\t{es}\t{ef}")
    lines.append("")

    # Relationships
    lines.append("%T\tTASKPRED")
    lines.append("%F\ttask_id\tpred_task_id\tpred_type\tlag_hr_cnt")
    rels = [
        (2, 1, "PR_FS", 0), (3, 2, "PR_FS", 0), (4, 3, "PR_FS", 0),
        (5, 4, "PR_FS", 0), (6, 5, "PR_FS", 0), (7, 6, "PR_FS", 0),
        (8, 7, "PR_FS", 0), (9, 8, "PR_FS", 0), (10, 9, "PR_FS", 0),
    ]
    for tid, pid, ptype, lag in rels:
        lines.append(f"%R\t{tid}\t{pid}\t{ptype}\t{lag}")
    lines.append("")
    lines.append("%E")

    output_path.write_text("\n".join(lines))
    return output_path


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("demo/output/schedule.xer")
    p = generate_xer(out)
    print(f"Generated: {p}")
