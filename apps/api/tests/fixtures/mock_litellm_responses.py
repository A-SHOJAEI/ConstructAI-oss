"""LiteLLM mock responses for reliability tests."""

from __future__ import annotations

MOCK_COMPLETION_SUCCESS = {
    "content": "The concrete strength requirement is 4000 PSI.",
    "model": "anthropic/claude-sonnet-4-20250514",
    "input_tokens": 150,
    "output_tokens": 25,
}

MOCK_COMPLETION_SECONDARY = {
    "content": "4000 PSI concrete per spec section 03 30 00.",
    "model": "openai/gpt-4o",
    "input_tokens": 150,
    "output_tokens": 20,
}

MOCK_COMPLETION_TERTIARY = {
    "content": "Concrete: 4000 PSI at 28 days.",
    "model": "gemini/gemini-2.0-flash",
    "input_tokens": 150,
    "output_tokens": 15,
}

MOCK_429_ERROR = {
    "status_code": 429,
    "message": "Rate limit exceeded",
    "provider": "anthropic",
}

MOCK_500_ERROR = {
    "status_code": 500,
    "message": "Internal server error",
    "provider": "anthropic",
}

MOCK_TIMEOUT_ERROR = {
    "status_code": 408,
    "message": "Request timeout after 30s",
    "provider": "openai",
}

MOCK_USAGE_RECORDS = [
    {
        "agent_name": "document_agent",
        "provider": "anthropic",
        "model": "anthropic/claude-sonnet-4-20250514",
        "input_tokens": 500,
        "output_tokens": 200,
        "cost_usd": 0.0045,
        "latency_ms": 1200,
        "cached": False,
    },
    {
        "agent_name": "estimating_agent",
        "provider": "anthropic",
        "model": "anthropic/claude-sonnet-4-20250514",
        "input_tokens": 800,
        "output_tokens": 350,
        "cost_usd": 0.0077,
        "latency_ms": 1800,
        "cached": False,
    },
    {
        "agent_name": "document_agent",
        "provider": "cache",
        "model": "cache",
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "latency_ms": 5,
        "cached": True,
    },
]
