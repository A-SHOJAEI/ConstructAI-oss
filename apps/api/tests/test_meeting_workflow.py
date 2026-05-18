"""Tests for enhanced meeting minutes: transcription, action items, overdue tracking."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from app.models.communication import MeetingMinutes
from app.models.project import Project
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def test_project(db_session: AsyncSession, test_org):
    project = Project(
        name="Meeting Test Project",
        org_id=test_org.id,
        status="active",
        contract_value=Decimal("500000.00"),
    )
    db_session.add(project)
    await db_session.flush()
    await db_session.refresh(project)
    return project


@pytest_asyncio.fixture(scope="function")
async def test_meeting(db_session: AsyncSession, test_project):
    meeting = MeetingMinutes(
        project_id=test_project.id,
        meeting_type="progress",
        meeting_date=date.today(),
        title="Weekly Progress Meeting",
        attendees=[{"name": "John Doe", "role": "PM"}],
    )
    db_session.add(meeting)
    await db_session.flush()
    await db_session.refresh(meeting)
    return meeting


@pytest_asyncio.fixture(scope="function")
async def meeting_with_action_items(db_session: AsyncSession, test_project):
    yesterday = date.today() - timedelta(days=1)
    tomorrow = date.today() + timedelta(days=1)
    meeting = MeetingMinutes(
        project_id=test_project.id,
        meeting_type="progress",
        meeting_date=date.today() - timedelta(days=7),
        title="Last Week Meeting",
        attendees=[],
        action_items=[
            {
                "description": "Submit RFI for foundation",
                "assignee": "John",
                "due_date": yesterday.isoformat(),
                "status": "pending",
            },
            {
                "description": "Order steel beams",
                "assignee": "Jane",
                "due_date": yesterday.isoformat(),
                "status": "completed",
            },
            {
                "description": "Review electrical plans",
                "assignee": "Bob",
                "due_date": tomorrow.isoformat(),
                "status": "pending",
            },
        ],
    )
    db_session.add(meeting)
    await db_session.flush()
    await db_session.refresh(meeting)
    return meeting


# ---------------------------------------------------------------------------
# Meeting Enhancements
# ---------------------------------------------------------------------------


class TestMeetingEnhancements:
    @pytest.mark.asyncio
    async def test_create_meeting_with_new_fields(self, client, auth_headers, test_project):
        response = await client.post(
            "/api/v1/communication/meetings",
            json={
                "project_id": str(test_project.id),
                "meeting_type": "progress",
                "meeting_date": "2025-07-15",
                "title": "Progress Meeting #5",
                "attendees": [{"name": "Alice", "role": "Engineer"}],
                "meeting_location": "Conference Room A",
                "start_time": "09:00",
                "end_time": "10:30",
                "notes": "Discussed foundation progress",
                "agenda_items": [
                    {
                        "topic": "Foundation update",
                        "discussion": "On track",
                        "decision": "Continue as planned",
                    }
                ],
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["meeting_location"] == "Conference Room A"
        assert data["notes"] == "Discussed foundation progress"
        assert len(data["agenda_items"]) == 1

    @pytest.mark.asyncio
    async def test_meeting_status_defaults_to_draft(self, client, auth_headers, test_project):
        response = await client.post(
            "/api/v1/communication/meetings",
            json={
                "project_id": str(test_project.id),
                "meeting_type": "safety",
                "meeting_date": "2025-07-16",
                "title": "Safety Briefing",
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        assert response.json()["status"] == "draft"

    @pytest.mark.asyncio
    async def test_update_meeting_status(self, client, auth_headers, test_meeting):
        resp = await client.patch(
            f"/api/v1/communication/meetings/{test_meeting.id}",
            json={"status": "published"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "published"

    @pytest.mark.asyncio
    async def test_update_meeting_location_and_notes(self, client, auth_headers, test_meeting):
        resp = await client.patch(
            f"/api/v1/communication/meetings/{test_meeting.id}",
            json={
                "meeting_location": "Site Trailer",
                "notes": "Rain delay discussed",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["meeting_location"] == "Site Trailer"
        assert resp.json()["notes"] == "Rain delay discussed"

    @pytest.mark.asyncio
    async def test_update_meeting_not_found(self, client, auth_headers):
        resp = await client.patch(
            f"/api/v1/communication/meetings/{uuid.uuid4()}",
            json={"status": "published"},
            headers=auth_headers,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Transcription Flow
# ---------------------------------------------------------------------------


class TestTranscriptionFlow:
    @pytest.mark.asyncio
    @patch("app.api.v1.communication._validate_audio_magic_bytes", new=lambda *_: None)
    @patch("app.services.communication.meeting_service._get_transcriber")
    @patch("app.services.communication.meeting_service.upload_file")
    async def test_transcribe_meeting_populates_fields(
        self, mock_upload, mock_get_transcriber, client, auth_headers, test_meeting
    ):
        mock_upload.return_value = "meetings/test/audio.wav"

        mock_transcriber = AsyncMock()
        mock_transcriber.transcribe.return_value = {
            "transcript": "We discussed the foundation. Action item: submit the RFI by Friday.",
            "summary": "Foundation discussion and RFI submission.",
            "action_items": [
                {
                    "description": "Submit the RFI by Friday",
                    "assignee": None,
                    "due_date": None,
                    "status": "pending",
                }
            ],
            "decisions": [{"description": "Proceed with Type II foundation"}],
            "agenda_items": [
                {
                    "topic": "Foundation discussion",
                    "discussion": "We discussed the foundation",
                    "decision": "Proceed with Type II foundation",
                    "action_item": "Submit the RFI by Friday",
                    "responsible_party": None,
                    "due_date": None,
                }
            ],
            "duration_seconds": 1800.0,
        }
        mock_get_transcriber.return_value = mock_transcriber

        resp = await client.post(
            f"/api/v1/communication/meetings/{test_meeting.id}/transcribe",
            files={"file": ("meeting.wav", b"fake-audio-bytes", "audio/wav")},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "transcript" in data
        assert len(data["action_items"]) == 1
        assert len(data["decisions"]) == 1
        assert len(data["agenda_items"]) == 1
        assert data["duration_seconds"] == 1800.0

    @pytest.mark.asyncio
    @patch("app.api.v1.communication._validate_audio_magic_bytes", new=lambda *_: None)
    @patch("app.services.communication.meeting_service._get_transcriber")
    @patch("app.services.communication.meeting_service.upload_file")
    async def test_transcribe_sets_action_item_status_pending(
        self, mock_upload, mock_get_transcriber, client, auth_headers, test_meeting
    ):
        mock_upload.return_value = "meetings/test/audio.wav"

        mock_transcriber = AsyncMock()
        mock_transcriber.transcribe.return_value = {
            "transcript": "We need to order concrete by Monday.",
            "summary": "Concrete ordering.",
            "action_items": [
                {
                    "description": "Order concrete by Monday",
                    "assignee": None,
                    "due_date": None,
                    "status": "pending",
                }
            ],
            "decisions": [],
            "agenda_items": [],
            "duration_seconds": 600.0,
        }
        mock_get_transcriber.return_value = mock_transcriber

        resp = await client.post(
            f"/api/v1/communication/meetings/{test_meeting.id}/transcribe",
            files={"file": ("meeting.wav", b"fake-audio", "audio/wav")},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        for item in resp.json()["action_items"]:
            assert item["status"] == "pending"

    @pytest.mark.asyncio
    async def test_transcribe_nonexistent_meeting_returns_404(self, client, auth_headers):
        resp = await client.post(
            f"/api/v1/communication/meetings/{uuid.uuid4()}/transcribe",
            files={"file": ("meeting.wav", b"fake-audio", "audio/wav")},
            headers=auth_headers,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Action Item Tracking
# ---------------------------------------------------------------------------


class TestActionItemTracking:
    @pytest.mark.asyncio
    async def test_update_action_item_to_in_progress(
        self, client, auth_headers, meeting_with_action_items
    ):
        resp = await client.patch(
            f"/api/v1/communication/meetings/{meeting_with_action_items.id}/action-items/0",
            json={"status": "in_progress"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "in_progress"

    @pytest.mark.asyncio
    async def test_update_action_item_to_completed(
        self, client, auth_headers, meeting_with_action_items
    ):
        resp = await client.patch(
            f"/api/v1/communication/meetings/{meeting_with_action_items.id}/action-items/0",
            json={"status": "completed"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"

    @pytest.mark.asyncio
    async def test_update_action_item_invalid_index(
        self, client, auth_headers, meeting_with_action_items
    ):
        resp = await client.patch(
            f"/api/v1/communication/meetings/{meeting_with_action_items.id}/action-items/99",
            json={"status": "completed"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_update_action_item_invalid_status(
        self, client, auth_headers, meeting_with_action_items
    ):
        resp = await client.patch(
            f"/api/v1/communication/meetings/{meeting_with_action_items.id}/action-items/0",
            json={"status": "invalid_status"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_update_action_item_meeting_not_found(self, client, auth_headers):
        resp = await client.patch(
            f"/api/v1/communication/meetings/{uuid.uuid4()}/action-items/0",
            json={"status": "completed"},
            headers=auth_headers,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Overdue Action Items
# ---------------------------------------------------------------------------


class TestOverdueActionItems:
    @pytest.mark.asyncio
    async def test_overdue_action_items_returns_past_due(
        self, client, auth_headers, test_project, meeting_with_action_items
    ):
        resp = await client.get(
            "/api/v1/communication/meetings/action-items/overdue",
            params={"project_id": str(test_project.id)},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        # Should have 1 overdue item (item 0: past due + pending)
        # Item 1 is past due but completed, item 2 is future
        assert data["total"] == 1
        assert data["data"][0]["description"] == "Submit RFI for foundation"
        assert data["data"][0]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_overdue_action_items_excludes_completed(
        self, client, auth_headers, test_project, meeting_with_action_items
    ):
        resp = await client.get(
            "/api/v1/communication/meetings/action-items/overdue",
            params={"project_id": str(test_project.id)},
            headers=auth_headers,
        )
        data = resp.json()
        descriptions = [item["description"] for item in data["data"]]
        assert "Order steel beams" not in descriptions  # completed item

    @pytest.mark.asyncio
    async def test_overdue_action_items_excludes_future_due(
        self, client, auth_headers, test_project, meeting_with_action_items
    ):
        resp = await client.get(
            "/api/v1/communication/meetings/action-items/overdue",
            params={"project_id": str(test_project.id)},
            headers=auth_headers,
        )
        data = resp.json()
        descriptions = [item["description"] for item in data["data"]]
        assert "Review electrical plans" not in descriptions  # future due date

    @pytest.mark.asyncio
    async def test_overdue_action_items_empty_when_none(
        self, client, auth_headers, test_project, test_meeting
    ):
        # test_meeting has no action items
        resp = await client.get(
            "/api/v1/communication/meetings/action-items/overdue",
            params={"project_id": str(test_project.id)},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["data"] == []

    @pytest.mark.asyncio
    async def test_overdue_action_items_filter_by_project(
        self, client, auth_headers, test_project, meeting_with_action_items, db_session, test_org
    ):
        # Create another project with no meetings
        other_project = Project(
            name="Other Project",
            org_id=test_org.id,
            status="active",
        )
        db_session.add(other_project)
        await db_session.flush()
        await db_session.refresh(other_project)

        resp = await client.get(
            "/api/v1/communication/meetings/action-items/overdue",
            params={"project_id": str(other_project.id)},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 0
