"""Document classifier using LLM to categorize construction documents."""

from __future__ import annotations

import json
import logging

from langchain_openai import ChatOpenAI

from app.utils.prompt_sanitizer import sanitize_for_prompt

logger = logging.getLogger(__name__)

CLASSIFICATION_PROMPT = """\
You are an expert construction document classifier. Analyze the following document \
text sample and filename, then classify the document.

**Filename:** {filename}

<user_document>
{text_sample}
</user_document>

Classify the document into exactly ONE of these types:
- specification
- drawing
- contract
- rfi
- submittal
- daily_log
- meeting_minutes
- change_order
- schedule
- bim_model
- other

Also identify:
- **csi_division**: The CSI MasterFormat division (e.g., "03 - Concrete", \
"09 - Finishes"). Use null if not applicable.
- **discipline**: The engineering/construction discipline (e.g., "structural", \
"mechanical", "electrical", "architectural", "civil", "plumbing"). Use null if unclear.
- **confidence**: A float between 0.0 and 1.0 indicating your confidence.

Respond ONLY with valid JSON in this exact format:
{{
  "classified_type": "<type>",
  "csi_division": "<division or null>",
  "discipline": "<discipline or null>",
  "confidence": <float>
}}
"""


async def classify_document(text_sample: str, filename: str) -> dict:
    """Classify a construction document using an LLM.

    Args:
        text_sample: A representative text excerpt from the document.
        filename: The original filename of the document.

    Returns:
        A dict with keys: classified_type, csi_division, discipline,
        confidence, model_used.
    """
    model_name = "gpt-4o-mini"
    llm = ChatOpenAI(model_name=model_name, temperature=0)

    # Sanitize user content to prevent prompt injection
    sanitized_text = sanitize_for_prompt(text_sample, max_length=4000)
    sanitized_filename = sanitize_for_prompt(filename, max_length=255)
    prompt = CLASSIFICATION_PROMPT.format(
        filename=sanitized_filename,
        text_sample=sanitized_text,
    )

    try:
        response = await llm.ainvoke(prompt)
        content = (
            response.content if isinstance(response.content, str) else str(response.content)
        ).strip()

        # Strip markdown code fences if present
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

        result = json.loads(content)

        return {
            "classified_type": result.get("classified_type", "other"),
            "csi_division": result.get("csi_division"),
            "discipline": result.get("discipline"),
            # Clamp LLM confidence to [0.0, 0.95] — never fully trust model self-scores
            "confidence": max(0.0, min(0.95, float(result.get("confidence", 0.0)))),
            "model_used": model_name,
        }
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse LLM classification response: %s", exc)
        return {
            "classified_type": "other",
            "csi_division": None,
            "discipline": None,
            "confidence": 0.0,
            "model_used": model_name,
        }
    except Exception as exc:
        logger.error("Document classification failed: %s", exc)
        return {
            "classified_type": "other",
            "csi_division": None,
            "discipline": None,
            "confidence": 0.0,
            "model_used": model_name,
        }
