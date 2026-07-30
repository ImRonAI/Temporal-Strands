"""Optional OpenTelemetry wiring shared by worker-side processes."""

import logging
import os
from typing import Sequence

logger = logging.getLogger(__name__)

_configured = False


def telemetry_plugins() -> Sequence[object]:
    """Return the Temporal OpenTelemetry plugin when OTLP is configured."""
    global _configured

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return []

    try:
        from temporalio.contrib.opentelemetry import (
            OpenTelemetryPlugin,
            create_tracer_provider,
        )
    except ImportError:
        logger.warning(
            "OTEL_EXPORTER_OTLP_ENDPOINT is set but Temporal OpenTelemetry "
            "support is unavailable; continuing without tracing."
        )
        return []

    if not _configured:
        try:
            import opentelemetry.trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider = create_tracer_provider(
                resource=Resource.create(
                    {
                        "service.name": os.getenv(
                            "OTEL_SERVICE_NAME", "perplexity-orchestrator"
                        )
                    }
                )
            )
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
            opentelemetry.trace.set_tracer_provider(provider)
            logger.info("OpenTelemetry tracing enabled")
        except ImportError:
            logger.warning(
                "OTLP exporter is unavailable; spans will not be exported."
            )
        _configured = True

    return [OpenTelemetryPlugin()]
