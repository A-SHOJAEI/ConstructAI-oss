"""Pydantic schemas for the Ask ConstructAI endpoint."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    """Request body for the /ask endpoint."""

    question: str = Field(
        ...,
        min_length=3,
        max_length=2000,
        description="The natural language question to ask ConstructAI.",
    )
    conversation_id: str | None = Field(
        None,
        description="Optional conversation ID for context continuity. When provided, "
        "the last 3 Q&A pairs from this conversation are included as context, "
        "enabling follow-up questions like 'tell me more about that'.",
    )


class CitationSchema(BaseModel):
    """A citation reference in the answer."""

    source: str = Field(..., description="Name of the data source cited.")
    page: int | None = Field(None, description="Page number if applicable.")
    section: str | None = Field(None, description="Section reference if applicable.")
    excerpt: str = Field("", description="Text excerpt surrounding the citation.")


class AskResponse(BaseModel):
    """Response body from the /ask endpoint."""

    answer: str = Field(..., description="The generated answer text.")
    intent: str = Field(..., description="Classified intent of the question.")
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score for the answer (0.0-1.0).",
    )
    citations: list[CitationSchema] = Field(
        default_factory=list,
        description="List of citations referenced in the answer.",
    )
    data_sources: list[str] = Field(
        default_factory=list,
        description="List of data source names used to construct the answer.",
    )
    follow_up_suggestions: list[str] = Field(
        default_factory=list,
        description="Suggested follow-up questions.",
    )
    processing_time_ms: int = Field(
        ...,
        description="Total processing time in milliseconds.",
    )


class SuggestionsResponse(BaseModel):
    """Response body for the /ask/suggestions endpoint."""

    suggestions: list[str] = Field(
        ...,
        description="Starter questions based on available project data.",
    )
