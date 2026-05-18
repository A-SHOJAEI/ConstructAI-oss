# ADR-002: LLM and Vision Model Selection

## Status
Accepted

## Date
2025-07-01

## Context
ConstructAI uses multiple AI models for different tasks: document understanding, cost
estimation reasoning, safety detection, and activity recognition. Model selection must
balance accuracy, latency, cost, and deployment flexibility (cloud vs edge).

## Decision
Use a multi-model strategy with LiteLLM gateway:
- **Primary LLM**: Claude Sonnet (via Anthropic API) for reasoning tasks
- **Fallback chain**: Claude -> GPT-4o -> Gemini Flash -> local Llama 3.2
- **Vision models**: YOLOv8/v11 for real-time detection, ViT-B/16 for classification
- **Embeddings**: Voyage AI for document embeddings, BGE-M3 as fallback
- **Edge models**: TensorRT-optimized models for Jetson Orin deployment

## Consequences
- LiteLLM abstracts provider differences, enabling seamless fallback
- Circuit breakers prevent cascade failures when a provider is down
- Edge models enable offline operation for safety-critical detection
- Cost tracking per-agent enables budget management
- Trade-off: Multiple model providers increase vendor dependency surface

## Alternatives Considered
- **Single provider (OpenAI only)**: Simpler but single point of failure
- **Self-hosted LLMs only**: Lower cost but significantly lower quality for reasoning
- **Google Vertex AI**: Good ecosystem but less flexibility in model mixing
