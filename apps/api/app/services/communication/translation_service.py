"""Multilingual translation service for construction workforce communication.

Uses the LLM gateway for translation with construction-domain awareness.
Preserves CSI codes, OSHA references, measurements, and trade terminology.
Supports the 8 most common construction workforce languages in the US.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from app.utils.prompt_sanitizer import sanitize_for_prompt

logger = logging.getLogger(__name__)

# Redis TTL for translation cache entries (24 hours)
_REDIS_TRANSLATION_TTL = 86400

# ---------------------------------------------------------------------------
# Supported languages — the most common construction workforce languages in US
# ---------------------------------------------------------------------------

SUPPORTED_LANGUAGES: dict[str, str] = {
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "pt": "Portuguese",
    "zh": "Chinese",
    "ko": "Korean",
    "vi": "Vietnamese",
    "pl": "Polish",
}

# ---------------------------------------------------------------------------
# Regex patterns for construction terms that must be preserved untranslated
# ---------------------------------------------------------------------------

# CSI MasterFormat codes: "03 30 00", "03 3000", "033000"
_CSI_CODE_RE = re.compile(r"\b\d{2}\s?\d{2}\s?\d{2}\b")

# OSHA 29 CFR references: "1926.501", "1926.502(b)(1)", "29 CFR 1926"
_OSHA_REF_RE = re.compile(
    r"(?:29\s*CFR\s*)?1926\.\d{3,4}(?:\([a-z]\)(?:\(\d+\))*)*",
    re.IGNORECASE,
)

# Engineering measurements: "4000 PSI", "3/4 inch", "150 mm", "#4 rebar"
_MEASUREMENT_RE = re.compile(
    r"\b\d+(?:[./]\d+)?\s*(?:PSI|psi|MPa|kPa|ksi|"
    r"mm|cm|m|km|in|inch|inches|ft|feet|yd|yards|"
    r"lbs?|kg|oz|ton|tons|"
    r"gal|gallons?|liters?|L|cf|cy|sf|sy|"
    r"F|C|fahrenheit|celsius|"
    r"AWG|gauge|ga)\b",
    re.IGNORECASE,
)

# Rebar designations: "#4", "#10", "No. 5 rebar"
_REBAR_RE = re.compile(r"(?:#\d{1,2}|No\.\s*\d{1,2})\s*(?:rebar|bar)?", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TranslationResult:
    """Result of a single translation operation."""

    translated_text: str
    source_language: str
    target_language: str
    confidence: float
    cached: bool


# ---------------------------------------------------------------------------
# LRU cache implementation (thread-safe ordered dict)
# ---------------------------------------------------------------------------


class _LRUCache:
    """Thread-safe async LRU cache backed by OrderedDict with Redis fallback.

    All mutating operations are protected by an ``asyncio.Lock`` to
    prevent corruption when accessed concurrently from multiple coroutines.
    On cache miss from the in-memory LRU, checks Redis as a second-level
    cache.  On cache set, writes to both LRU and Redis.  Gracefully falls
    back to LRU-only if Redis is unavailable.
    """

    def __init__(self, maxlen: int = 5000):
        self._maxlen = maxlen
        self._cache: OrderedDict[str, TranslationResult] = OrderedDict()
        self._lock = asyncio.Lock()
        self._redis = None
        self._redis_checked = False

    async def _get_redis(self):
        """Lazily obtain a Redis connection. Returns None if unavailable."""
        if self._redis_checked:
            return self._redis
        self._redis_checked = True
        try:
            from app.config import settings

            redis_url = getattr(settings, "REDIS_URL", None)
            if not redis_url:
                return None
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(redis_url, decode_responses=True, socket_timeout=2)
            # Quick connectivity check
            await self._redis.ping()
            logger.info("Translation cache connected to Redis")
            return self._redis
        except Exception:
            logger.debug(
                "Redis not available for translation cache; using LRU only",
                exc_info=True,
            )
            self._redis = None
            return None

    async def get(self, key: str) -> TranslationResult | None:
        async with self._lock:
            # Check in-memory LRU first
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]

        # Check Redis as second-level cache (outside lock to avoid blocking)
        try:
            r = await self._get_redis()
            if r is not None:
                redis_key = f"translation:{key}"
                raw = await r.get(redis_key)
                if raw is not None:
                    data = json.loads(raw)
                    result = TranslationResult(
                        translated_text=data["translated_text"],
                        source_language=data["source_language"],
                        target_language=data["target_language"],
                        confidence=data["confidence"],
                        cached=True,
                    )
                    # Promote to in-memory LRU
                    async with self._lock:
                        if len(self._cache) >= self._maxlen:
                            self._cache.popitem(last=False)
                        self._cache[key] = result
                    return result
        except Exception:
            logger.debug("Redis get failed for translation cache", exc_info=True)

        return None

    async def put(self, key: str, value: TranslationResult) -> None:
        async with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self._cache[key] = value
            else:
                if len(self._cache) >= self._maxlen:
                    self._cache.popitem(last=False)
                self._cache[key] = value

        # Write-through to Redis (outside lock, fire-and-forget)
        try:
            r = await self._get_redis()
            if r is not None:
                redis_key = f"translation:{key}"
                data = json.dumps(
                    {
                        "translated_text": value.translated_text,
                        "source_language": value.source_language,
                        "target_language": value.target_language,
                        "confidence": value.confidence,
                    }
                )
                await r.set(redis_key, data, ex=_REDIS_TRANSLATION_TTL)
        except Exception:
            logger.debug("Redis set failed for translation cache", exc_info=True)

    @property
    def size(self) -> int:
        return len(self._cache)

    def clear(self) -> None:
        self._cache.clear()


# ---------------------------------------------------------------------------
# Context-specific system prompts
# ---------------------------------------------------------------------------

_BASE_SYSTEM_PROMPT = (
    "You are a professional translator specializing in construction industry "
    "communication. Translate the following text from {source_lang_name} to "
    "{target_lang_name}.\n\n"
    "CRITICAL RULES:\n"
    "1. Preserve ALL technical codes exactly as-is: CSI MasterFormat codes "
    "(e.g., '03 30 00'), OSHA references (e.g., '1926.501'), rebar "
    "designations (e.g., '#4 rebar'), and specification section numbers.\n"
    "2. Preserve ALL measurements and units exactly as-is (e.g., '4000 PSI', "
    "'3/4 inch', '150 mm').\n"
    "3. Preserve proper nouns, project names, and company names.\n"
    "4. Use construction industry terminology appropriate for the target language.\n"
    "5. Output ONLY the translated text. Do not add any explanation, notes, "
    "preamble, or commentary.\n"
)

_CONTEXT_PROMPTS: dict[str, str] = {
    "safety_alert": (
        "CONTEXT: This is a SAFETY ALERT for construction workers. "
        "Use DIRECT, URGENT language. Keep sentences short and clear. "
        "Safety-critical terms must be unambiguous. If the source uses "
        "imperative mood (commands), maintain that in the translation. "
        "Lives depend on clarity — do not soften warnings or add hedging language.\n"
    ),
    "daily_log": (
        "CONTEXT: This is a construction daily log / field report. "
        "Preserve field-specific terminology (e.g., 'pour', 'cure', 'form', "
        "'strip', 'backfill', 'grade'). Maintain the factual, reporting tone. "
        "Keep quantities, crew sizes, and equipment names accurate.\n"
    ),
    "rfi": (
        "CONTEXT: This is a Request for Information (RFI) in a construction "
        "project. Preserve all technical specifics including specification "
        "references, drawing numbers, detail callouts, and submittal numbers. "
        "Maintain the formal, precise tone appropriate for contractual "
        "communication.\n"
    ),
    "meeting_minutes": (
        "CONTEXT: These are construction meeting minutes. Preserve speaker "
        "names and attributions. Keep action item formatting intact. Maintain "
        "the structured format with dates, responsibilities, and deadlines.\n"
    ),
    "general": "",
}

_DETECT_LANGUAGE_PROMPT = (
    "You are a language detection expert. Identify the language of the "
    "following text. Respond with ONLY the ISO 639-1 two-letter language "
    "code (e.g., 'en' for English, 'es' for Spanish, 'fr' for French, "
    "'pt' for Portuguese, 'zh' for Chinese, 'ko' for Korean, 'vi' for "
    "Vietnamese, 'pl' for Polish). Output NOTHING else — just the two-letter code."
)


# ---------------------------------------------------------------------------
# Translation Service
# ---------------------------------------------------------------------------


class TranslationService:
    """Production translation service using the LLM gateway.

    Provides language detection, single/batch translation with
    construction-domain awareness, and an in-memory LRU cache.
    """

    def __init__(
        self,
        llm_gateway: Any | None = None,
        cache_maxlen: int = 5000,
    ):
        self._cache = _LRUCache(maxlen=cache_maxlen)
        self._gateway = llm_gateway

    async def _get_gateway(self) -> Any:
        """Lazily obtain the LLM gateway singleton."""
        if self._gateway is not None:
            return self._gateway
        from app.services.reliability.llm_gateway import get_llm_gateway

        self._gateway = await get_llm_gateway()
        return self._gateway

    # ------------------------------------------------------------------
    # Language detection
    # ------------------------------------------------------------------

    async def detect_language(self, text: str) -> str:
        """Detect the language of *text* using the LLM.

        Returns an ISO 639-1 two-letter language code.  Falls back to
        ``"en"`` if detection fails or returns an unsupported code.
        """
        if not text or not text.strip():
            return "en"

        sanitized = sanitize_for_prompt(text[:500])

        gateway = await self._get_gateway()
        messages = [
            {"role": "system", "content": _DETECT_LANGUAGE_PROMPT},
            {"role": "user", "content": sanitized},
        ]

        try:
            result = await gateway.complete(
                messages=messages,
                agent_name="translation_service",
                task_class="classification",
                max_tokens=10,
                temperature=0.0,
            )
            raw = result.get("content", "").strip().lower()
            # Extract just the 2-letter code from the response
            code = re.sub(r"[^a-z]", "", raw)[:2]
            if code in SUPPORTED_LANGUAGES:
                return code
            # Try to match partial response
            for lang_code in SUPPORTED_LANGUAGES:
                if lang_code in raw:
                    return lang_code
            logger.warning(
                "Language detection returned unsupported code '%s', defaulting to 'en'",
                raw,
            )
            return "en"
        except Exception:
            logger.warning("Language detection failed, defaulting to 'en'", exc_info=True)
            return "en"

    # ------------------------------------------------------------------
    # Single translation
    # ------------------------------------------------------------------

    async def translate(
        self,
        text: str,
        target_lang: str,
        source_lang: str | None = None,
        context: str | None = None,
    ) -> TranslationResult:
        """Translate *text* to *target_lang*.

        Args:
            text: The text to translate (1-5000 chars).
            target_lang: ISO 639-1 target language code.
            source_lang: ISO 639-1 source language code. Auto-detected if None.
            context: Translation context — one of ``safety_alert``, ``daily_log``,
                ``rfi``, ``meeting_minutes``, ``general``, or None.

        Returns:
            A ``TranslationResult`` with the translated text, language codes,
            confidence score, and cache-hit flag.

        Raises:
            ValueError: If *target_lang* or *source_lang* is not in
                ``SUPPORTED_LANGUAGES``.
        """
        if target_lang not in SUPPORTED_LANGUAGES:
            raise ValueError(
                f"Unsupported target language '{target_lang}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_LANGUAGES))}"
            )

        if source_lang is not None and source_lang not in SUPPORTED_LANGUAGES:
            raise ValueError(
                f"Unsupported source language '{source_lang}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_LANGUAGES))}"
            )

        # Auto-detect source language if not provided
        if source_lang is None:
            source_lang = await self.detect_language(text)

        # No-op if source and target are the same
        if source_lang == target_lang:
            return TranslationResult(
                translated_text=text,
                source_language=source_lang,
                target_language=target_lang,
                confidence=1.0,
                cached=False,
            )

        # Check cache
        cache_key = self._cache_key(text, source_lang, target_lang)
        cached_result = await self._cache.get(cache_key)
        if cached_result is not None:
            return TranslationResult(
                translated_text=cached_result.translated_text,
                source_language=cached_result.source_language,
                target_language=cached_result.target_language,
                confidence=cached_result.confidence,
                cached=True,
            )

        # Build prompt and call LLM
        sanitized_text = sanitize_for_prompt(text)
        system_prompt = self._build_translation_prompt(
            sanitized_text, source_lang, target_lang, context
        )

        gateway = await self._get_gateway()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": sanitized_text},
        ]

        # max_tokens budget: character count is a poor proxy for tokens
        # (especially for languages with multi-byte characters), so floor at
        # 1024 to give the model room for an unstuffed translation. The 4 ×
        # multiplier accounts for translations that expand vs source.
        result = await gateway.complete(
            messages=messages,
            agent_name="translation_service",
            task_class="summarization",
            max_tokens=min(max(len(text) * 4, 1024), 4096),
            temperature=0.1,
        )

        translated = self._parse_translation_response(result.get("content", ""))

        # Estimate confidence based on response length ratio
        confidence = self._estimate_confidence(text, translated, source_lang, target_lang)

        translation_result = TranslationResult(
            translated_text=translated,
            source_language=source_lang,
            target_language=target_lang,
            confidence=confidence,
            cached=False,
        )

        # Cache the result
        await self._cache.put(cache_key, translation_result)

        return translation_result

    # ------------------------------------------------------------------
    # Batch translation
    # ------------------------------------------------------------------

    async def translate_batch(
        self,
        items: list[dict[str, Any]],
        target_lang: str,
    ) -> list[TranslationResult]:
        """Translate a batch of texts in optimized LLM calls.

        Deduplicates identical texts and batches up to 20 items per LLM
        call for efficiency.

        Args:
            items: List of dicts with ``text`` and optional ``context``,
                ``source_lang``, ``reference_id`` keys.
            target_lang: ISO 639-1 target language code.

        Returns:
            List of ``TranslationResult`` in the same order as *items*.
        """
        if target_lang not in SUPPORTED_LANGUAGES:
            raise ValueError(
                f"Unsupported target language '{target_lang}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_LANGUAGES))}"
            )

        if not items:
            return []

        # SV-13 / SV-15: Build a dedup map keyed on (text, context) so that
        # the same text with different contexts (e.g. "safety_alert" vs
        # "general") produces different translations.
        dedup: dict[str, dict[str, Any]] = {}
        for idx, item in enumerate(items):
            text = item.get("text", "")
            context = item.get("context") or "general"
            key = f"{text}\x00{context}"
            if key not in dedup:
                dedup[key] = {
                    "indices": [],
                    "text": text,
                    "context": context,
                    "source_lang": item.get("source_lang"),
                }
            dedup[key]["indices"].append(idx)

        unique_items = list(dedup.values())
        results_map: dict[str, TranslationResult] = {}

        # Process in batches of up to 20
        batch_size = 20
        for batch_start in range(0, len(unique_items), batch_size):
            batch = unique_items[batch_start : batch_start + batch_size]
            batch_results = await self._translate_batch_chunk(batch, target_lang)
            for item_info, result in zip(batch, batch_results, strict=False):
                composite_key = f"{item_info['text']}\x00{item_info.get('context') or 'general'}"
                results_map[composite_key] = result

        # Reconstruct results in original order
        output: list[TranslationResult] = [
            TranslationResult(
                translated_text="",
                source_language="en",
                target_language=target_lang,
                confidence=0.0,
                cached=False,
            )
        ] * len(items)

        for composite_key, result in results_map.items():
            for idx in dedup[composite_key]["indices"]:
                output[idx] = result

        return output

    async def _translate_batch_chunk(
        self,
        batch: list[dict[str, Any]],
        target_lang: str,
    ) -> list[TranslationResult]:
        """Translate a chunk of up to 20 unique texts in a single LLM call."""
        if len(batch) == 1:
            item = batch[0]
            result = await self.translate(
                text=item["text"],
                target_lang=target_lang,
                source_lang=item.get("source_lang"),
                context=item.get("context"),
            )
            return [result]

        # Detect source languages for items that don't have one
        source_langs: list[str] = []
        for item in batch:
            sl = item.get("source_lang")
            if sl is None:
                sl = await self.detect_language(item["text"])
            source_langs.append(sl)

        # Check cache for each item; only call LLM for misses
        cached_results: dict[int, TranslationResult] = {}
        uncached_indices: list[int] = []
        for i, item in enumerate(batch):
            if source_langs[i] == target_lang:
                cached_results[i] = TranslationResult(
                    translated_text=item["text"],
                    source_language=source_langs[i],
                    target_language=target_lang,
                    confidence=1.0,
                    cached=False,
                )
                continue
            cache_key = self._cache_key(item["text"], source_langs[i], target_lang)
            cached = await self._cache.get(cache_key)
            if cached is not None:
                cached_results[i] = TranslationResult(
                    translated_text=cached.translated_text,
                    source_language=cached.source_language,
                    target_language=cached.target_language,
                    confidence=cached.confidence,
                    cached=True,
                )
            else:
                uncached_indices.append(i)

        if not uncached_indices:
            return [cached_results[i] for i in range(len(batch))]

        # Build a combined prompt for uncached items
        target_lang_name = SUPPORTED_LANGUAGES.get(target_lang, target_lang)
        system_prompt = (
            "You are a professional translator specializing in construction "
            "industry communication. Translate each numbered text below to "
            f"{target_lang_name}.\n\n"
            "CRITICAL RULES:\n"
            "1. Preserve ALL technical codes exactly as-is: CSI MasterFormat codes, "
            "OSHA references, rebar designations, and specification section numbers.\n"
            "2. Preserve ALL measurements and units exactly as-is.\n"
            "3. Preserve proper nouns, project names, and company names.\n"
            "4. Output ONLY numbered translations in the format:\n"
            "[1] translated text\n"
            "[2] translated text\n"
            "Do not add any explanation or commentary.\n"
        )

        user_lines: list[str] = []
        for seq, idx in enumerate(uncached_indices, 1):
            sanitized = sanitize_for_prompt(batch[idx]["text"])
            user_lines.append(f"[{seq}] {sanitized}")

        gateway = await self._get_gateway()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "\n".join(user_lines)},
        ]

        try:
            gw_result = await gateway.complete(
                messages=messages,
                agent_name="translation_service",
                task_class="summarization",
                max_tokens=min(sum(len(batch[i]["text"]) for i in uncached_indices) * 3, 4096),
                temperature=0.1,
            )
            raw_content = gw_result.get("content", "")
            parsed = self._parse_batch_response(raw_content, len(uncached_indices))
        except Exception:
            logger.warning("Batch translation failed, falling back to individual", exc_info=True)
            # Fallback: translate individually
            parsed = []
            for idx in uncached_indices:
                try:
                    single = await self.translate(
                        text=batch[idx]["text"],
                        target_lang=target_lang,
                        source_lang=source_langs[idx],
                        context=batch[idx].get("context"),
                    )
                    parsed.append(single.translated_text)
                except Exception:
                    parsed.append(batch[idx]["text"])

        # Assemble results for uncached items
        for seq, idx in enumerate(uncached_indices):
            translated_text = parsed[seq] if seq < len(parsed) else batch[idx]["text"]
            confidence = self._estimate_confidence(
                batch[idx]["text"], translated_text, source_langs[idx], target_lang
            )
            tr = TranslationResult(
                translated_text=translated_text,
                source_language=source_langs[idx],
                target_language=target_lang,
                confidence=confidence,
                cached=False,
            )
            cache_key = self._cache_key(batch[idx]["text"], source_langs[idx], target_lang)
            await self._cache.put(cache_key, tr)
            cached_results[idx] = tr

        return [cached_results[i] for i in range(len(batch))]

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    def _build_translation_prompt(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        context: str | None,
    ) -> str:
        """Build the system prompt for a single translation.

        The prompt varies by *context* to optimize tone and terminology
        preservation for different construction document types.
        """
        source_lang_name = SUPPORTED_LANGUAGES.get(source_lang, source_lang)
        target_lang_name = SUPPORTED_LANGUAGES.get(target_lang, target_lang)

        prompt = _BASE_SYSTEM_PROMPT.format(
            source_lang_name=source_lang_name,
            target_lang_name=target_lang_name,
        )

        # Add context-specific instructions
        ctx = context or "general"
        context_prompt = _CONTEXT_PROMPTS.get(ctx, "")
        if context_prompt:
            prompt += "\n" + context_prompt

        return prompt

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_translation_response(self, response_text: str) -> str:
        """Extract just the translation from an LLM response.

        Strips any preamble, explanation, or quotation marks the LLM
        may have added around the translation.
        """
        if not response_text:
            return ""

        text = response_text.strip()

        # Strip common LLM preambles
        preamble_patterns = [
            r"^(?:Here(?:'s| is) the translation[:\s]*)",
            r"^(?:Translation[:\s]*)",
            r"^(?:Translated text[:\s]*)",
            r"^(?:The translation (?:is|reads)[:\s]*)",
        ]
        for pattern in preamble_patterns:
            text = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()

        # Strip wrapping quotes if the entire response is quoted
        if len(text) >= 2:
            if (text[0] == '"' and text[-1] == '"') or (text[0] == "'" and text[-1] == "'"):
                inner = text[1:-1].strip()
                if inner:
                    text = inner

        # Strip trailing explanation after double newline
        double_nl = text.find("\n\n")
        if double_nl > 0:
            # Only strip if the remainder looks like an explanation
            remainder = text[double_nl + 2 :].strip().lower()
            explanation_markers = ["note:", "note that", "explanation:", "i ", "the above", "this "]
            if any(remainder.startswith(m) for m in explanation_markers):
                text = text[:double_nl].strip()

        return text

    def _parse_batch_response(self, response_text: str, expected_count: int) -> list[str]:
        """Parse a numbered batch translation response.

        Expects format like:
            [1] translated text one
            [2] translated text two
        """
        if not response_text:
            return [""] * expected_count

        results: list[str] = []

        # Try to parse numbered format [N] text
        pattern = re.compile(r"\[(\d+)\]\s*(.*?)(?=\n\[\d+\]|\Z)", re.DOTALL)
        matches = pattern.findall(response_text)

        if matches:
            # Sort by number and extract texts
            numbered: dict[int, str] = {}
            for num_str, text in matches:
                num = int(num_str)
                numbered[num] = text.strip()
            for i in range(1, expected_count + 1):
                results.append(numbered.get(i, ""))
        else:
            # Fallback: split by newlines and take first N non-empty lines
            lines = [ln.strip() for ln in response_text.strip().split("\n") if ln.strip()]
            for i in range(expected_count):
                if i < len(lines):
                    # Strip any leading number/bullet
                    line = re.sub(r"^\d+[\.\)]\s*", "", lines[i])
                    results.append(line)
                else:
                    results.append("")

        return results

    # ------------------------------------------------------------------
    # Cache key generation
    # ------------------------------------------------------------------

    def _cache_key(self, text: str, source_lang: str, target_lang: str) -> str:
        """Generate a deterministic cache key using SHA-256.

        The key is based on the text content and the source/target
        language pair, ensuring that the same text translated between
        different language pairs gets different cache entries.
        """
        raw = f"{source_lang}:{target_lang}:{text}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # Confidence estimation
    # ------------------------------------------------------------------

    def _estimate_confidence(
        self,
        source_text: str,
        translated_text: str,
        source_lang: str,
        target_lang: str,
    ) -> float:
        """Estimate translation confidence based on heuristics.

        Factors:
        - Length ratio: CJK translations tend to be shorter; Latin-script
          translations should be roughly similar length.
        - Preservation of technical terms (CSI codes, OSHA refs, measurements).
        - Non-empty response.
        """
        if not translated_text:
            return 0.0

        confidence = 0.85  # Base confidence for a successful LLM response

        # Length ratio check
        src_len = len(source_text)
        tgt_len = len(translated_text)

        if src_len > 0:
            ratio = tgt_len / src_len
            # CJK languages tend to produce shorter text
            if target_lang in ("zh", "ko", "vi"):
                if 0.2 <= ratio <= 2.0:
                    confidence += 0.05
            else:
                if 0.4 <= ratio <= 2.5:
                    confidence += 0.05

        # Check preservation of technical terms
        source_csi = set(_CSI_CODE_RE.findall(source_text))
        target_csi = set(_CSI_CODE_RE.findall(translated_text))
        if source_csi and source_csi.issubset(target_csi):
            confidence += 0.05

        source_osha = set(_OSHA_REF_RE.findall(source_text))
        target_osha = set(_OSHA_REF_RE.findall(translated_text))
        if source_osha and source_osha.issubset(target_osha):
            confidence += 0.05

        return min(confidence, 1.0)


# ---------------------------------------------------------------------------
# Module-level singleton accessor
# ---------------------------------------------------------------------------

_service_instance: TranslationService | None = None


def get_translation_service(llm_gateway: Any | None = None) -> TranslationService:
    """Return a shared ``TranslationService`` singleton.

    If *llm_gateway* is provided, it is used for the new instance;
    otherwise the gateway is lazily loaded from the default singleton.
    """
    global _service_instance
    if _service_instance is None:
        _service_instance = TranslationService(llm_gateway=llm_gateway)
    return _service_instance
