"""Pydantic schemas for communication endpoints."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.pagination import PaginationMeta

# H-3: Shared vocabularies constrained at the schema layer.
RFIPriority = Literal["low", "normal", "high", "urgent"]
RFIStatus = Literal["open", "in_review", "answered", "closed", "void"]
MeetingStatus = Literal["draft", "published", "archived"]
ActionItemStatus = Literal["pending", "in_progress", "completed"]


class DailyReportCreate(BaseModel):
    project_id: uuid.UUID
    report_date: date


class DailyReportResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    report_date: date
    status: str
    content_markdown: str | None = None
    content_html: str | None = None
    pdf_url: str | None = None
    sections: dict
    generated_by: str
    reviewed_by: uuid.UUID | None = None
    published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DailyReportListResponse(BaseModel):
    data: list[DailyReportResponse]
    meta: PaginationMeta


class MeetingMinutesCreate(BaseModel):
    project_id: uuid.UUID
    meeting_type: str = Field(min_length=1, max_length=100)
    meeting_date: date
    title: str = Field(min_length=1, max_length=200)
    attendees: list[dict] = Field(default_factory=list, max_length=200)
    meeting_location: str | None = Field(default=None, max_length=500)
    start_time: str | None = Field(default=None, max_length=8)  # HH:MM format
    end_time: str | None = Field(default=None, max_length=8)  # HH:MM format
    notes: str | None = Field(default=None, max_length=20000)
    agenda_items: list[dict] = Field(default_factory=list, max_length=100)


class MeetingMinutesResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    meeting_type: str
    meeting_date: date
    title: str
    attendees: list
    transcript: str | None = None
    summary: str | None = None
    action_items: list
    decisions: list
    audio_url: str | None = None
    pdf_url: str | None = None
    meeting_location: str | None = None
    start_time: time | None = None
    end_time: time | None = None
    agenda_items: list = Field(default_factory=list)
    notes: str | None = None
    status: str = "draft"
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MeetingMinutesListResponse(BaseModel):
    data: list[MeetingMinutesResponse]
    meta: PaginationMeta


class MeetingMinutesUpdate(BaseModel):
    """Partial update for meeting minutes."""

    meeting_location: str | None = Field(default=None, max_length=500)
    start_time: str | None = Field(default=None, max_length=8)
    end_time: str | None = Field(default=None, max_length=8)
    notes: str | None = Field(default=None, max_length=20000)
    status: MeetingStatus | None = None
    agenda_items: list[dict] | None = Field(default=None, max_length=100)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    attendees: list[dict] | None = Field(default=None, max_length=200)


class AgendaItem(BaseModel):
    """Structured agenda item from a meeting."""

    topic: str
    discussion: str | None = None
    decision: str | None = None
    action_item: str | None = None
    responsible_party: str | None = None
    due_date: date | None = None


class ActionItemUpdate(BaseModel):
    """Update an action item's status."""

    status: ActionItemStatus


class OverdueActionItem(BaseModel):
    """A single overdue action item from a meeting."""

    meeting_id: uuid.UUID
    meeting_title: str
    meeting_date: date
    item_index: int
    description: str
    assignee: str | None = None
    due_date: date
    status: str


class OverdueActionItemsResponse(BaseModel):
    data: list[OverdueActionItem]
    total: int


class TranscribeRequest(BaseModel):
    meeting_id: uuid.UUID
    audio_url: str


class TranscribeResponse(BaseModel):
    meeting_id: uuid.UUID
    transcript: str
    summary: str
    action_items: list[dict]
    decisions: list[dict]
    duration_seconds: float


class TranscribeUploadResponse(BaseModel):
    """Response from audio file upload + transcription."""

    meeting_id: uuid.UUID
    transcript: str
    summary: str
    action_items: list[dict]
    decisions: list[dict]
    agenda_items: list[dict]
    duration_seconds: float


class RFICreate(BaseModel):
    project_id: uuid.UUID
    rfi_number: str
    subject: str
    question: str
    priority: str = "normal"
    due_date: date | None = None


class RFIResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    rfi_number: str
    subject: str
    question: str
    status: str
    priority: str
    submitted_by: uuid.UUID | None = None
    assigned_to: uuid.UUID | None = None
    response: str | None = None
    ai_suggested_response: str | None = None
    due_date: date | None = None
    responded_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RFIListResponse(BaseModel):
    data: list[RFIResponse]
    meta: PaginationMeta


class SubmittalCreate(BaseModel):
    project_id: uuid.UUID
    submittal_number: str
    title: str
    spec_section: str | None = None
    document_urls: list[str] = Field(default_factory=list)
    due_date: date | None = None


class SubmittalResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    submittal_number: str
    title: str
    spec_section: str | None = None
    status: str
    submitted_by: uuid.UUID | None = None
    reviewer_id: uuid.UUID | None = None
    document_urls: list
    review_comments: list
    due_date: date | None = None
    submitted_at: datetime | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SubmittalListResponse(BaseModel):
    data: list[SubmittalResponse]
    meta: PaginationMeta


# ---------------------------------------------------------------------------
# Submittal workflow v2 schemas
# ---------------------------------------------------------------------------


class SubmittalCreateV2(BaseModel):
    """Create a submittal — project_id from path, number auto-generated."""

    title: str
    description: str | None = None
    spec_section: str | None = None
    spec_section_name: str | None = None
    submittal_type: str = "shop_drawing"
    priority: str = "normal"
    due_date: date | None = None
    date_required: date | None = None
    lead_time_days: int | None = None
    review_chain: list[dict] = Field(default_factory=list)
    distribution_list: list[dict] = Field(default_factory=list)
    linked_rfi_ids: list[str] = Field(default_factory=list)


class SubmittalUpdate(BaseModel):
    """Partial update for a submittal."""

    title: str | None = None
    description: str | None = None
    spec_section: str | None = None
    spec_section_name: str | None = None
    submittal_type: str | None = None
    priority: str | None = None
    status: str | None = None
    assigned_to: uuid.UUID | None = None
    ball_in_court: uuid.UUID | None = None
    due_date: date | None = None
    date_required: date | None = None
    lead_time_days: int | None = None
    review_chain: list[dict] | None = None
    distribution_list: list[dict] | None = None
    linked_rfi_ids: list[str] | None = None


class SubmittalReviewCreate(BaseModel):
    """Submit a review action on a submittal."""

    review_action: str
    comments: str | None = None


class SubmittalResubmitRequest(BaseModel):
    """Resubmit a submittal for a new revision."""

    notes: str | None = None


class SubmittalReviewItem(BaseModel):
    """A single review action in the submittal approval chain."""

    id: uuid.UUID
    submittal_id: uuid.UUID
    reviewer_id: uuid.UUID | None = None
    review_action: str
    comments: str | None = None
    revision_number: int = 0
    reviewed_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


class SubmittalAttachmentItem(BaseModel):
    """An attachment on a submittal."""

    id: uuid.UUID
    submittal_id: uuid.UUID
    file_path: str
    file_name: str
    file_type: str | None = None
    file_size_bytes: int | None = None
    uploaded_by: uuid.UUID | None = None
    uploaded_at: datetime
    download_url: str | None = None

    model_config = {"from_attributes": True}


class SubmittalDetailResponse(BaseModel):
    """Full submittal detail including reviews and attachments."""

    id: uuid.UUID
    project_id: uuid.UUID
    submittal_number: str
    title: str
    description: str | None = None
    spec_section: str | None = None
    spec_section_name: str | None = None
    submittal_type: str = "other"
    status: str
    priority: str = "normal"
    revision_number: int = 0
    submitted_by: uuid.UUID | None = None
    reviewer_id: uuid.UUID | None = None
    current_reviewer: uuid.UUID | None = None
    ball_in_court: uuid.UUID | None = None
    document_urls: list = Field(default_factory=list)
    review_comments: list = Field(default_factory=list)
    due_date: date | None = None
    date_required: date | None = None
    date_submitted: datetime | None = None
    date_returned: datetime | None = None
    submitted_at: datetime | None = None
    reviewed_at: datetime | None = None
    lead_time_days: int | None = None
    distribution_list: list = Field(default_factory=list)
    linked_rfi_ids: list = Field(default_factory=list)
    review_chain: list = Field(default_factory=list)
    data_source: str = "manual"
    is_overdue: bool = False
    days_open: int | None = None
    reviews: list[SubmittalReviewItem] = Field(default_factory=list)
    attachments: list[SubmittalAttachmentItem] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SubmittalDetailListResponse(BaseModel):
    """Paginated list of submittal details."""

    data: list[SubmittalDetailResponse]
    meta: PaginationMeta


class SubmittalStatsResponse(BaseModel):
    """Aggregate submittal statistics for a project."""

    total: int
    not_submitted: int = 0
    pending_review: int = 0
    approved: int = 0
    approved_as_noted: int = 0
    revise_and_resubmit: int = 0
    rejected: int = 0
    closed: int = 0
    overdue: int = 0
    avg_review_days: float | None = None


class SubmittalRegisterEntry(BaseModel):
    """One row in the submittal register matrix."""

    spec_section: str
    spec_section_name: str | None = None
    total: int = 0
    not_submitted: int = 0
    pending_review: int = 0
    approved: int = 0
    approved_as_noted: int = 0
    revise_and_resubmit: int = 0
    rejected: int = 0
    closed: int = 0


