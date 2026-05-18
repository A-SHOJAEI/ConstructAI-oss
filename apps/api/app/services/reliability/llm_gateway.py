"""LiteLLM-based AI gateway with fallback chains."""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import time
from datetime import UTC, date, datetime, timedelta
from datetime import time as dt_time
from typing import Any

logger = logging.getLogger(__name__)

# Default timeout for individual LLM API calls (seconds).
DEFAULT_LLM_TIMEOUT_SECONDS = 30

# Default max tokens per request to prevent runaway cost.
DEFAULT_MAX_TOKENS_PER_REQUEST = 4096

# Default per-request cost limit in USD.
DEFAULT_MAX_COST_PER_REQUEST = 1.00

# Maximum number of usage records to keep in memory.
_USAGE_LOG_MAXLEN = 10_000

# PI-09: Default maximum concurrent LLM API calls to prevent overwhelming
# provider rate limits during high-concurrency periods.
DEFAULT_MAX_CONCURRENT_LLM_CALLS = 10

# ---------------------------------------------------------------------------
# Model configuration from environment
# ---------------------------------------------------------------------------


def _load_fallback_models() -> list[dict]:
    """Load fallback model chain — local-only (Spark 1 vLLM + Spark 2 Ollama).

    Per Spark-2 scaffold: cloud providers are disabled. To re-enable cloud
    fallback, set ``LLM_LEGACY_CLOUD_FALLBACK=1`` and the previous chain
    (anthropic, openai, gemini, ollama-llama3) will be restored.
    """
    if os.environ.get("LLM_LEGACY_CLOUD_FALLBACK"):
        return [
            {"name": "anthropic", "model": "anthropic/claude-sonnet-4-20250514", "priority": 1},
            {"name": "openai", "model": "openai/gpt-4o", "priority": 2},
            {"name": "gemini", "model": "gemini/gemini-2.0-flash", "priority": 3},
            {"name": "ollama-legacy", "model": "ollama/llama3.2:3b", "priority": 4},
        ]

    try:
        from app.config import settings

        vllm_base = settings.LOCAL_VLLM_BASE_URL or "http://spark1:8000/v1"
        vllm_key = settings.LOCAL_VLLM_API_KEY or "vllm"
        vllm_model = settings.LOCAL_VLLM_MODEL_NAME or "constructai-primary"
        ollama_base = settings.LOCAL_OLLAMA_BASE_URL or "http://localhost:11434/v1"
        ollama_model = settings.LOCAL_OLLAMA_MODEL_NAME or "gpt-oss:20b"
    except Exception:
        vllm_base = os.environ.get("LOCAL_VLLM_BASE_URL", "http://spark1:8000/v1")
        vllm_key = os.environ.get("LOCAL_VLLM_API_KEY", "vllm")
        vllm_model = os.environ.get("LOCAL_VLLM_MODEL_NAME", "constructai-primary")
        ollama_base = os.environ.get("LOCAL_OLLAMA_BASE_URL", "http://localhost:11434/v1")
        ollama_model = os.environ.get("LOCAL_OLLAMA_MODEL_NAME", "gpt-oss:20b")

    return [
        {
            "name": "local-vllm-spark1-120b",
            "model": f"openai/{vllm_model}",
            "api_base": vllm_base,
            "api_key": vllm_key,
            "task_classes": ("reasoning", "default"),
            "priority": 1,
        },
        {
            "name": "local-ollama-spark2-fast",
            "model": f"openai/{ollama_model}",
            "api_base": ollama_base,
            "api_key": "ollama",
            "task_classes": ("fast", "classification", "summarization"),
            "priority": 2,
        },
    ]


DEFAULT_FALLBACK_MODELS = _load_fallback_models()


