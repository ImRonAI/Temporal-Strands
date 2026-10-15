from __future__ import annotations

import asyncio
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


FORGE_NAMES = (
    "forge.openapi.load", "forge.openapi.validate", "forge.openapi.synthesize",
    "forge.openapi.synthesize_resources", "forge.openapi.build_resource_endpoints",
    "forge.openapi.splice", "forge.openapi.filter", "forge.openapi.invoke",
    "forge.openapi.invoke_stream", "forge.openapi.invoke_with_context",
    "forge.openapi.invoke_with_context_stream", "forge.fhir.synthesize_r4",
    "forge.fhir.validate_profile", "forge.fhir.validate_terminology",
    "forge.fhir.validate_resource", "forge.mcp.export_tools",
    "forge.context_packs.upsert", "forge.context_packs.get",
    "forge.context_packs.list", "forge.context_packs.delete",
    "forge.webhooks.verify_signature", "forge.webhooks.create_headers",
    "forge.webhooks.create_subscription", "forge.webhooks.list_subscriptions",
    "forge.webhooks.delete_subscription", "forge.webhooks.enqueue_delivery",
    "forge.health",
)
DICOM_NAMES = (
    "dicom_read_metadata", "dicom_read_pixel_data", "dicom_to_png",
    "dicom_query_orthanc", "dicom_store_orthanc", "dicom_qido_search",
    "dicom_wado_retrieve", "dicom_create_fhir_imaging_study",
    "dicom_validate_tags", "dicom_anonymize",
)
FHIR_ACTIONS = (
    "list_bindings", "binding_info", "search", "lookup", "validate", "suggest",
    "record_usage", "audit_resource_terminology", "propose_terminology_patches",
    "generate_valueset", "generate_conceptmap", "generate_ecl",
    "snomed_concept_graph", "health",
)
ORCHESTRATOR = Path(__file__).resolve().parents[1]
REPORT_DIR = ORCHESTRATOR.parent / ".omo" / "evidence" / "04-package-audit"


def _call(rows, name, function, *, allowed_failure=False, **kwargs):
    try:
        result = function(**kwargs)
        if hasattr(result, "__await__"):
            result = asyncio.run(result)
        assert isinstance(result, dict), repr(result)
        errors = [chunk["error"] for chunk in result.get("chunks", []) if "error" in chunk]
        failed = (
            result.get("status") == "error"
            or result.get("ok") is False
            or result.get("valid") is False
            or result.get("error") is not None
            or errors
        )
        rows[name] = {
            "status": "failed_closed" if failed and allowed_failure else "failed" if failed else "passed",
            "result": result,
        }
        return result
    except Exception as exc:
        rows[name] = {
            "status": "failed_closed" if allowed_failure else "failed",
            "error": f"{type(exc).__name__}: {exc}",
        }
        return {}


def _audit_forge(rows):
    module = importlib.import_module("strands_tools.forge.strands_tools")
    tools = {item.tool_name: item for item in module.get_forge_strands_tools()}
    assert set(tools) == set(FORGE_NAMES)
    spec = {
        "openapi": "3.1.0", "info": {"title": "Local audit", "version": "1"},
        "paths": {"/items": {"get": {"operationId": "listItems", "responses": {"200": {"description": "OK"}}}}},
    }
    patient = {"resourceType": "Patient", "id": "audit", "active": True}
    calls = {
        "forge.openapi.load": {"source": json.dumps(spec)},
        "forge.openapi.validate": {"spec": spec},
        "forge.openapi.synthesize": {"endpoints": [{"path": "/items", "method": "get", "operationId": "listItems"}]},
        "forge.openapi.synthesize_resources": {"resources": [{"name": "item"}]},
        "forge.openapi.build_resource_endpoints": {"resources": [{"name": "item"}]},
        "forge.openapi.splice": {"specs": [spec]},
        "forge.openapi.filter": {"spec": spec, "allow_operation_ids": ["listItems"]},
        "forge.fhir.synthesize_r4": {"resources": ["Patient"]},
        "forge.fhir.validate_profile": {"resource": patient},
        "forge.fhir.validate_terminology": {"resource": patient},
        "forge.fhir.validate_resource": {"resource": patient},
        "forge.mcp.export_tools": {"spec": spec},
        "forge.context_packs.upsert": {"id": "audit", "pack": {}},
        "forge.context_packs.get": {"id": "audit"},
        "forge.context_packs.list": {},
        "forge.context_packs.delete": {"id": "audit"},
        "forge.health": {},
    }
    for name, kwargs in calls.items():
        _call(rows, name, tools[name], **kwargs)
    for name in FORGE_NAMES:
        if name.startswith("forge.openapi.invoke"):
            _call(rows, name, tools[name], allowed_failure=True, spec=spec, operation_id="listItems")
    payload = {"event": "audit"}
    headers = _call(rows, "forge.webhooks.create_headers", tools["forge.webhooks.create_headers"],
                    secret="local-audit-secret", payload=payload)
    _call(rows, "forge.webhooks.verify_signature", tools["forge.webhooks.verify_signature"],
          secret="local-audit-secret", payload=payload, headers=headers.get("headers", {}))
    _call(rows, "forge.webhooks.create_subscription", tools["forge.webhooks.create_subscription"],
          allowed_failure=True, id="audit", url="https://example.invalid/audit",
          event_types=["audit"], secret="local-audit-secret")
    _call(rows, "forge.webhooks.list_subscriptions", tools["forge.webhooks.list_subscriptions"], allowed_failure=True)
    _call(rows, "forge.webhooks.enqueue_delivery", tools["forge.webhooks.enqueue_delivery"],
          allowed_failure=True, subscription_id="audit", event_type="audit", payload=payload)
    _call(rows, "forge.webhooks.delete_subscription", tools["forge.webhooks.delete_subscription"],
          allowed_failure=True, subscription_id="audit")


