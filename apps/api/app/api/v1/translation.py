"""Translation API endpoints for multilingual construction communication.

All routes require authentication.  Project-scoped routes additionally
verify that the authenticated user belongs to the project's organization.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission
from app.models.user import User
from app.schemas.translation import (
    DetectLanguageRequest,
    DetectLanguageResponse,
    TranslateBatchRequest,
    TranslateBatchResponse,
    TranslateRequest,
    TranslateResponse,
)
from app.services.communication.translation_service import (
    SUPPORTED_LANGUAGES,
    get_translation_service,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# POST /translate — single text translation
# ---------------------------------------------------------------------------


@router.post(
    "/translate",
    response_model=TranslateResponse,
)
async def translate_text(
    body: TranslateRequest,
    current_user: User = Depends(require_permission("communication", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Translate a single text to the specified target language.

    Requires ``communication:read`` permission.  Auto-detects the source
    language if not provided.  Construction-domain terms (CSI codes, OSHA
    references, measurements) are preserved untranslated.
    """
    # Validate language codes
    if body.target_language not in SUPPORTED_LANGUAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported target language '{body.target_language}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_LANGUAGES))}"
            ),
        )

    if body.source_language and body.source_language not in SUPPORTED_LANGUAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported source language '{body.source_language}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_LANGUAGES))}"
            ),
        )

    service = get_translation_service()

    try:
        result = await service.translate(
            text=body.text,
            target_lang=body.target_language,
            source_lang=body.source_language,
            context=body.context,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except RuntimeError as exc:
        logger.error("Translation LLM call failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Translation service temporarily unavailable.",
        )

    return TranslateResponse(
        translated_text=result.translated_text,
        source_language=result.source_language,
        target_language=result.target_language,
        confidence=result.confidence,
        cached=result.cached,
    )


# ---------------------------------------------------------------------------
# POST /translate/batch — batch translation (up to 50 items)
# ---------------------------------------------------------------------------


@router.post(
    "/translate/batch",
    response_model=TranslateBatchResponse,
)
async def translate_batch(
    body: TranslateBatchRequest,
    current_user: User = Depends(require_permission("communication", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Translate up to 50 texts in an optimized batch.

    Deduplicates identical texts and batches up to 20 per LLM call for
    efficiency.  Requires ``communication:read`` permission.
    """
    if body.target_language not in SUPPORTED_LANGUAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported target language '{body.target_language}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_LANGUAGES))}"
            ),
        )

    service = get_translation_service()

    items = [
        {
            "text": item.text,
            "context": item.context,
            "reference_id": item.reference_id,
        }
        for item in body.items
    ]

    try:
        results = await service.translate_batch(items=items, target_lang=body.target_language)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except RuntimeError as exc:
        logger.error("Batch translation LLM call failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Translation service temporarily unavailable.",
        )

    return TranslateBatchResponse(
        translations=[
            TranslateResponse(
                translated_text=r.translated_text,
                source_language=r.source_language,
                target_language=r.target_language,
                confidence=r.confidence,
                cached=r.cached,
            )
            for r in results
        ]
    )


# ---------------------------------------------------------------------------
# POST /translate/detect — language detection only
# ---------------------------------------------------------------------------


@router.post(
    "/translate/detect",
    response_model=DetectLanguageResponse,
)
async def detect_language(
    body: DetectLanguageRequest,
    current_user: User = Depends(require_permission("communication", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Detect the language of the provided text.

    Returns an ISO 639-1 two-letter language code and a confidence score.
    Requires ``communication:read`` permission.
    """
    service = get_translation_service()

    try:
        lang_code = await service.detect_language(body.text)
    except RuntimeError as exc:
        logger.error("Language detection LLM call failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Language detection service temporarily unavailable.",
        )

    # Confidence is high for known languages, moderate otherwise
    confidence = 0.95 if lang_code in SUPPORTED_LANGUAGES else 0.5

    return DetectLanguageResponse(
        language=lang_code,
        confidence=confidence,
    )
