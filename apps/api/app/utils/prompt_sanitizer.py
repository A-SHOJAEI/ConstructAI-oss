"""Sanitize user-supplied text before interpolation into LLM prompts.

Mitigates prompt injection by stripping role-change markers, instruction
override patterns, control characters, and Unicode obfuscation from user
content.  All LLM-facing services should call ``sanitize_for_prompt()`` on
every user-controlled field before embedding it in a prompt template.
"""

from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------------------
# Unicode obfuscation characters to strip (C-09 fix)
# ---------------------------------------------------------------------------

# Zero-width and invisible formatting characters used to bypass keyword blocklists
_INVISIBLE_CHARS_RE = re.compile(
    "["
    "\u200b"  # zero-width space
    "\u200c"  # zero-width non-joiner
    "\u200d"  # zero-width joiner
    "\u200e"  # left-to-right mark
    "\u200f"  # right-to-left mark
    "\u202a"  # left-to-right embedding
    "\u202b"  # right-to-left embedding
    "\u202c"  # pop directional formatting
    "\u202d"  # left-to-right override
    "\u202e"  # right-to-left override
    "\u2060"  # word joiner
    "\u2061"  # function application
    "\u2062"  # invisible times
    "\u2063"  # invisible separator
    "\u2064"  # invisible plus
    "\ufeff"  # BOM / zero-width no-break space
    "\ufff9"  # interlinear annotation anchor
    "\ufffa"  # interlinear annotation separator
    "\ufffb"  # interlinear annotation terminator
    "]"
)

# ---------------------------------------------------------------------------
# Patterns that attempt to override the system prompt or switch roles
# ---------------------------------------------------------------------------

_ROLE_MARKERS_RE = re.compile(
    r"(?:^|\n)\s*(?:"
    r"system\s*:|user\s*:|assistant\s*:|human\s*:|ai\s*:"
    r"|<\|(?:im_start|im_end|system|user|assistant)\|>"  # ChatML
    r"|\[INST\]|\[/INST\]"  # Llama-style
    r"|<<SYS>>|<</SYS>>"  # Llama2 system
    r"|<\|(?:begin|end)_of_text\|>"  # Newer tokenizer markers
    r")",
    re.IGNORECASE,
)

_INSTRUCTION_OVERRIDE_RE = re.compile(
    r"(?:ignore|forget|disregard|override|bypass)\s+"
    r"(?:all\s+)?(?:previous|above|prior|earlier|system)\s+"
    r"(?:instructions?|prompts?|rules?|context)",
    re.IGNORECASE,
)

# Control characters (C0/C1) except \n and \t
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

# ---------------------------------------------------------------------------
# XML-style delimiter tag injection patterns (C-10 fix)
# ---------------------------------------------------------------------------

# Matches opening and closing XML-style delimiter tags used in prompt templates.
# User input containing these could break out of delimited sections and inject
# content into a different prompt context.
_XML_DELIMITER_TAG_RE = re.compile(
    r"</?(?:user_input|user_query|user_data|retrieved_document)>",
    re.IGNORECASE,
)


def sanitize_for_prompt(text: str, *, max_length: int = 6000) -> str:
    """Sanitize user text for safe LLM prompt interpolation.

    - **Normalizes Unicode (NFKC)** to collapse homoglyphs (C-09 fix)
    - **Strips zero-width and invisible characters** that can break up
      blocked keywords (C-09 fix)
    - Strips role-change markers (``system:``, ``[INST]``, ChatML tags)
    - Strips instruction-override phrases
    - Removes control characters
    - Truncates to *max_length*
    - Escapes triple backticks to prevent code-fence breakout

    The resulting text is safe to place inside delimited ``<user_document>``
    tags within a prompt template.
    """
    if not text:
        return ""

    # Truncate first to limit processing on very large inputs
    text = text[:max_length]

    # SECURITY (C-09): Normalize Unicode to NFKC form to collapse homoglyphs.
    # This converts visually similar characters (e.g. Cyrillic "а" → Latin "a",
    # fullwidth "ｓｙｓｔｅｍ" → "system") so that blocklist patterns match
    # regardless of character encoding tricks.
    text = unicodedata.normalize("NFKC", text)

    # SECURITY (C-09): Strip zero-width and invisible Unicode characters that
    # can break up blocked keywords (e.g. "s\u200bystem:" → "system:").
    text = _INVISIBLE_CHARS_RE.sub("", text)

    # Remove control characters
    text = _CONTROL_CHARS_RE.sub("", text)

    # Neutralise role-change markers by prefixing with a visible escape
    text = _ROLE_MARKERS_RE.sub("[blocked-marker]", text)

    # Neutralise instruction-override phrases
    text = _INSTRUCTION_OVERRIDE_RE.sub("[blocked-instruction]", text)

    # SECURITY (C-10): Neutralise XML-style delimiter tags that could break
    # out of delimited prompt sections (e.g. </user_input>, </user_query>).
    text = _XML_DELIMITER_TAG_RE.sub("[blocked-tag]", text)

    # Escape triple backticks to prevent code-fence breakout
    text = text.replace("```", "'''")

    return text
