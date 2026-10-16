from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import workspace_api
from workspace_api import WorkspaceRequestBoundary, WorkspaceSettings, router
from workspace_registry import initialize_registry, resolve_session, revoke_session
from workspace_service import create_workspace_service
from workspace_state import (
    begin_control_transition, complete_control_transition, get_lease, initialize_state,
)


@pytest.fixture
def connected(tmp_path):
    path = tmp_path / "control.db"
    initialize_state(path)
    initialize_registry(path)
    app = FastAPI()
    app.include_router(router)
    app.add_middleware(WorkspaceRequestBoundary)
    app.state.workspace_settings = WorkspaceSettings(
        owner_id="owner-subject", origin="https://gwen.example", state_path=path,
        auth_socket=tmp_path / "auth.sock", service_socket=tmp_path / "workspace.sock",
    )
    auth_requests = []
    def auth(request):
        auth_requests.append(request)
        if request.headers.get("cookie") != "oidc=valid":
            return httpx.Response(401)
        return httpx.Response(202, headers={"X-Auth-Request-User": "owner-subject"})

    @asynccontextmanager
    async def clients():
        async with httpx.AsyncClient(transport=httpx.MockTransport(auth), base_url="http://auth") as auth_client:
            app.state.workspace_auth = auth_client
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://gwen.example") as client:
                yield app, client, path, auth_requests
    return clients


HEADERS = {"Cookie": "oidc=valid", "Origin": "https://gwen.example", "X-Gwen-CSRF": "1"}


async def registered(client):
    workspace = (await client.post("/workspaces", json={}, headers=HEADERS)).json()
    workspace_id = workspace["workspace_id"]
    result = await client.post(f"/workspaces/{workspace_id}/sessions", json={}, headers=HEADERS)
    assert result.status_code == 201, result.text
    session = result.json()
    headers = {**HEADERS, "Authorization": "Bearer " + session["token"]}
    return workspace_id, session, headers


@pytest.mark.asyncio
async def test_disabled_api_and_error_cache_policy():
    app = FastAPI()
    app.include_router(router)
    app.add_middleware(WorkspaceRequestBoundary)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/workspaces", json={})
        assert response.status_code == 503
        assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_explicit_enablement_requires_configuration(monkeypatch):
    monkeypatch.setenv("GWEN_WORKSPACE_API_ENABLED", "1")
    monkeypatch.delenv("GWEN_WORKSPACE_OWNER_ID", raising=False)
    with pytest.raises(KeyError):
        async with workspace_api.workspace_lifespan(FastAPI()):
            pytest.fail("Missing configuration must not enable workspace endpoints")


@pytest.mark.asyncio
@pytest.mark.parametrize("changes,status", [
    ({"Cookie": "oidc=invalid", "X-Auth-Request-User": "owner-subject"}, 401),
    ({"Cookie": ""}, 401),
    ({"Origin": "https://preview.example"}, 403),
    ({"Origin": "null"}, 403),
    ({"X-Gwen-CSRF": ""}, 403),
    ({"Sec-Fetch-Site": "cross-site"}, 403),
    ({"Content-Type": "text/plain"}, 403),
])
async def test_auth_and_csrf_fail_closed(connected, changes, status):
    async with connected() as (_, client, _, _):
        response = await client.post("/workspaces", json={}, headers={**HEADERS, **changes})
        assert response.status_code == status
        assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_owner_resolved_only_from_native_auth(connected):
    async with connected() as (app, client, _, calls):
        response = await client.post("/workspaces", json={}, headers={
            **HEADERS, "X-Auth-Request-User": "attacker", "Authorization": "Bearer forged",
        })
        assert response.status_code == 200
        assert response.json()["owner_id"] == "owner-subject"
        assert "authorization" not in calls[0].headers
        assert "x-auth-request-user" not in calls[0].headers
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(202, headers={"X-Auth-Request-User": "other"}),
        ), base_url="http://auth") as auth:
            app.state.workspace_auth = auth
            assert (await client.post("/workspaces", json={}, headers=HEADERS)).status_code == 403


