"""Authenticated control API; filesystem calls go only to the private service.

Disabled unless explicitly configured. OAuth2 Proxy revalidates each request
through its private Unix socket; browser identity headers are never trusted.
These sessions authorize workspace APIs, not existing unauthenticated chat IDs.
Native VNC revocation must be implemented before exposing control acquisition.
"""

import json
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from config import (
    DESKTOP_MAX_MUTATIONS, WORKSPACE_AUTH_TIMEOUT_SECONDS,
    WORKSPACE_HTTP_MAX_BYTES, WORKSPACE_SERVICE_TIMEOUT_SECONDS,
    WORKSPACE_PROJECT_CREATE_ACTION,
)
from workspace_registry import (
    finish_project_operation, get_project, initialize_registry, issue_session, list_projects,
    register_workspace, reserve_project, resolve_session, revoke_session,
    _validate_name, open_project,
)
from workspace_state import (
    ScopeUnavailable, StateConflict, WorkspaceBinding, accept_operation,
    get_lease, get_operation, start_operation,
)


class WorkspaceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    owner_id: str = Field(min_length=1, max_length=256)
    origin: str
    state_path: Path
    auth_socket: Path
    service_socket: Path

    @field_validator("origin")
    @classmethod
    def exact_https_origin(cls, value: str) -> str:
        url = httpx.URL(value)
        if (
            url.scheme != "https" or not url.host or url.userinfo
            or url.path != "/" or url.query or url.fragment or value.endswith("/")
            or str(url).rstrip("/") != value
        ):
            raise ValueError("An exact HTTPS application origin is required")
        return value

    @field_validator("state_path", "auth_socket", "service_socket")
    @classmethod
    def absolute_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("Absolute trusted service paths required")
        return value


@asynccontextmanager
async def workspace_lifespan(app):
    app.state.workspace_settings = None
    if os.environ.get("GWEN_WORKSPACE_API_ENABLED") != "1":
        yield
        return
    # Explicit enablement with invalid/missing configuration fails startup closed.
    settings = WorkspaceSettings(
        owner_id=os.environ["GWEN_WORKSPACE_OWNER_ID"],
        origin=os.environ["GWEN_WORKSPACE_ORIGIN"],
        state_path=Path(os.environ["GWEN_WORKSPACE_STATE_PATH"]),
        auth_socket=Path(os.environ["GWEN_WORKSPACE_AUTH_SOCKET"]),
        service_socket=Path(os.environ["GWEN_WORKSPACE_SERVICE_SOCKET"]),
    )
    await run_in_threadpool(initialize_registry, settings.state_path)
    async with (
        httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=str(settings.auth_socket)),
            base_url="http://auth", timeout=WORKSPACE_AUTH_TIMEOUT_SECONDS,
            follow_redirects=False, trust_env=False,
        ) as auth,
        httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=str(settings.service_socket)),
            base_url="http://workspace", timeout=WORKSPACE_SERVICE_TIMEOUT_SECONDS,
            follow_redirects=False, trust_env=False,
        ) as service,
    ):
        app.state.workspace_settings = settings
        app.state.workspace_auth = auth
        app.state.workspace_service = service
        try:
            yield
        finally:
            app.state.workspace_settings = None


