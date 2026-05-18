"""Enhanced meeting minutes service: transcription, action items, overdue tracking."""

from __future__ import annotations

import logging
import os
import tempfile
import uuid
from datetime import date, time
from typing import Any

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.communication import MeetingMinutes
from app.services.communication.transcriber import MeetingTranscriber
from app.utils.s3 import upload_file

logger = logging.getLogger(__name__)

# Singleton transcriber
_transcriber: MeetingTranscriber | None = None


def _get_transcriber() -> MeetingTranscriber:
    global _transcriber
    if _transcriber is None:
        _transcriber = MeetingTranscriber()
    return _transcriber


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------


async def transcribe_meeting(
    db: AsyncSession,
    meeting_id: uuid.UUID,
    audio_file: UploadFile,
    project_id: uuid.UUID,
) -> dict | None:
    """Upload audio, transcribe, and populate meeting fields."""
    # Verify meeting exists and belongs to the project
    query = select(MeetingMinutes).where(
        MeetingMinutes.id == meeting_id, MeetingMinutes.project_id == project_id
    )
    result = await db.execute(query)
    meeting = result.scalar_one_or_none()
    if not meeting:
        return None

    # Save audio file to S3
    MAX_AUDIO_SIZE_BYTES = 100 * 1024 * 1024  # 100 MB
    filename = audio_file.filename or "audio.wav"
    ext = os.path.splitext(filename)[1] or ".wav"
    audio_bytes = await audio_file.read()
    if len(audio_bytes) > MAX_AUDIO_SIZE_BYTES:
        from fastapi import HTTPException

        raise HTTPException(status_code=413, detail="Audio file exceeds 100 MB limit")
    s3_key = f"meetings/{meeting.project_id}/{meeting_id}/audio{ext}"
    upload_file(s3_key, audio_bytes, audio_file.content_type or "audio/wav")

    # Save to temp file for transcription
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        transcriber = _get_transcriber()
        result_data = await transcriber.transcribe(tmp_path)
    finally:
        os.unlink(tmp_path)

    # Update meeting record
    meeting.transcript = result_data.get("transcript", "")
    meeting.summary = result_data.get("summary", "")
    meeting.action_items = result_data.get("action_items", [])
    meeting.decisions = result_data.get("decisions", [])
    meeting.agenda_items = result_data.get("agenda_items", [])
    meeting.audio_url = s3_key

    await db.flush()
    await db.refresh(meeting)

    return {
        "meeting_id": str(meeting.id),
        "transcript": meeting.transcript or "",
        "summary": meeting.summary or "",
        "action_items": meeting.action_items,
        "decisions": meeting.decisions,
        "agenda_items": meeting.agenda_items,
        "duration_seconds": result_data.get("duration_seconds", 0.0),
    }


# ---------------------------------------------------------------------------
# Overdue Action Items
# ---------------------------------------------------------------------------


async def get_overdue_action_items(
    db: AsyncSession,
    project_id: uuid.UUID,
    assigned_to: str | None = None,
) -> list[dict]:
    """Find action items across meetings that are past due and not completed."""
    query = select(MeetingMinutes).where(MeetingMinutes.project_id == project_id)
    result = await db.execute(query)
    meetings = result.scalars().all()

    today = date.today()
    overdue: list[dict] = []

    for meeting in meetings:
        action_items = meeting.action_items or []
        for idx, item in enumerate(action_items):
            if not isinstance(item, dict):
                continue

            item_status = item.get("status", "pending")
            if item_status == "completed":
                continue

            due_date_str = item.get("due_date")
            if not due_date_str:
                continue

            try:
                item_due = date.fromisoformat(str(due_date_str))
            except (ValueError, TypeError):
                continue

            if item_due >= today:
                continue

            assignee = item.get("assignee")
            if assigned_to and assignee != assigned_to:
                continue

            overdue.append(
                {
                    "meeting_id": str(meeting.id),
                    "meeting_title": meeting.title,
                    "meeting_date": meeting.meeting_date.isoformat(),
                    "item_index": idx,
                    "description": item.get("description", ""),
                    "assignee": assignee,
                    "due_date": item_due.isoformat(),
                    "status": item_status,
                }
            )

    return overdue


# ---------------------------------------------------------------------------
# Action Item Status Updates
# ---------------------------------------------------------------------------

VALID_ACTION_ITEM_STATUSES = {"pending", "in_progress", "completed"}


async def update_action_item_status(
    db: AsyncSession,
    meeting_id: uuid.UUID,
    item_index: int,
    new_status: str,
    project_id: uuid.UUID,
) -> dict | None:
    """Update the status of an action item at the given index."""
    if new_status not in VALID_ACTION_ITEM_STATUSES:
        raise ValueError(f"Invalid status: {new_status}")

    query = select(MeetingMinutes).where(
        MeetingMinutes.id == meeting_id, MeetingMinutes.project_id == project_id
    )
    result = await db.execute(query)
    meeting = result.scalar_one_or_none()
    if not meeting:
        return None

    action_items = list(meeting.action_items or [])
    if item_index < 0 or item_index >= len(action_items):
        raise IndexError(f"Action item index {item_index} out of range")

    action_items[item_index]["status"] = new_status
    meeting.action_items = action_items

    await db.flush()
    await db.refresh(meeting)

    return action_items[item_index]


# ---------------------------------------------------------------------------
# Meeting Update
# ---------------------------------------------------------------------------


async def update_meeting(
    db: AsyncSession,
    meeting_id: uuid.UUID,
    data: dict[str, Any],
    project_id: uuid.UUID,
) -> MeetingMinutes | None:
    """Partial update of meeting fields."""
    query = select(MeetingMinutes).where(
        MeetingMinutes.id == meeting_id, MeetingMinutes.project_id == project_id
    )
    result = await db.execute(query)
    meeting = result.scalar_one_or_none()
    if not meeting:
        return None

    # Handle time fields specially
    for time_field in ("start_time", "end_time"):
        if time_field in data and data[time_field] is not None:
            time_str = data[time_field]
            if isinstance(time_str, str):
                parts = time_str.split(":")
                data[time_field] = time(int(parts[0]), int(parts[1]))

    for key, value in data.items():
        if value is not None and hasattr(meeting, key):
            setattr(meeting, key, value)

    await db.flush()
    await db.refresh(meeting)
    return meeting