@pytest.mark.asyncio
async def test_session_binding_and_revocation(connected):
    async with connected() as (_, client, _, _):
        workspace_id, session, headers = await registered(client)
        url = f"/workspaces/{workspace_id}"
        assert (await client.get(url, headers=HEADERS)).status_code == 401
        assert (await client.get(url, headers={**HEADERS, "Authorization": "Bearer fake"})).status_code == 404
        response = await client.get(url, headers=headers)
        assert response.status_code == 200
        assert response.json()["desktop_control_enabled"] is False
        assert (await client.get(f"/workspaces/{uuid4()}", headers=headers)).status_code == 404
        response = await client.request("DELETE", url + "/sessions/current", json={}, headers=headers)
        assert response.status_code == 204
        assert (await client.get(url, headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_project_creation_fenced_without_native_control(connected):
    async with connected() as (_, client, _, _):
        workspace_id, _, headers = await registered(client)
        response = await client.post(f"/workspaces/{workspace_id}/projects/prepare", headers=headers,
                                     json={"name": "fixture", "desktop_epoch": 1, "lease_epoch": 0})
        assert response.status_code == 409


def activate_fixture_controller(path, workspace_id, token):
    binding = resolve_session(path, "owner-subject", workspace_id, token)
    lease = get_lease(path, binding)
    pending = begin_control_transition(path, binding, lease.desktop_epoch, lease.lease_epoch)
    lease = complete_control_transition(path, binding, pending.transition_id, "human",
                                        revocation_receipt="synthetic-native-revocation")
    return binding, lease


@pytest.mark.asyncio
async def test_connected_project_creation_read_search_and_duplicate(connected, tmp_path):
    async with connected() as (app, client, path, _):
        workspace_id, session, headers = await registered(client)
        _, lease = activate_fixture_controller(path, workspace_id, session["token"])
        root = tmp_path / "projects"
        root.mkdir()
        service = create_workspace_service(root, UUID(workspace_id))
        with TestClient(service):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service), base_url="http://workspace") as upstream:
                app.state.workspace_service = upstream
                prefix = f"/workspaces/{workspace_id}/projects"
                response = await client.post(prefix + "/prepare", headers=headers, json={
                    "name": "Fixture project", "desktop_epoch": lease.desktop_epoch, "lease_epoch": lease.lease_epoch,
                })
                assert response.status_code == 201, response.text
                project = response.json()
                url = prefix + "/" + project["project_id"]
                result = await client.post(url + "/create", headers=headers, json={})
                assert result.status_code == 200, result.text
                assert result.json()["state"] == "succeeded"
                assert (await client.post(url + "/create", headers=headers, json={})).json() == result.json()
                selected = await client.post(url + "/open", headers=headers, json={})
                assert selected.json()["project_id"] == project["project_id"]
                snapshot = await client.get(f"/workspaces/{workspace_id}", headers=headers)
                assert snapshot.json()["binding"]["project_id"] == project["project_id"]
                (root / project["project_id"] / "hello.txt").write_text("hello desktop\n")
                assert (await client.get(url + "/files", headers=headers)).json()["entries"][0]["name"] == "hello.txt"
                assert (await client.get(url + "/file", headers=headers, params={"path": "hello.txt"})).json()["text"] == "hello desktop\n"
                assert (await client.get(url + "/search", headers=headers, params={"query": "desktop"})).json()["matches"]
                assert (await client.get(url + "/file", headers=headers, params={"path": "../secret"})).status_code == 422


@pytest.mark.asyncio
async def test_lost_service_reply_never_redispatches(connected):
    async with connected() as (app, client, path, _):
        workspace_id, session, headers = await registered(client)
        _, lease = activate_fixture_controller(path, workspace_id, session["token"])
        creates = []
        def upstream(request):
            if request.url.path == "/health":
                return httpx.Response(200, json={"workspace_id": workspace_id, "filesystem": "ready"})
            creates.append(request)
            raise httpx.ReadTimeout("lost reply")
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://workspace") as service:
            app.state.workspace_service = service
            prefix = f"/workspaces/{workspace_id}/projects"
            project = (await client.post(prefix + "/prepare", headers=headers, json={
                "name": "fixture", "desktop_epoch": lease.desktop_epoch, "lease_epoch": lease.lease_epoch,
            })).json()
            url = prefix + "/" + project["project_id"] + "/create"
            assert (await client.post(url, headers=headers, json={})).status_code == 503
            response = await client.post(url, headers=headers, json={})
            assert response.json()["state"] == "outcome_unknown"
            assert len(creates) == 1


