import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.pagination import PaginationMeta


class DocumentUploadResponse(BaseModel):
    id: uuid.UUID
    title: str
    original_filename: str
    type: str
    processing_status: str
    s3_key: str
    file_size_bytes: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class DocumentResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    type: str
    title: str
    original_filename: str
    csi_division: str | None = None
    discipline: str | None = None
    revision: str | None = None
    cde_status: str
    s3_key: str
    file_size_bytes: int | None = None
    page_count: int | None = None
    processing_status: str
    processing_error: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DocumentStatusResponse(BaseModel):
    id: uuid.UUID
    processing_status: str
    processing_error: str | None = None
    page_count: int | None = None
    chunk_count: int = 0
    entity_count: int = 0

    model_config = {"from_attributes": True}


class DocumentListResponse(BaseModel):
    data: list[DocumentResponse]
    meta: PaginationMeta


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    project_id: uuid.UUID
    limit: int = Field(default=10, ge=1, le=50)
    search_type: str = Field(default="hybrid")


class SearchResultItem(BaseModel):
    chunk_id: uuid.UUID | None = None
    content: str
    document_id: uuid.UUID | None = None
    document_title: str
    page_number: int | None = None
    section_hierarchy: list[str] = []
    csi_section: str | None = None
    score: float


class SearchResponse(BaseModel):
    results: list[SearchResultItem]
    query: str
    total: int


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    project_id: uuid.UUID


class SourceCitation(BaseModel):
    chunk_id: uuid.UUID
    document_name: str
    page_number: int | None = None
    section: str | None = None
    relevance_score: float


class AskResponse(BaseModel):
    answer: str
    confidence: float
    sources: list[SourceCitation]
    model_used: str


class ClassifyRequest(BaseModel):
    document_id: uuid.UUID


class ClassificationResponse(BaseModel):
    document_id: uuid.UUID
    classified_type: str
    csi_division: str | None = None
    discipline: str | None = None
    confidence: float
    model_used: str


class EntityResponse(BaseModel):
    id: uuid.UUID
    entity_type: str
    entity_value: str
    section_reference: str | None = None
    confidence: float | None = None

    model_config = {"from_attributes": True}


class EntitiesListResponse(BaseModel):
    document_id: uuid.UUID
    entities: list[EntityResponse]
    total: int
