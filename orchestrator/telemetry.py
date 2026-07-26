"""OpenTelemetry wiring, shared by the worker and the HTTP bridge.

Straight from the Strands plugin's own "Observability" section: register
`OpenTelemetryPlugin` on the *client* (workers built from that client pick it
up automatically) alongside `StrandsPlugin`, and set the tracer provider
before connecting. That yields OTel spans around every model, tool, and MCP
activity the plugin schedules, plus whatever Strands itself emits inside
`invoke_async` — which is the only way to see where time actually goes in a
long-horizon, multi-turn agent run.

Enabling is gated on OTEL_EXPORTER_OTLP_ENDPOINT, the standard OpenTelemetry
environment variable, rather than a flag invented here. Unset means tracing
is off entirely and neither process pays for it. The tracer provider is
installed exactly once per process; `set_tracer_provider` warns and keeps the
first provider if called twice, so re-entry is harmless but pointless.
"""

import logging
import os
from typing import Sequence

logger = logging.getLogger(__name__)

_configured = False


def telemetry_plugins() -> Sequence[object]:
    """Return `[OpenTelemetryPlugin()]` when tracing is configured, else `[]`.

    Returning a list (rather than toggling a boolean at each call site) keeps
    the plugin list in run_worker.py / server.py identical whether tracing is
    on or off — there is no second, untested code path when it is disabled.
    """
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
            "OTEL_EXPORTER_OTLP_ENDPOINT is set but temporalio's opentelemetry "
            "contrib is unavailable; continuing without tracing."
        )
        return []

    if not _configured:
        try:
            import opentelemetry.trace
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            # Imported lazily and separately: the exporter lives in its own
            # distribution (opentelemetry-exporter-otlp-proto-http). If an
            # environment predates it being added to requirements.txt, the
            # spans still get created and context still propagates across the
            # workflow/activity boundary — they just aren't shipped anywhere.
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

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
            logger.info("OpenTelemetry tracing enabled, exporting to %s", endpoint)
        except ImportError:
            logger.warning(
                "OTEL_EXPORTER_OTLP_ENDPOINT is set but the OTLP exporter is not "
                "installed (pip install opentelemetry-exporter-otlp-proto-http). "
                "Spans will be created and context propagated, but not exported."
            )
        _configured = True

    return [OpenTelemetryPlugin()]
