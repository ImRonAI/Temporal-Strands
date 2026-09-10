"""The worker's provider-declaring model catalog, served over Temporal.

There is no readiness file and no lease. Asking "which models are registered,
and by which provider?" is a question only a live worker can answer, so it is
asked the way Temporal asks any such question: a workflow schedules an
activity on the worker's task queue.

``ModelCatalogWorkflow`` is the request; ``model_catalog`` is the answer. The
activity returns the catalog ``run_worker`` captured at boot through
:func:`set_catalog` -- the same module-level-registry pattern
``think_activity.set_model_factories`` uses, for the same reason: the values
are worker-process state that must never cross a workflow payload boundary.

Liveness falls out of this for free. If no worker is polling the queue the
workflow's activity never starts and the API's execute times out, which is
precisely the degraded state ``/health`` reports -- no PID probes, no
heartbeat timestamps, no clock-skew handling.
"""

from __future__ import annotations

import logging

from temporalio import activity, workflow

from config import CATALOG_ACTIVITY_TIMEOUT, RegisteredModel

logger = logging.getLogger(__name__)

# The catalog captured at worker boot. Populated once by run_worker.main after
# assemble_model_factories(); empty in any process that never booted a worker
# (the API process imports the definitions, not the state).
_CATALOG: list[RegisteredModel] = []


def set_catalog(catalog: list[RegisteredModel]) -> None:
    """Install the worker's provider catalog for the ``model_catalog`` activity.

    Copies the entries: the caller's list is boot-time scratch that must not
    stay aliased into the served catalog.
    """
    _CATALOG.clear()
    _CATALOG.extend(catalog)


@activity.defn(name="model_catalog")
async def model_catalog() -> list[RegisteredModel]:
    """Every model this worker registered, with its declared provider.

    Providers are declared here, never inferred from the id shape: a
    ``gemini*`` id may be Google AI Studio direct or ``google/*`` through the
    Perplexity gateway, and only the worker that built the factory knows which.
    """
    return list(_CATALOG)


@workflow.defn
class ModelCatalogWorkflow:
    """Fetch the live worker's model catalog.

    Deliberately a single activity call: the durable unit of work IS the
    catalog read, and its completion is the proof that a worker is serving the
    task queue.
    """

    @workflow.run
    async def run(self) -> list[RegisteredModel]:
        return await workflow.execute_activity(
            model_catalog,
            start_to_close_timeout=CATALOG_ACTIVITY_TIMEOUT,
        )