def _dicom_fixture():
    from pydicom.dataset import FileDataset, FileMetaDataset

    meta = FileMetaDataset()
    meta.TransferSyntaxUID = "1.2.840.10008.1.2.1"
    meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.7"
    meta.MediaStorageSOPInstanceUID = "1.2.826.0.1.3680043.10.999.1"
    ds = FileDataset("audit.dcm", {}, preamble=b"\0" * 128, file_meta=meta)
    ds.SOPClassUID = meta.MediaStorageSOPClassUID
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.StudyInstanceUID = "1.2.826.0.1.3680043.10.999.2"
    ds.SeriesInstanceUID = "1.2.826.0.1.3680043.10.999.3"
    ds.PatientName = "Audit^Fixture"
    ds.PatientID = "audit-only"
    ds.Modality = "OT"
    ds.StudyDate = "20261009"
    ds.Rows = 2
    ds.Columns = 2
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    ds.PixelData = bytes([0, 64, 128, 255])
    ds.save_as("audit.dcm", enforce_file_format=True)


def _audit_dicom(rows):
    module = importlib.import_module("strands_tools.dicom.strands_tools")
    tools = {item.tool_name: item for item in module.get_dicom_strands_tools()}
    assert set(tools) == set(DICOM_NAMES)
    bound = {
        "dicom_query_orthanc": "needs Orthanc",
        "dicom_store_orthanc": "needs Orthanc",
        "dicom_qido_search": "needs DICOMweb",
        "dicom_wado_retrieve": "needs DICOMweb",
    }
    for name, reason in bound.items():
        rows[name] = {"status": "needs_server", "reason": reason}
    try:
        _dicom_fixture()
    except ImportError as exc:
        for name in set(DICOM_NAMES) - set(bound):
            rows[name] = {"status": "missing_import", "reason": f"{type(exc).__name__}: {exc}"}
        return
    for name in DICOM_NAMES:
        if name in bound:
            continue
        kwargs = {"file_path": "audit.dcm"}
        if name == "dicom_read_pixel_data":
            kwargs["output_format"] = "numpy_shape"
        result = _call(rows, name, tools[name], **kwargs)
        if name == "dicom_read_metadata" and result:
            assert result["patient"]["id"] == "audit-only"
        elif name == "dicom_validate_tags" and result:
            assert result["valid"] is True
        elif name == "dicom_read_pixel_data" and result:
            assert result["shape"] == [2, 2]
        elif name in {"dicom_anonymize", "dicom_to_png"} and "output_path" in result:
            assert Path(result["output_path"]).stat().st_size > 0
        elif name == "dicom_create_fhir_imaging_study" and result:
            assert result["resourceType"] == "ImagingStudy"