# ---------------------------------------------------------------------------
# Task-class routing — select preferred provider by task semantics
# ---------------------------------------------------------------------------
#
# Callers can pass ``task_class="<class>"`` to ``complete()`` to bias routing
# toward the right model. If the routed provider's circuit breaker is open
# (or it errors), the gateway falls through to the next model in priority
# order — so this is a hint, not a hard pin.
TASK_CLASS_ROUTE: dict[str, str] = {
    "reasoning": "local-vllm-spark1-120b",
    "fast": "local-ollama-spark2-fast",
    "classification": "local-ollama-spark2-fast",
    "summarization": "local-ollama-spark2-fast",
    "default": "local-vllm-spark1-120b",
}

# ---------------------------------------------------------------------------
# Pricing configuration
# ---------------------------------------------------------------------------

# Default pricing: model -> (input_rate_per_1k, output_rate_per_1k)
# Local models are free at the API layer; budget tracking still records
# token counts so abuse / runaway loops are visible.
_DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    "openai/constructai-primary": (0.0, 0.0),
    "openai/gpt-oss:20b": (0.0, 0.0),
    "anthropic/claude-sonnet-4-20250514": (0.003, 0.015),
    "openai/gpt-4o": (0.005, 0.015),
    "gemini/gemini-2.0-flash": (0.0001, 0.0004),
    "ollama/llama3.2:3b": (0.0, 0.0),
}


def _load_pricing() -> dict[str, tuple[float, float]]:
    """Load pricing from ``LLM_PRICING_JSON`` env var or use defaults.

    The env var should contain a JSON object mapping model IDs to
    ``[input_rate, output_rate]`` arrays, e.g.::

        {"openai/gpt-4o": [0.005, 0.015], "anthropic/claude-sonnet-4-20250514": [0.003, 0.015]}
    """
    raw = os.environ.get("LLM_PRICING_JSON")
    if not raw:
        return dict(_DEFAULT_PRICING)
    try:
        parsed = json.loads(raw)
        pricing: dict[str, tuple[float, float]] = {}
        for model_id, rates in parsed.items():
            if isinstance(rates, list | tuple) and len(rates) == 2:
                pricing[model_id] = (float(rates[0]), float(rates[1]))
            else:
                logger.warning(
                    "Invalid pricing entry for %s, expected [input, output]: %s",
                    model_id,
                    rates,
                )
        return pricing
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("Failed to parse LLM_PRICING_JSON, using defaults: %s", exc)
        return dict(_DEFAULT_PRICING)