async def authorized_owner(request: Request, response: Response):
    settings = getattr(request.app.state, "workspace_settings", None)
    if settings is None:
        raise HTTPException(503, "Workspace API disabled")
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Cookie, Authorization"
    origin = request.headers.getlist("origin")
    if len(origin) > 1 or (origin and origin != [settings.origin]):
        raise HTTPException(403, "Application origin required")
    if request.headers.get("sec-fetch-site") not in (None, "same-origin", "none"):
        raise HTTPException(403, "Cross-site workspace access denied")
    if request.method not in ("GET", "HEAD") and (
        origin != [settings.origin]
        or request.headers.getlist("x-gwen-csrf") != ["1"]
        or request.headers.get("content-type", "").split(";")[0] != "application/json"
    ):
        raise HTTPException(403, "Same-origin JSON mutation required")
    cookies = request.headers.getlist("cookie")
    if len(cookies) != 1 or not cookies[0]:
        raise HTTPException(401, "Authentication required")
    try:
        # Only the native auth endpoint receives cookies. No Authorization,
        # forwarded host, browser identity headers, or service credentials pass.
        async with request.app.state.workspace_auth.stream(
            "GET", "/oauth2/auth", headers={"Cookie": cookies[0]},
        ) as result:
            if result.status_code in (401, 403):
                raise HTTPException(result.status_code, "Authentication required")
            if result.status_code != 202:
                raise HTTPException(503, "Authentication service unavailable")
            if result.headers.get_list("x-auth-request-user") != [settings.owner_id]:
                raise HTTPException(403, "Owner access required")
    except httpx.HTTPError:
        raise HTTPException(503, "Authentication service unavailable") from None
    try:
        yield settings.owner_id
    except ScopeUnavailable:
        raise HTTPException(404, "Workspace resource unavailable") from None
    except StateConflict:
        raise HTTPException(409, "Workspace state conflict") from None
    except (OSError, sqlite3.Error):
        raise HTTPException(503, "Workspace storage unavailable") from None


Owner = Annotated[str, Depends(authorized_owner)]
router = APIRouter(tags=["workspaces"])


async def session_binding(workspace_id: UUID, request: Request, owner: Owner) -> WorkspaceBinding:
    authorization = request.headers.getlist("authorization")
    if (
        len(authorization) != 1 or not authorization[0].startswith("Bearer ")
        or len(authorization[0]) > 256
    ):
        raise HTTPException(401, "Workspace session required")
    token = authorization[0][7:]
    if not token or any(character.isspace() for character in token):
        raise HTTPException(401, "Workspace session required")
    return await run_in_threadpool(
        resolve_session, request.app.state.workspace_settings.state_path,
        owner, str(workspace_id), token,
    )


Binding = Annotated[WorkspaceBinding, Depends(session_binding)]


