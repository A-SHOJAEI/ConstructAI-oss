"""Shared LangGraph invocation config builder.

M-19 / M-20: Previously every agent built its own ``config`` dict with just
``{"configurable": {"thread_id": ...}}``. That left two things unbounded:

1. **Recursion limit.** LangGraph's default (25) is conservative but has
   bitten us before when a node accidentally re-entered itself. Pinning a
   small explicit value here makes runaway loops fail fast instead of
   eating a 5-minute timeout.
2. **Correlation ID propagation.** The event router generates a
   ``correlation_id`` per workflow, but when the agent invoked its graph
   that ID was dropped — making it impossible to stitch a multi-stage run
   together in logs.

Use ``make_agent_config(thread_id, correlation_id=..., recursion_limit=...)``
instead of hand-rolling the dict.
"""

from __future__ import annotations

from typing import Any, cast

from langchain_core.runnables import RunnableConfig

AGENT_DEFAULT_RECURSION_LIMIT = 50


def make_agent_config(
    thread_id: str,
    *,
    correlation_id: str | None = None,
    recursion_limit: int = AGENT_DEFAULT_RECURSION_LIMIT,
    extra_configurable: dict[str, Any] | None = None,
) -> RunnableConfig:
    """Build the ``config`` dict for ``graph.ainvoke(state, config=config)``.

    ``recursion_limit`` is a *top-level* key (not inside ``configurable``);
    LangGraph honors it to cap node-to-node transitions and avoid runaway
    loops.
    """
    configurable: dict[str, Any] = {"thread_id": thread_id}
    if correlation_id:
        configurable["correlation_id"] = correlation_id
    if extra_configurable:
        configurable.update(extra_configurable)
    return cast(
        RunnableConfig,
        {
            "configurable": configurable,
            "recursion_limit": recursion_limit,
        },
    )