class LLMGateway:
    """LiteLLM-based AI gateway configuration.

    Fallback chain: Claude -> GPT-4o -> Gemini -> local Llama
    Budget alerting: per-agent and per-org usage tracking
    Semantic caching: Redis-backed cosine threshold 0.90
    """

    def __init__(
        self,
        fallback_models: list[dict] | None = None,
        circuit_breaker_manager: Any = None,
        semantic_cache: Any = None,
        timeout_seconds: int = DEFAULT_LLM_TIMEOUT_SECONDS,
        max_tokens_per_request: int = DEFAULT_MAX_TOKENS_PER_REQUEST,
        max_cost_per_request: float = DEFAULT_MAX_COST_PER_REQUEST,
        max_concurrent_calls: int = DEFAULT_MAX_CONCURRENT_LLM_CALLS,
    ):
        self.fallback_models = sorted(
            fallback_models or DEFAULT_FALLBACK_MODELS,
            key=lambda m: m["priority"],
        )
        self.circuit_breaker = circuit_breaker_manager
        self.cache = semantic_cache
        self.timeout_seconds = timeout_seconds
        self.max_tokens_per_request = max_tokens_per_request
        self.max_cost_per_request = max_cost_per_request

        # PI-09: Semaphore to limit concurrent LLM API calls, preventing
        # overwhelming provider rate limits during high-concurrency periods.
        self._concurrency_semaphore = asyncio.Semaphore(max_concurrent_calls)

        # Bounded deque to prevent unbounded memory growth
        self._usage_log: collections.deque[dict] = collections.deque(
            maxlen=_USAGE_LOG_MAXLEN,
        )

        # Per-org aggregate usage tracking
        self._org_usage: dict[str, dict] = {}

        # Per-org daily token budget tracking: {org_id: {date_str: total_tokens}}
        # In-memory fallback when Redis is unavailable.
        self._org_daily_tokens: dict[str, dict[str, int]] = {}

        # Redis connection for cross-process budget tracking
        self._redis = None

        # Configurable daily token budget per org (default 1M)
        try:
            from app.config import settings

            self._daily_token_budget: int = settings.LLM_DAILY_TOKEN_BUDGET
        except Exception:
            self._daily_token_budget = int(os.environ.get("LLM_DAILY_TOKEN_BUDGET", "1000000"))

        # Load pricing from env or defaults
        self._pricing = _load_pricing()

    async def _get_redis(self):
        """Lazily acquire a Redis connection for budget tracking."""
        if self._redis is not None:
            return self._redis
        try:
            from app.config import settings

            redis_url = getattr(settings, "REDIS_URL", None) or os.environ.get("REDIS_URL")
            if redis_url:
                import redis.asyncio as aioredis

                self._redis = aioredis.from_url(redis_url, decode_responses=True)
                return self._redis
        except Exception as exc:
            logger.warning("Redis connection failed for budget tracking: %s", exc)
        return None

    async def _check_budget_exceeded(self, org_id: str) -> bool:
        """Check whether the org has exceeded its daily token budget.

        Uses Redis when available for cross-process consistency;
        falls back to in-memory tracking.
        """
        try:
            redis = await self._get_redis()
            if redis:
                key = f"llm_budget:{org_id}:{date.today().isoformat()}"
                current = await redis.get(key)
                return int(current or 0) >= self._daily_token_budget
        except Exception:
            logger.warning("Redis unavailable for budget check, using in-memory fallback")

        # In-memory fallback
        today_str = date.today().isoformat()
        org_daily = self._org_daily_tokens.get(org_id, {})
        return org_daily.get(today_str, 0) >= self._daily_token_budget

    async def _record_budget_usage(self, org_id: str, tokens: int) -> None:
        """Record token usage against the daily budget.

        Uses Redis INCRBY with auto-expiry when available;
        falls back to in-memory tracking.
        """
        if not org_id or tokens <= 0:
            return

        try:
            redis = await self._get_redis()
            if redis:
                key = f"llm_budget:{org_id}:{date.today().isoformat()}"
                current = await redis.incrby(key, tokens)
                if current == tokens:  # First increment today
                    tomorrow = datetime.combine(date.today() + timedelta(days=1), dt_time.min)
                    await redis.expireat(key, tomorrow)
                return
        except Exception:
            logger.warning("Redis unavailable for budget tracking, using in-memory fallback")

        # In-memory fallback
        today_str = date.today().isoformat()
        org_daily = self._org_daily_tokens.setdefault(org_id, {})
        org_daily[today_str] = org_daily.get(today_str, 0) + tokens

    async def complete(
        self,
        messages: list[dict],
        agent_name: str,
        org_id: str | None = None,
        task_class: str | None = None,
        **kwargs,
    ) -> dict:
        """Route completion through fallback chain.

        Tries each model in priority order. Records usage.

        Args:
            messages: List of message dicts with ``role`` and ``content``.
            agent_name: Identifier for the calling agent (for tracking).
            org_id: Optional organization ID for per-org usage tracking.
            task_class: Optional routing hint — one of ``reasoning``,
                ``fast``, ``classification``, ``summarization``, ``default``.
                When set, the matching provider in ``TASK_CLASS_ROUTE`` is
                tried first; on circuit-open or failure, falls through to
                priority order. Treated as a hint, not a hard pin.
            **kwargs: Additional arguments passed to the model call
                (e.g. ``temperature``, ``max_tokens``).

        Returns:
            Dict with ``content``, ``model``, ``input_tokens``,
            ``output_tokens``.
        """
        # Check semantic cache first
        # SECURITY (C-01): Pass org_id to cache for tenant-isolated keys.
        # SECURITY: Skip cache entirely when org_id is None to prevent
        # cross-tenant cache poisoning from unauthenticated requests.
        if self.cache and org_id is not None:
            prompt_text = self._messages_to_text(messages)
            cached = await self.cache.get(
                prompt_text,
                agent_name,
                org_id=org_id,
            )
            if cached:
                self._record_usage(
                    agent_name,
                    "cache",
                    "cache",
                    0,
                    0,
                    0,
                    0,
                    org_id=org_id,
                )
                return cached

        # Per-org daily token budget check (Redis with in-memory fallback)
        if org_id:
            budget_exceeded = await self._check_budget_exceeded(org_id)
            if budget_exceeded:
                # SECURITY [M-27]: Don't expose org_id or exact token counts to callers
                logger.warning(
                    "SECURITY [M-27]: Org %s exceeded daily token budget",
                    org_id,
                )
                raise RuntimeError(
                    "Daily AI usage limit exceeded. Try again tomorrow or contact your administrator."
                )

        # Apply task_class routing hint: try the matching provider first,
        # then fall through the rest in priority order.
        ordered_models = self.fallback_models
        if task_class and task_class in TASK_CLASS_ROUTE:
            preferred_name = TASK_CLASS_ROUTE[task_class]
            preferred = [m for m in self.fallback_models if m.get("name") == preferred_name]
            others = [m for m in self.fallback_models if m.get("name") != preferred_name]
            if preferred:
                ordered_models = preferred + others

        last_error = None
        for model_config in ordered_models:
            model = model_config["model"]
            # Use the explicit "name" for circuit-breaker keying when present
            # (so the vLLM and Ollama providers don't share a breaker just
            # because they both use the openai/ LiteLLM prefix).
            provider = model_config.get("name") or model.split("/")[0]

            # Check circuit breaker
            if self.circuit_breaker:
                breaker = self.circuit_breaker.get_breaker(
                    provider,
                )
                if not await breaker.is_available():
                    logger.warning(
                        "Circuit breaker open for %s",
                        provider,
                    )
                    continue

            try:
                # Enforce max_tokens budget on each request
                if "max_tokens" not in kwargs:
                    kwargs["max_tokens"] = self.max_tokens_per_request

                # Per-provider connection details (for local OpenAI-compat
                # endpoints like vLLM at Spark 1 / Ollama at Spark 2). These
                # are passed through to LiteLLM as ``api_base`` / ``api_key``.
                call_kwargs = dict(kwargs)
                if model_config.get("api_base"):
                    call_kwargs["api_base"] = model_config["api_base"]
                if model_config.get("api_key"):
                    call_kwargs["api_key"] = model_config["api_key"]

                # PI-09: Acquire semaphore to limit concurrent LLM calls
                start = time.monotonic()
                async with self._concurrency_semaphore:
                    result = await asyncio.wait_for(
                        self._call_model(
                            model,
                            messages,
                            **call_kwargs,
                        ),
                        timeout=self.timeout_seconds,
                    )
                latency = int(
                    (time.monotonic() - start) * 1000,
                )

                input_tokens = result.get("input_tokens", 0)
                output_tokens = result.get("output_tokens", 0)
                cost = self._estimate_cost(
                    model,
                    input_tokens,
                    output_tokens,
                )

                self._record_usage(
                    agent_name,
                    provider,
                    model,
                    input_tokens,
                    output_tokens,
                    cost,
                    latency,
                    org_id=org_id,
                )

                # Update daily token budget (Redis or in-memory)
                if org_id:
                    await self._record_budget_usage(org_id, input_tokens + output_tokens)

                # Record success on circuit breaker — the provider responded
                # successfully, so record that before any cost-limit check.
                if self.circuit_breaker:
                    breaker = self.circuit_breaker.get_breaker(
                        provider,
                    )
                    await breaker.record_success()

                # SECURITY [M-13]: Enforce per-request cost limit as a blocking control.
                # Previously this was advisory-only (warning logged but request allowed).
                # Now raises RuntimeError to prevent runaway costs from completing.
                # NOTE: This check is intentionally AFTER breaker.record_success()
                # because the provider did not fail — the cost check failed.
                if cost > self.max_cost_per_request:
                    logger.warning(
                        "SECURITY [M-13]: Request cost $%.4f exceeds limit $%.2f for agent %s on model %s — blocking",
                        cost,
                        self.max_cost_per_request,
                        agent_name,
                        model,
                    )
                    raise RuntimeError("Request blocked: per-request cost limit exceeded.")

                # Cache the result
                # SECURITY (C-01): Pass org_id to cache for tenant-isolated keys.
                # SECURITY: Skip cache when org_id is None to prevent cross-tenant leakage.
                if self.cache and org_id is not None:
                    prompt_text = self._messages_to_text(messages)
                    await self.cache.set(
                        prompt_text,
                        result,
                        agent_name,
                        org_id=org_id,
                    )

                return result

            except RuntimeError:
                # Re-raise RuntimeError (e.g., cost-limit exceeded) without
                # recording a circuit breaker failure — the provider succeeded.
                raise

            except Exception as e:
                last_error = e
                logger.warning(
                    "Model %s failed: %s",
                    model,
                    str(e),
                )
                if self.circuit_breaker:
                    breaker = self.circuit_breaker.get_breaker(
                        provider,
                    )
                    await breaker.record_failure()
                continue

        # SECURITY [M-27]: Log full error server-side, raise generic message
        logger.error("All models in fallback chain failed. Last error: %s", last_error)
        raise RuntimeError(
            "AI service is temporarily unavailable. All models in the fallback chain failed."
        )

    async def _call_model(
        self,
        model: str,
        messages: list[dict],
        **kwargs,
    ) -> dict:
        """Call a model via LiteLLM.

        In production, uses litellm.acompletion().
        For testing, this is mocked.
        """
        try:
            import litellm

            response = await litellm.acompletion(
                model=model,
                messages=messages,
                **kwargs,
            )
            msg = response.choices[0].message
            content = msg.content
            # gpt-oss / o1-style reasoning models emit the final answer in
            # ``content`` and the chain-of-thought in ``reasoning_content``.
            # When the model emits only thinking tokens without a final
            # answer, ``content`` can be None / empty — surface the reasoning
            # text so callers don't get an empty string.
            if not content:
                reasoning = getattr(msg, "reasoning_content", None) or getattr(
                    msg, "reasoning", None
                )
                if reasoning:
                    content = reasoning
            return {
                "content": content or "",
                "model": model,
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": (response.usage.completion_tokens),
            }
        except ImportError as exc:
            raise RuntimeError(f"litellm not available for model {model}") from exc

    def _messages_to_text(self, messages: list[dict]) -> str:
        """Convert messages to text for caching."""
        return " ".join(m.get("content", "") for m in messages)

    def _estimate_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """Estimate cost in USD using configurable pricing."""
        input_rate, output_rate = self._pricing.get(
            model,
            (0.001, 0.002),
        )
        return input_tokens * input_rate / 1000 + output_tokens * output_rate / 1000

    def _record_usage(
        self,
        agent_name: str,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost: float,
        latency_ms: int,
        org_id: str | None = None,
    ):
        """Record usage for tracking.

        Appends to the bounded deque (oldest entries are evicted when
        the deque reaches ``_USAGE_LOG_MAXLEN``).  Also updates per-org
        aggregate counters when ``org_id`` is provided.
        """
        record = {
            "agent_name": agent_name,
            "provider": provider,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost,
            "latency_ms": latency_ms,
            "cached": provider == "cache",
            "timestamp": time.time(),
        }
        if org_id:
            record["org_id"] = org_id
        self._usage_log.append(record)

        # Update per-org daily token tracking
        if org_id:
            today_str = datetime.now(UTC).strftime("%Y-%m-%d")
            org_daily = self._org_daily_tokens.setdefault(org_id, {})
            org_daily[today_str] = org_daily.get(today_str, 0) + input_tokens + output_tokens
            # Clean up old dates to prevent memory growth
            old_dates = [d for d in org_daily if d < today_str]
            for d in old_dates:
                del org_daily[d]

        # Update per-org aggregates
        if org_id:
            if org_id not in self._org_usage:
                self._org_usage[org_id] = {
                    "total_requests": 0,
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                    "total_cost_usd": 0.0,
                    "cache_hits": 0,
                    "by_model": {},
                }
            org = self._org_usage[org_id]
            org["total_requests"] += 1
            org["total_input_tokens"] += input_tokens
            org["total_output_tokens"] += output_tokens
            org["total_cost_usd"] += cost
            if provider == "cache":
                org["cache_hits"] += 1
            # Track per-model breakdown within the org
            if model not in org["by_model"]:
                org["by_model"][model] = {
                    "requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_usd": 0.0,
                }
            org["by_model"][model]["requests"] += 1
            org["by_model"][model]["input_tokens"] += input_tokens
            org["by_model"][model]["output_tokens"] += output_tokens
            org["by_model"][model]["cost_usd"] += cost

    def get_usage_log(self) -> list[dict]:
        """Get all usage records (from bounded deque)."""
        return list(self._usage_log)

    def get_usage_summary(
        self,
        agent_name: str | None = None,
    ) -> dict:
        """Get aggregate usage summary, optionally filtered by agent.

        Returns total requests, tokens, cost, cache hits, and a
        per-model breakdown.
        """
        records = list(self._usage_log)
        if agent_name:
            records = [r for r in records if r["agent_name"] == agent_name]

        # Per-model breakdown
        by_model: dict[str, dict] = {}
        for r in records:
            model = r["model"]
            if model not in by_model:
                by_model[model] = {
                    "requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_usd": 0.0,
                }
            by_model[model]["requests"] += 1
            by_model[model]["input_tokens"] += r["input_tokens"]
            by_model[model]["output_tokens"] += r["output_tokens"]
            by_model[model]["cost_usd"] += r["cost_usd"]

        return {
            "total_requests": len(records),
            "total_cost_usd": sum(r["cost_usd"] for r in records),
            "total_input_tokens": sum(r["input_tokens"] for r in records),
            "total_output_tokens": sum(r["output_tokens"] for r in records),
            "cache_hits": sum(1 for r in records if r["cached"]),
            "by_model": by_model,
        }

    def get_org_usage(self, org_id: str) -> dict:
        """Get aggregate usage for a specific organization.

        Args:
            org_id: The organization identifier.

        Returns:
            Dict with ``total_requests``, ``total_input_tokens``,
            ``total_output_tokens``, ``total_cost_usd``, ``cache_hits``,
            and ``by_model`` breakdown.  Returns zeroed dict if the
            org has no recorded usage.
        """
        return self._org_usage.get(
            org_id,
            {
                "total_requests": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_cost_usd": 0.0,
                "cache_hits": 0,
                "by_model": {},
            },
        )

    def get_all_org_usage(self) -> dict[str, dict]:
        """Get usage summaries for all tracked organizations.

        Returns:
            Dict mapping org_id to their aggregate usage dicts.
        """
        return dict(self._org_usage)


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_default_gateway: LLMGateway | None = None
_gateway_lock = asyncio.Lock()


async def get_llm_gateway() -> LLMGateway:
    """Return the shared LLMGateway singleton.

    Creates a single instance on first access so that circuit-breaker state,
    usage tracking, and semantic caching persist across all callers.
    """
    global _default_gateway
    if _default_gateway is None:
        async with _gateway_lock:
            if _default_gateway is None:
                _default_gateway = LLMGateway()
    return _default_gateway
