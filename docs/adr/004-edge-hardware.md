# ADR-004: NVIDIA Jetson Orin for Edge Deployment

## Status
Accepted

## Date
2025-08-01

## Context
Construction sites require real-time safety monitoring with low-latency inference
(<50ms per frame). Internet connectivity is unreliable on remote sites. Edge compute
must handle multiple camera streams simultaneously while operating in harsh conditions.

## Decision
Deploy NVIDIA Jetson Orin Nano (8GB) as the edge compute platform:
- **TensorRT** for optimized model inference
- **DeepStream** for multi-stream video pipeline
- **MQTT** for lightweight telemetry communication
- **Offline buffer** with CRDT-based sync for connectivity gaps

## Consequences
- Sub-50ms inference latency for safety-critical detections
- Operates fully offline with local model execution
- Supports up to 8 concurrent camera streams at 15fps
- MQTT enables efficient communication with cloud backend
- Trade-off: Limited compute restricts model size and complexity

## Alternatives Considered
- **Intel NUC with GPU**: More powerful but higher cost and power consumption
- **Google Coral**: Lower cost but insufficient for multi-stream processing
- **Cloud-only processing**: Lower hardware cost but unacceptable latency and connectivity risk