@pytest.mark.asyncio
async def test_request_limit_before_parsing(connected, monkeypatch):
    monkeypatch.setattr(workspace_api, "WORKSPACE_HTTP_MAX_BYTES", 128)
    async with connected() as (_, client, _, calls):
        response = await client.post("/workspaces", content=b" " * 129, headers=HEADERS)
        assert response.status_code == 413
        assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_status", [200, 302, 500])
async def test_auth_failure_does_not_become_login_html(connected, auth_status):
    async with connected() as (app, client, _, _):
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(auth_status, text="login or internal failure"),
        ), base_url="http://auth") as auth:
            app.state.workspace_auth = auth
            response = await client.post("/workspaces", json={}, headers=HEADERS)
            assert response.status_code == 503
            assert "login or internal failure" not in response.text


@pytest.mark.asyncio
async def test_mutation_budget_and_client_scope_injection(connected, monkeypatch):
    monkeypatch.setattr(workspace_api, "DESKTOP_MAX_MUTATIONS", 1)
    async with connected() as (_, client, path, _):
        workspace_id, session, headers = await registered(client)
        _, lease = activate_fixture_controller(path, workspace_id, session["token"])
        url = f"/workspaces/{workspace_id}/projects/prepare"
        body = {"name": "fixture", "desktop_epoch": lease.desktop_epoch, "lease_epoch": lease.lease_epoch}
        for field in ("owner_id", "root", "operation_id", "session_id", "actor"):
            assert (await client.post(url, headers=headers, json={**body, field: "forged"})).status_code == 422
        assert (await client.post(url, headers=headers, json=body)).status_code == 201
        assert (await client.post(url, headers=headers, json=body)).status_code == 409


@pytest.mark.asyncio
async def test_session_cannot_execute_another_sessions_project(connected):
    async with connected() as (_, client, path, _):
        workspace_id, session, headers = await registered(client)
        _, lease = activate_fixture_controller(path, workspace_id, session["token"])
        prefix = f"/workspaces/{workspace_id}/projects"
        project = (await client.post(prefix + "/prepare", headers=headers, json={
            "name": "fixture", "desktop_epoch": lease.desktop_epoch, "lease_epoch": lease.lease_epoch,
        })).json()
        other = (await client.post(f"/workspaces/{workspace_id}/sessions", json={}, headers=HEADERS)).json()
        other_headers = {**HEADERS, "Authorization": "Bearer " + other["token"]}
        response = await client.post(prefix + "/" + project["project_id"] + "/create", json={}, headers=other_headers)
        assert response.status_code == 404


@pytest.mark.parametrize("origin", [
    "http://gwen.example", "https://gwen.example/", "https://gwen.example/path",
    "https://gwen.example?x=y", "https://user:password@gwen.example", "https://gwen.example#fragment",
])
def test_settings_reject_ambiguous_origins(tmp_path, origin):
    with pytest.raises(ValueError):
        WorkspaceSettings(owner_id="owner", origin=origin, state_path=tmp_path / "db",
                          auth_socket=tmp_path / "auth", service_socket=tmp_path / "service")


