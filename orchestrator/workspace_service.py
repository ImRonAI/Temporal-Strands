"""Private workspace-side filesystem API, never loaded by the Strands worker.

Run as the desktop UID in a restricted mount namespace. The Unix socket and its
parent must be inaccessible to project processes, including same-UID processes.
Only the trusted control API may connect; there is deliberately no TCP listener
or caller-selected root. Project creation is dispatched once by its journal.
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from config import WORKSPACE_PATH_MAX_BYTES, WORKSPACE_SEARCH_QUERY_MAX_BYTES

from workspace_files import (
    FileLimitExceeded, FilePreconditionFailed, FileServiceUnavailable,
    FileUnavailable, _directory, list_project_files, read_project_file,
    search_project_files,
)


def create_workspace_service(projects_root: Path, workspace_id: UUID) -> FastAPI:
    """Open only an existing mounted root; never create missing mount parents."""
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        descriptor = os.open(
            projects_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        app.state.projects_fd = descriptor
        try:
            yield
        finally:
            os.close(descriptor)

    app = FastAPI(lifespan=lifespan)

    @app.exception_handler(FileUnavailable)
    @app.exception_handler(FileLimitExceeded)
    @app.exception_handler(FilePreconditionFailed)
    @app.exception_handler(FileServiceUnavailable)
    @app.exception_handler(OSError)
    @app.exception_handler(ValueError)
    async def error_response(request: Request, error: Exception):
        status = 503
        if isinstance(error, FileUnavailable):
            status = 404
        elif isinstance(error, FileLimitExceeded):
            status = 413
        elif isinstance(error, FilePreconditionFailed):
            status = 412
        elif isinstance(error, ValueError):
            status = 422
        return JSONResponse({"detail": "Workspace filesystem request failed"}, status_code=status)

    @app.get("/health")
    def health():
        os.fstat(app.state.projects_fd)
        return {"workspace_id": str(workspace_id), "filesystem": "ready"}

    @app.post("/projects/{project_id}", status_code=201)
    def create_project(project_id: UUID):
        # Existing roots are never adopted: a lost response requires reconciliation.
        try:
            os.mkdir(str(project_id), mode=0o700, dir_fd=app.state.projects_fd)
        except FileExistsError:
            raise HTTPException(409, "Project root already exists; reconcile operation") from None
        os.fsync(app.state.projects_fd)
        return {"project_id": str(project_id)}

    @app.get("/projects/{project_id}/files")
    def files(project_id: UUID, path: str = Query(default="", max_length=WORKSPACE_PATH_MAX_BYTES)):
        with _directory(app.state.projects_fd, [str(project_id)]) as root:
            return list_project_files(root, path)

    @app.get("/projects/{project_id}/file")
    def file(project_id: UUID, path: str = Query(max_length=WORKSPACE_PATH_MAX_BYTES), expected_sha256: str | None = None):
        with _directory(app.state.projects_fd, [str(project_id)]) as root:
            return read_project_file(root, path, expected_sha256=expected_sha256)

    @app.get("/projects/{project_id}/search")
    def search(project_id: UUID, query: str = Query(max_length=WORKSPACE_SEARCH_QUERY_MAX_BYTES), path: str = ""):
        with _directory(app.state.projects_fd, [str(project_id)]) as root:
            return search_project_files(root, query, path)

    return app


def configured_app() -> FastAPI:
    """uvicorn workspace_service:configured_app --factory --uds <private-socket>."""
    return create_workspace_service(
        Path(os.environ["GWEN_WORKSPACE_PROJECTS_ROOT"]),
        UUID(os.environ["GWEN_WORKSPACE_ID"]),
    )
