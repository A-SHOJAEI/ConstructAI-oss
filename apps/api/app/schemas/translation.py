"""Pydantic schemas for translation endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class TranslateItem(BaseModel):
    """A single item within a batch translation request."""

    text: str = Field(..., min_length=1, max_length=5000)
    context: str | None = Field(
        None,
        pattern="^(safety_alert|daily_log|rfi|meeting_minutes|general)$",
        description="Translation context for tone/terminology",
    )
    reference_id: str | None = Field(
        None,
        description="Optional caller-provided reference to correlate results",
    )


class TranslateRequest(BaseModel):
    """Request body for single text translation."""

    text: str = Field(..., min_length=1, max_length=5000)
    target_language: str = Field(
        ...,
        min_length=2,
        max_length=2,
        description="ISO 639-1 two-letter target language code",
    )
    source_language: str | None = Field(
        None,
        min_length=2,
        max_length=2,
        description="ISO 639-1 source language code. Auto-detected if omitted.",
    )
    context: str | None = Field(
        None,
        pattern="^(safety_alert|daily_log|rfi|meeting_minutes|general)$",
        description="Translation context: safety_alert, daily_log, rfi, meeting_minutes, general",
    )


class TranslateBatchRequest(BaseModel):
    """Request body for batch translation."""

    items: list[TranslateItem] = Field(..., min_length=1, max_length=50)
    target_language: str = Field(
        ...,
        min_length=2,
        max_length=2,
        description="ISO 639-1 two-letter target language code",
    )


class DetectLanguageRequest(BaseModel):
    """Request body for language detection."""

    text: str = Field(..., min_length=1, max_length=5000)


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class TranslateResponse(BaseModel):
    """Response for a single translation."""

    translated_text: str
    source_language: str
    target_language: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    cached: bool


class TranslateBatchResponse(BaseModel):
    """Response for batch translation."""

    translations: list[TranslateResponse]


class DetectLanguageResponse(BaseModel):
    """Response for language detection."""

    language: str
    confidence: float = Field(..., ge=0.0, le=1.0)