@pytest.mark.asyncio
async def test_revocation_during_dispatch_preserves_unknown_and_settles_project(connected):
    async with connected() as (app, client, path, _):
        workspace_id, session, headers = await registered(client)
        binding, lease = activate_fixture_controller(path, workspace_id, session["token"])
        def upstream(request):
            if request.url.path == "/health":
                return httpx.Response(200, json={"workspace_id": workspace_id, "filesystem": "ready"})
            revoke_session(path, binding)
            return httpx.Response(201, json={"project_id": request.url.path.split("/")[-1]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://workspace") as service:
            app.state.workspace_service = service
            prefix = f"/workspaces/{workspace_id}/projects"
            project = (await client.post(prefix + "/prepare", headers=headers, json={
                "name": "fixture", "desktop_epoch": lease.desktop_epoch, "lease_epoch": lease.lease_epoch,
            })).json()
            result = await client.post(prefix + "/" + project["project_id"] + "/create", json={}, headers=headers)
            assert result.status_code == 200, result.text
            assert result.json()["state"] == "outcome_unknown"
            new = (await client.post(f"/workspaces/{workspace_id}/sessions", json={}, headers=HEADERS)).json()
            response = await client.get(prefix, headers={**HEADERS, "Authorization": "Bearer " + new["token"]})
            assert response.json()[0]["state"] == "outcome_unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["/tmp", "nested/project", " backspace ", "\\root", "\u2028"])
async def test_invalid_project_names_fail_before_journaling(connected, name):
    async with connected() as (_, client, _, _):
        workspace_id, _, headers = await registered(client)
        response = await client.post(f"/workspaces/{workspace_id}/projects/prepare", headers=headers,
                                     json={"name": name, "desktop_epoch": 1, "lease_epoch": 0})
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_expiry_between_resolution_and_dispatch_is_rechecked(connected, monkeypatch):
    import workspace_registry
    async with connected() as (app, client, path, _):
        workspace_id, session, headers = await registered(client)
        _, lease = activate_fixture_controller(path, workspace_id, session["token"])
        creates = []
        def upstream(request):
            if request.url.path == "/health":
                monkeypatch.setattr(workspace_registry, "_now", lambda: session["expires_at"] + 1)
                return httpx.Response(200, json={"workspace_id": workspace_id, "filesystem": "ready"})
            creates.append(request)
            return httpx.Response(201)
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://workspace") as service:
            app.state.workspace_service = service
            prefix = f"/workspaces/{workspace_id}/projects"
            project = (await client.post(prefix + "/prepare", headers=headers, json={
                "name": "fixture", "desktop_epoch": lease.desktop_epoch, "lease_epoch": lease.lease_epoch,
            })).json()
            result = await client.post(prefix + "/" + project["project_id"] + "/create", json={}, headers=headers)
            assert result.status_code == 404
            assert not creates


@pytest.mark.asyncio
async def test_wrong_workspace_service_never_receives_create(connected):
    async with connected() as (app, client, path, _):
        workspace_id, session, headers = await registered(client)
        _, lease = activate_fixture_controller(path, workspace_id, session["token"])
        calls = []
        def upstream(request):
            calls.append(request.url.path)
            return httpx.Response(200, json={"workspace_id": str(uuid4()), "filesystem": "ready"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://workspace") as service:
            app.state.workspace_service = service
            prefix = f"/workspaces/{workspace_id}/projects"
            project = (await client.post(prefix + "/prepare", headers=headers, json={
                "name": "fixture", "desktop_epoch": lease.desktop_epoch, "lease_epoch": lease.lease_epoch,
            })).json()
            response = await client.post(prefix + "/" + project["project_id"] + "/create", json={}, headers=headers)
            assert response.status_code == 503
            assert calls == ["/health"]


@pytest.mark.asyncio
async def test_project_completion_transaction_rolls_back_together(connected):
    from workspace_state import _transaction, start_operation, get_operation
    from workspace_registry import finish_project_operation, get_project
    import sqlite3

    async with connected() as (_, client, path, _):
        workspace_id, session, headers = await registered(client)
        binding, lease = activate_fixture_controller(path, workspace_id, session["token"])
        project = (await client.post(f"/workspaces/{workspace_id}/projects/prepare", headers=headers, json={
            "name": "fixture", "desktop_epoch": lease.desktop_epoch, "lease_epoch": lease.lease_epoch,
        })).json()
        start_operation(path, binding, project["operation_id"])
        with _transaction(path) as connection:
            connection.execute("""CREATE TRIGGER fail_completion BEFORE UPDATE OF state ON operations
                                BEGIN SELECT RAISE(ABORT, 'synthetic storage failure'); END""")
        with pytest.raises(sqlite3.IntegrityError):
            finish_project_operation(path, binding, project["project_id"], succeeded=True)
        assert get_project(path, binding, project["project_id"]).state == "pending"
        assert get_operation(path, binding, project["operation_id"]).state == "running"