def _audit_fhir(rows):
    module = importlib.import_module("strands_tools.fhir.fhir_tools")
    assert module.TOOL_SPEC["name"] == "fhir_terminology_copilot"
    assert set(module.TOOL_SPEC["inputSchema"]["json"]["properties"]["action"]["enum"]) == set(FHIR_ACTIONS)
    inputs = {
        "health": {}, "list_bindings": {},
        "record_usage": {"fhirpath": "Patient.gender", "code": "unknown"},
        "generate_valueset": {"name": "Audit", "concepts": [{"system": "urn:audit", "code": "a"}]},
        "generate_conceptmap": {"name": "Audit", "source_system": "urn:audit:a", "target_system": "urn:audit:b",
                                "mappings": [{"sourceCode": "a", "targetCode": "b"}]},
        "generate_ecl": {"snomed_roots": ["138875005"]},
    }
    for action in FHIR_ACTIONS:
        if action in inputs:
            _call(rows, action, module.fhir_terminology_copilot,
                  tool={"toolUseId": f"audit-{action}", "name": "fhir_terminology_copilot",
                        "input": {"action": action, **inputs[action]}})
        else:
            reason = "needs terminology server or fetched terminology artifacts"
            if action == "snomed_concept_graph":
                reason = "needs licensed SNOMED content and configured Snowstorm server"
            rows[action] = {"status": "needs_server", "reason": reason}


def _run_audit(package, report_path):
    rows = {}
    network_attempts = []

    def forbid_network(event, args):
        if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
            network_attempts.append(event)
            raise RuntimeError(f"Network prohibited in package audit: {event}")

    sys.addaudithook(forbid_network)
    inventories = {"forge": FORGE_NAMES, "dicom": DICOM_NAMES, "fhir": FHIR_ACTIONS}
    audit = {"forge": _audit_forge, "dicom": _audit_dicom, "fhir": _audit_fhir}
    error = None
    try:
        audit[package](rows)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        for name in inventories[package]:
            rows.setdefault(name, {"status": "blocked", "reason": error})
    report = {"package": package, "rows": rows, "error": error, "network_attempts": network_attempts}
    if package == "fhir":
        report["source_note"] = "Source also exports decorated convenience tools; the plan's copilot/action inventory is audited here."
    report_path.write_text(json.dumps(report, indent=2, default=str) + "\n")


@pytest.mark.asyncio
async def test_discovery_lists_packages_and_excludes_absent_tools():
    from load_tool import STRANDS_TOOLS_DIR, _nested_tool_file, discover_tools_activity

    result = await discover_tools_activity()
    assert result["status"] == "success"
    catalog = set(result["content"][0]["text"].splitlines())
    assert {"forge", "dicom", "fhir"} <= catalog
    assert not {"pubmed", "fda"} & catalog
    for name in ("forge", "dicom", "fhir"):
        assert _nested_tool_file(name) is not None
    assert not (STRANDS_TOOLS_DIR / "pubmed").exists()
    fda_sources = list((STRANDS_TOOLS_DIR / "fda").glob("*.py"))
    assert [path.name for path in fda_sources] == ["__init__.py"]
    assert not any(line.strip() and not line.lstrip().startswith("#") for line in fda_sources[0].read_text().splitlines())
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "discovery.json").write_text(json.dumps({
        "catalog": sorted(catalog), "pubmed": "absent on disk", "fda": "initializer contains only a comment",
    }, indent=2) + "\n")


@pytest.mark.parametrize("package,inventory", [("forge", FORGE_NAMES), ("dicom", DICOM_NAMES), ("fhir", FHIR_ACTIONS)])
def test_package_inventory_audit(package, inventory, tmp_path):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"{package}.json"
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "FHIR_TERMINOLOGY_CACHE_DIR": str(tmp_path / "fhir-cache"),
           "FHIR_TERMINOLOGY_SNOMED_ENABLED": "false", "FHIR_TERMINOLOGY_TX_SERVER": "", "FHIR_TERMINOLOGY_BINDINGS_JSON": ""}
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), package, str(report_path)],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    report = json.loads(report_path.read_text())
    assert set(report["rows"]) == set(inventory), report
    assert report["network_attempts"] == [], report
    failures = {name: row for name, row in report["rows"].items() if row["status"] in {"failed", "blocked"}}
    assert not failures, f"{report_path}: {json.dumps(failures, indent=2)}"
    if package == "forge":
        assert report["rows"]["forge.health"]["result"]["healthy"] is True
    if package == "fhir":
        assert report["rows"]["health"]["status"] == "passed"
        assert report["rows"]["list_bindings"]["status"] == "passed"
    print(f"Package audit: {report_path}")


if __name__ == "__main__":
    _run_audit(sys.argv[1], Path(sys.argv[2]))
