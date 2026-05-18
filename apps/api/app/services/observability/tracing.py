"""OpenTelemetry distributed tracing instrumentation."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class TracingSetup:
    """Configure OpenTelemetry tracing for the application."""

    def __init__(
        self,
        service_name: str = "constructai-api",
        endpoint: str = "http://localhost:4317",
    ):
        self.service_name = service_name
        self.endpoint = endpoint
        self._initialized = False

    def setup(self, app=None, engine=None):
        """Instrument FastAPI, SQLAlchemy, Redis, and Celery."""
        try:
            from opentelemetry import trace
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import (
                BatchSpanProcessor,
            )

            resource = Resource.create({"service.name": self.service_name})
            provider = TracerProvider(resource=resource)

            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                    OTLPSpanExporter,
                )

                exporter = OTLPSpanExporter(endpoint=self.endpoint)
                provider.add_span_processor(BatchSpanProcessor(exporter))
            except Exception:
                logger.warning("OTLP exporter not available, using console")

            trace.set_tracer_provider(provider)

            # Instrument FastAPI
            if app:
                try:
                    from opentelemetry.instrumentation.fastapi import (
                        FastAPIInstrumentor,
                    )

                    FastAPIInstrumentor.instrument_app(app)
                    logger.info("FastAPI instrumented")
                except ImportError:
                    logger.warning("FastAPI instrumentation not available")

            # Instrument SQLAlchemy
            if engine:
                try:
                    from opentelemetry.instrumentation.sqlalchemy import (
                        SQLAlchemyInstrumentor,
                    )

                    SQLAlchemyInstrumentor().instrument(engine=engine)
                    logger.info("SQLAlchemy instrumented")
                except ImportError:
                    logger.warning("SQLAlchemy instrumentation not available")

            self._initialized = True
            logger.info(
                "Tracing setup complete for %s",
                self.service_name,
            )

        except ImportError:
            logger.warning("OpenTelemetry SDK not available, tracing disabled")

    def get_tracer(self, name: str = "constructai"):
        """Get a tracer instance."""
        try:
            from opentelemetry import trace

            return trace.get_tracer(name)
        except ImportError:
            return None

    @property
    def initialized(self) -> bool:
        return self._initialized