class SubmittalRegisterResponse(BaseModel):
    """Submittal register — spec section × status matrix."""

    data: list[SubmittalRegisterEntry]


# ---------------------------------------------------------------------------
# RFI workflow v2 schemas
# ---------------------------------------------------------------------------


class RFICreateV2(BaseModel):
    """Create an RFI — project_id from path, rfi_number auto-generated."""

    subject: str = Field(min_length=1, max_length=300)
    question: str = Field(min_length=1, max_length=10000)
    priority: RFIPriority = "normal"
    status: RFIStatus = "open"
    assigned_to: uuid.UUID | None = None
    due_date: date | None = None
    spec_section: str | None = Field(default=None, max_length=100)
    drawing_reference: str | None = Field(default=None, max_length=100)
    cost_impact: bool | None = None
    schedule_impact: bool | None = None
    cost_impact_amount: float | None = None
    schedule_impact_days: int | None = None
    distribution_list: list[dict] = Field(default_factory=list, max_length=100)


class RFIUpdate(BaseModel):
    """Partial update for an RFI."""

    subject: str | None = Field(default=None, min_length=1, max_length=300)
    question: str | None = Field(default=None, min_length=1, max_length=10000)
    answer: str | None = Field(default=None, max_length=20000)
    priority: RFIPriority | None = None
    status: RFIStatus | None = None
    assigned_to: uuid.UUID | None = None
    ball_in_court: uuid.UUID | None = None
    due_date: date | None = None
    spec_section: str | None = Field(default=None, max_length=100)
    drawing_reference: str | None = Field(default=None, max_length=100)
    cost_impact: bool | None = None
    schedule_impact: bool | None = None
    cost_impact_amount: float | None = None
    schedule_impact_days: int | None = None
    distribution_list: list[dict] | None = Field(default=None, max_length=100)


class RFIResponseCreate(BaseModel):
    """Submit a response to an RFI."""

    response_text: str = Field(min_length=1, max_length=20000)


class RFICloseRequest(BaseModel):
    """Close an RFI with an optional final answer."""

    answer: str | None = Field(default=None, max_length=20000)


class RFIResponseItem(BaseModel):
    """A single response in the RFI review chain."""

    id: uuid.UUID
    rfi_id: uuid.UUID
    responder_id: uuid.UUID | None = None
    response_text: str
    status: str
    responded_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


class RFIAttachmentItem(BaseModel):
    """An attachment on an RFI."""

    id: uuid.UUID
    rfi_id: uuid.UUID
    file_path: str
    file_name: str
    file_type: str | None = None
    file_size_bytes: int | None = None
    uploaded_by: uuid.UUID | None = None
    uploaded_at: datetime
    download_url: str | None = None

    model_config = {"from_attributes": True}


class RFIDetailResponse(BaseModel):
    """Full RFI detail including responses and attachments."""

    id: uuid.UUID
    project_id: uuid.UUID
    rfi_number: str
    subject: str
    question: str
    answer: str | None = None
    status: str
    priority: str
    submitted_by: uuid.UUID | None = None
    assigned_to: uuid.UUID | None = None
    ball_in_court: uuid.UUID | None = None
    response: str | None = None
    ai_suggested_response: str | None = None
    due_date: date | None = None
    spec_section: str | None = None
    drawing_reference: str | None = None
    cost_impact: bool | None = None
    schedule_impact: bool | None = None
    cost_impact_amount: float | None = None
    schedule_impact_days: int | None = None
    distribution_list: list = Field(default_factory=list)
    date_sent: date | None = None
    date_answered: datetime | None = None
    date_closed: datetime | None = None
    responded_at: datetime | None = None
    data_source: str = "manual"
    is_overdue: bool = False
    days_open: int | None = None
    responses: list[RFIResponseItem] = Field(default_factory=list)
    attachments: list[RFIAttachmentItem] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RFIDetailListResponse(BaseModel):
    """Paginated list of RFI details."""

    data: list[RFIDetailResponse]
    meta: PaginationMeta


class RFIStatsResponse(BaseModel):
    """Aggregate RFI statistics for a project."""

    total: int
    draft: int = 0
    open: int = 0
    pending_review: int = 0
    answered: int = 0
    closed: int = 0
    void: int = 0
    overdue: int = 0
    avg_response_days: float | None = None


class BulkRFIUpdateRequest(BaseModel):
    """Bulk-update status and/or assignee for multiple RFIs at once."""

    rfi_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=100)
    status: str | None = None
    assigned_to: uuid.UUID | None = None


class BulkRFIUpdateResponse(BaseModel):
    """Result of a bulk RFI update."""

    updated: int
    failed: int
    errors: list[dict] = Field(default_factory=list)
