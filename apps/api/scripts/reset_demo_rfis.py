"""Reset the seeded demo RFIs back to a "fresh" state.

After demo dry-runs, the RFIs end up with answered status, AI drafts in
the response timeline, and resolution-log rows. This script puts them
back to where seed_demo_content.py + seed_demo_extras.py left them,
EXCEPT it also clears the pre-baked answers for ALL seeded RFIs so the
user can demonstrate the AI generating each one from scratch.

What it does, per RFI in (demo_session_01..06):
  - status = "open"
  - clear answer / response / responded_at / response_status / responded_by
  - delete rows in rfi_responses for this RFI
  - delete rfi_resolution_logs rows for this RFI
  - clear ai_suggested_response

What it preserves:
  - The RFI itself (id, project_id, rfi_number, subject, question,
    spec_section, drawing_reference, priority)
  - The corpus chunks and embeddings (so the RAG pipeline still has
    documents + similar-RFI candidates to draw on)
  - The submittals, daily reports, meetings, punch list, PCOs, alerts

Run:
    cd apps/api && .venv/bin/python scripts/reset_demo_rfis.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import text

from app.database import async_session


async def reset() -> None:
    async with async_session() as db:
        # Find demo project ids
        rows = (
            await db.execute(
                text(
                    "SELECT p.id FROM projects p "
                    "JOIN organizations o ON o.id = p.org_id "
                    "WHERE o.slug LIKE 'demo_session_%'"
                )
            )
        ).all()
        project_ids = [str(r[0]) for r in rows]
        if not project_ids:
            print("No demo projects found.")
            return
        print(f"Resetting RFIs across {len(project_ids)} demo projects.")

        # Drop downstream artifacts first (FK to rfis.id)
        deleted_responses = await db.execute(
            text(
                "DELETE FROM rfi_responses WHERE rfi_id IN ("
                "  SELECT id FROM rfis WHERE project_id = ANY(:projects)"
                ")"
            ),
            {"projects": project_ids},
        )
        print(f"  deleted rfi_responses: {deleted_responses.rowcount}")

        deleted_logs = await db.execute(
            text(
                "DELETE FROM rfi_resolution_logs WHERE rfi_id IN ("
                "  SELECT id FROM rfis WHERE project_id = ANY(:projects)"
                ")"
            ),
            {"projects": project_ids},
        )
        print(f"  deleted rfi_resolution_logs: {deleted_logs.rowcount}")

        # Reset the RFI rows themselves to a fresh "open" state
        updated = await db.execute(
            text(
                "UPDATE rfis SET "
                "  status = 'open', "
                "  answer = NULL, "
                "  response = NULL, "
                "  responded_at = NULL, "
                "  ai_suggested_response = NULL, "
                "  date_answered = NULL, "
                "  date_closed = NULL "
                "WHERE project_id = ANY(:projects)"
            ),
            {"projects": project_ids},
        )
        print(f"  reset rfis: {updated.rowcount}")

        await db.commit()

    print("\nDone. Re-run AI Draft Response / Auto-Resolve to see fresh outputs.")


if __name__ == "__main__":
    asyncio.run(reset())