class WorkspaceRequestBoundary:
    """Bound JSON before FastAPI parsing and prevent caching even error responses."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not (
            scope["path"] == "/workspaces" or scope["path"].startswith("/workspaces/")
        ):
            await self.app(scope, receive, send)
            return

        async def private_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = [
                    (key, value) for key, value in message.get("headers", [])
                    if key.lower() not in (b"cache-control", b"vary")
                ] + [(b"cache-control", b"private, no-store"), (b"vary", b"Cookie, Authorization")]
            await send(message)

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > WORKSPACE_HTTP_MAX_BYTES:
                await JSONResponse({"detail": "Workspace request too large"}, status_code=413)(
                    scope, receive, private_send,
                )
                return
            if not message.get("more_body", False):
                break
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, private_send)


async def service_request(request: Request, method: str, path: str, **kwargs) -> dict:
    try:
        async with request.app.state.workspace_service.stream(method, path, **kwargs) as result:
            if result.status_code not in (200, 201):
                status = result.status_code if result.status_code in (404, 409, 412, 413, 422) else 503
                raise HTTPException(status, "Workspace service request failed")
            body = bytearray()
            async for chunk in result.aiter_bytes():
                body.extend(chunk)
                if len(body) > WORKSPACE_HTTP_MAX_BYTES:
                    raise HTTPException(503, "Workspace response exceeds limit")
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError
            return payload
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, "Workspace service unavailable") from None


async def check_service(request: Request, binding: WorkspaceBinding):
    status = await service_request(request, "GET", "/health")
    if status != {"workspace_id": binding.workspace_id, "filesystem": "ready"}:
        raise HTTPException(503, "Workspace service binding mismatch")


@router.post("/workspaces", response_model=WorkspaceBinding)
async def register(request: Request, owner: Owner):
    return await run_in_threadpool(
        register_workspace, request.app.state.workspace_settings.state_path, owner,
    )


@router.post("/workspaces/{workspace_id}/sessions", status_code=201)
async def new_session(workspace_id: UUID, request: Request, owner: Owner):
    return await run_in_threadpool(
        issue_session, request.app.state.workspace_settings.state_path, owner, str(workspace_id),
    )


@router.delete("/workspaces/{workspace_id}/sessions/current", status_code=204)
async def end_session(request: Request, binding: Binding):
    await run_in_threadpool(revoke_session, request.app.state.workspace_settings.state_path, binding)


@router.get("/workspaces/{workspace_id}")
async def snapshot(request: Request, binding: Binding):
    path = request.app.state.workspace_settings.state_path
    lease = await run_in_threadpool(get_lease, path, binding)
    projects = await run_in_threadpool(list_projects, path, binding)
    return {"binding": binding, "controller": lease, "projects": projects,
            "desktop_control_enabled": False}


@router.get("/workspaces/{workspace_id}/projects")
async def projects(request: Request, binding: Binding):
    return await run_in_threadpool(list_projects, request.app.state.workspace_settings.state_path, binding)


class PrepareProject(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=128, pattern=r"^[^\x00-\x1f\x7f]+$")
    desktop_epoch: int = Field(ge=1)
    lease_epoch: int = Field(ge=0)

    @field_validator("name")
    @classmethod
    def project_label(cls, value: str) -> str:
        _validate_name(value)
        return value


@router.post("/workspaces/{workspace_id}/projects/prepare", status_code=201)
async def prepare_project(body: PrepareProject, request: Request, binding: Binding):
    path = request.app.state.workspace_settings.state_path
    operation_id = str(uuid4())
    await run_in_threadpool(
        accept_operation, path, binding, operation_id, body.desktop_epoch,
        body.lease_epoch, "human", WORKSPACE_PROJECT_CREATE_ACTION, {"name": body.name},
        max_mutations=DESKTOP_MAX_MUTATIONS,
        require_active_session=True,
    )
    return await run_in_threadpool(reserve_project, path, binding, body.name, operation_id)


@router.post("/workspaces/{workspace_id}/projects/{project_id}/create")
async def create_project(project_id: UUID, request: Request, binding: Binding):
    path = request.app.state.workspace_settings.state_path
    project = await run_in_threadpool(get_project, path, binding, str(project_id))
    operation = await run_in_threadpool(get_operation, path, binding, project.operation_id)
    if operation.state in ("succeeded", "failed", "cancelled", "outcome_unknown"):
        return operation
    await check_service(request, binding)
    await run_in_threadpool(start_operation, path, binding, operation.operation_id, require_active_session=True)
    try:
        result = await service_request(request, "POST", f"/projects/{project.project_id}")
        if result != {"project_id": project.project_id}:
            raise HTTPException(503, "Workspace project acknowledgment invalid")
    except Exception:
        # Dispatch may have created a directory, including when its reply is lost.
        await run_in_threadpool(
            finish_project_operation, path, binding, project.project_id, succeeded=False,
        )
        raise
    return await run_in_threadpool(
        finish_project_operation, path, binding, project.project_id, succeeded=True,
    )


@router.get("/workspaces/{workspace_id}/operations/{operation_id}")
async def operation(operation_id: UUID, request: Request, binding: Binding):
    return await run_in_threadpool(
        get_operation, request.app.state.workspace_settings.state_path, binding, str(operation_id),
    )


@router.post("/workspaces/{workspace_id}/projects/{project_id}/open", response_model=WorkspaceBinding)
async def select_project(project_id: UUID, request: Request, binding: Binding):
    return await run_in_threadpool(
        open_project, request.app.state.workspace_settings.state_path, binding, str(project_id),
    )


@router.get("/workspaces/{workspace_id}/projects/{project_id}/{view}")
async def project_files(
    project_id: UUID, view: Literal["files", "file", "search"], request: Request,
    binding: Binding, path: str = "", query: str = "", expected_sha256: str | None = None,
):
    project = await run_in_threadpool(
        get_project, request.app.state.workspace_settings.state_path, binding, str(project_id),
    )
    if project.state != "ready":
        raise HTTPException(409, "Project is not ready")
    await check_service(request, binding)
    params = {"path": path}
    if view == "search":
        params["query"] = query
    if view == "file" and expected_sha256 is not None:
        params["expected_sha256"] = expected_sha256
    return await service_request(request, "GET", f"/projects/{project.project_id}/{view}", params=params)
