"""ModelCatalogWorkflow + the ``model_catalog`` activity.

The worker-declared provider catalog is served over Temporal's own unit of
work (one workflow scheduling one activity) instead of a hand-rolled JSON
readiness lease. No Temporal server is started here: the activity is a plain
async function and the workflow/activity definitions are read back through the
SDK's own registries.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from temporalio import activity, workflow

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import catalog_workflow
from catalog_workflow import ModelCatalogWorkflow, model_catalog, set_catalog
from config import PROVIDER_GOOGLE_AI_STUDIO, RegisteredModel


@pytest.fixture(autouse=True)
def _reset_catalog():
    yield
    set_catalog([])


def test_workflow_is_a_temporal_workflow_definition() -> None:
    definition = workflow._Definition.must_from_class(ModelCatalogWorkflow)
    assert definition.name == "ModelCatalogWorkflow"
    assert definition.run_fn.__name__ == "run"


def test_activity_is_registered_under_its_stable_name() -> None:
    definition = activity._Definition.must_from_callable(model_catalog)
    assert definition.name == "model_catalog"


@pytest.mark.asyncio
async def test_activity_returns_the_catalog_installed_at_worker_boot() -> None:
    entries = [
        RegisteredModel(
            id="gemini-3.8-flash",
            provider=PROVIDER_GOOGLE_AI_STUDIO,
            label="gemini-3.8-flash",
        )
    ]
    set_catalog(entries)
    assert await model_catalog() == entries


@pytest.mark.asyncio
async def test_activity_returns_empty_before_any_catalog_is_installed() -> None:
    set_catalog([])
    assert await model_catalog() == []


def test_set_catalog_copies_so_later_mutation_cannot_leak() -> None:
    entries = [
        RegisteredModel(id="m", provider=PROVIDER_GOOGLE_AI_STUDIO, label="m")
    ]
    set_catalog(entries)
    entries.clear()
    assert catalog_workflow._CATALOG == [
        RegisteredModel(id="m", provider=PROVIDER_GOOGLE_AI_STUDIO, label="m")
    ]
