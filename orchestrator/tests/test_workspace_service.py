import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from workspace_service import create_workspace_service


@pytest.fixture
def service(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    workspace_id = uuid4()
    app = create_workspace_service(root, workspace_id)
    with TestClient(app) as client:
        yield client, root, workspace_id


def test_service_create_read_search(service):
    client, root, workspace_id = service
    assert client.get("/health").json()["workspace_id"] == str(workspace_id)
    project_id = str(uuid4())
    assert client.post(f"/projects/{project_id}").status_code == 201
    assert client.post(f"/projects/{project_id}").status_code == 409
    (root / project_id / "hello.txt").write_text("hello desktop\n")
    assert client.get(f"/projects/{project_id}/files").json()["entries"][0]["name"] == "hello.txt"
    result = client.get(f"/projects/{project_id}/file", params={"path": "hello.txt"})
    assert result.json()["text"] == "hello desktop\n"
    assert client.get(f"/projects/{project_id}/search", params={"query": "desktop"}).json()["matches"]
    assert client.get(f"/projects/{project_id}/file", params={
        "path": "hello.txt", "expected_sha256": "0" * 64,
    }).status_code == 412


@pytest.mark.parametrize("path,status", [("../secret", 422), (".env", 404), ("missing", 404)])
def test_service_path_denial(service, path, status):
    client, _, _ = service
    project_id = str(uuid4())
    client.post(f"/projects/{project_id}")
    assert client.get(f"/projects/{project_id}/file", params={"path": path}).status_code == status


def test_service_never_follows_project_root_link(service, tmp_path):
    client, root, _ = service
    project_id = str(uuid4())
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / project_id).symlink_to(outside, target_is_directory=True)
    assert client.get(f"/projects/{project_id}/files").status_code == 404


def test_service_missing_mount_fails_startup(tmp_path):
    with pytest.raises(OSError), TestClient(create_workspace_service(tmp_path / "missing", uuid4())):
        pass
    assert not (tmp_path / "missing").exists()


def test_service_storage_error_is_sanitized(service, monkeypatch):
    client, root, _ = service
    def fail(*args, **kwargs):
        raise OSError(f"sensitive host path {root}")
    monkeypatch.setattr(os, "mkdir", fail)
    result = client.post(f"/projects/{uuid4()}")
    assert result.status_code == 503
    assert str(root) not in result.text
