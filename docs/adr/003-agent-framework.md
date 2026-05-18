# ADR-003: LangGraph as Agent Framework

## Status
Accepted

## Date
2025-07-15

## Context
ConstructAI needs a framework for building AI agents that support: stateful multi-step
workflows, human-in-the-loop approval, parallel tool execution, and hierarchical team
coordination. The framework must integrate with LangSmith for observability.

## Decision
Use LangGraph for all AI agent implementations:
- **StateGraph** pattern for each of the 11 agents
- **Team supervisors** for coordinating related agents (Planning, Execution, Compliance)
- **Orchestrator agent** wrapping all three team supervisors
- **Checkpointing** via PostgresSaver for workflow state persistence
- **Subgraphs** for the 6-stage guardrails validation pipeline

## Consequences
- Consistent agent architecture across all 11 agents
- Built-in support for conditional routing, parallel execution, and human-in-the-loop
- LangSmith integration provides trace-level observability
- Checkpointing enables workflow recovery after failures
- Trade-off: LangGraph has a learning curve and version churn

## Alternatives Considered
- **CrewAI**: Simpler API but less control over execution flow
- **AutoGen**: Good for multi-agent chat but less suited for structured workflows
- **Custom framework**: Maximum flexibility but high maintenance burden
