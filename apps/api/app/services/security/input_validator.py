"""Input validation and sanitisation utilities."""

from __future__ import annotations

import logging
import re
from typing import ClassVar

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Compiled pattern sets
# ------------------------------------------------------------------ #

# SECURITY [M-10]: Removed character blocklist for ', ", ; that rejected legitimate
# construction input like `8" pipe`, `O'Brien Construction`, `3/4" plywood`.
# The app uses parameterized queries everywhere, so SQL injection via these
# characters is not a risk. We keep keyword-based patterns for defense-in-depth
# but log them as warnings instead of blocking.
_SQL_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)(OR\s+1\s*=\s*1)"),
    re.compile(r"(?i)(UNION\s+SELECT)"),
    re.compile(r"(?i)\b(DROP|ALTER|CREATE|EXEC)\s+(TABLE|DATABASE|INDEX|PROCEDURE)\b"),
]

_XSS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"<script[^>]*>", re.IGNORECASE),
    re.compile(r"javascript:", re.IGNORECASE),
    re.compile(
        r"\bon(click|dblclick|mouse\w+|key\w+|load|unload|error|focus|blur|submit|change|input|select|reset|abort|resize|scroll|contextmenu|drag\w*|drop|copy|cut|paste|before\w+|after\w+)\s*=",
        re.IGNORECASE,
    ),
]

_HTML_TAG_RE = re.compile(r"<[^>]+>")

# Subset of SQL patterns used for search queries (more permissive:
# we allow single quotes and semicolons but still block dangerous
# keywords in suspicious positions).
_SEARCH_DANGEROUS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)(UNION\s+SELECT)"),
    re.compile(r"(?i)(OR\s+1\s*=\s*1)"),
    re.compile(r"(?i)\b(DROP|ALTER|CREATE|EXEC|INSERT|DELETE)\b"),
    re.compile(r"(--|/\*|\*/)"),
]


class InputValidator:
    """Validate and sanitize user inputs."""

    SQL_INJECTION_PATTERNS: ClassVar[list[str]] = [p.pattern for p in _SQL_INJECTION_PATTERNS]
    XSS_PATTERNS: ClassVar[list[str]] = [p.pattern for p in _XSS_PATTERNS]

    # -------------------------------------------------------------- #
    # Text validation
    # -------------------------------------------------------------- #

    def validate_text_input(
        self,
        text: str,
        field_name: str = "",
    ) -> tuple[bool, str]:
        """Validate text input for injection attempts.

        Returns ``(is_safe, sanitized_text)``.  When the input is
        deemed unsafe the *sanitized_text* value is an empty string.
        """
        if not text:
            return True, text

        # SECURITY [M-10]: SQL injection patterns are logged but not blocked,
        # since the app uses parameterized queries everywhere. Blocking these
        # patterns rejected legitimate construction input (measurements, names).
        for pattern in _SQL_INJECTION_PATTERNS:
            if pattern.search(text):
                logger.warning(
                    "Suspicious SQL pattern in field '%s': matched %s (allowed — parameterized queries in use)",
                    field_name or "<unknown>",
                    pattern.pattern,
                )

        # Check XSS patterns — these are still blocked since HTML injection is
        # possible regardless of parameterized queries
        for pattern in _XSS_PATTERNS:
            if pattern.search(text):
                logger.warning(
                    "XSS pattern detected in field '%s': matched %s",
                    field_name or "<unknown>",
                    pattern.pattern,
                )
                return False, ""

        # Strip any residual HTML tags as a safety measure
        sanitized = self.sanitize_html(text)

        return True, sanitized

    # -------------------------------------------------------------- #
    # HTML sanitisation
    # -------------------------------------------------------------- #

    def sanitize_html(self, text: str) -> str:
        """Remove all HTML tags from *text*."""
        return _HTML_TAG_RE.sub("", text)

    # -------------------------------------------------------------- #
    # Search query validation
    # -------------------------------------------------------------- #

    def validate_search_query(
        self,
        query: str,
    ) -> tuple[bool, str]:
        """Validate and sanitize a search query.

        Search queries are more permissive than free-text fields
        but still reject obvious injection attempts such as
        ``UNION SELECT`` or ``DROP TABLE``.

        Returns ``(is_safe, sanitized_query)``.
        """
        if not query:
            return True, query

        for pattern in _SEARCH_DANGEROUS_PATTERNS:
            if pattern.search(query):
                logger.warning(
                    "Dangerous pattern in search query: matched %s",
                    pattern.pattern,
                )
                return False, ""

        # Check XSS in search queries as well
        for pattern in _XSS_PATTERNS:
            if pattern.search(query):
                logger.warning(
                    "XSS pattern in search query: matched %s",
                    pattern.pattern,
                )
                return False, ""

        # Strip HTML tags but keep the rest
        sanitized = self.sanitize_html(query)

        return True, sanitized
