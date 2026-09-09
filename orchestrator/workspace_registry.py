"""Trusted workspace registry: single-owner pilot registration, sessions, projects.

Extends the control database owned by workspace_state with registry tables.
Call from service/activity threads, never workflow code. The service lifecycle
calls initialize_registry once against an already-initialized, trusted control
database; no other function creates tables or databases. Raw session tokens are
returned exactly once and never persisted; only their SHA-256 digest is stored.
Project names are labels, never filesystem roots: callers never supply paths.
"""

import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from config import (
    WORKSPACE_MAX_PROJECTS, WORKSPACE_SESSION_TTL, WORKSPACE_PROJECT_CREATE_ACTION,
    WORKSPACE_TOKEN_BYTES,
)
from workspace_state import (
    ControllerLease,
    ScopeUnavailable,
    StateConflict,
    WorkspaceBinding,
    _lease,
    _transaction,
    _operation,
    _json,
)

_NAME_FORBIDDEN = frozenset("/\\\x00")


class ProjectRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    workspace_id: Annotated[str, Field(min_length=1, max_length=128)]
    project_id: Annotated[str, Field(min_length=1, max_length=128)]
    name: Annotated[str, Field(min_length=1, max_length=128)]
    state: Literal["pending", "ready", "outcome_unknown"]
    operation_id: Annotated[str, Field(min_length=1, max_length=128)]


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def initialize_registry(path: Path) -> None:
    """Explicit service-lifecycle bootstrap on an existing trusted control database.

    Never creates the database: _transaction opens mode=rw and rejects unknown
    schemas, so a missing or foreign file fails closed instead of being adopted.
    """
    with _transaction(path) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS registry_workspaces (
                workspace_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                bootstrap_session_id TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS registry_sessions (
                workspace_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                issued_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                revoked_at REAL,
                PRIMARY KEY (workspace_id, session_id)
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                workspace_id TEXT NOT NULL,
                project_id TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                name TEXT NOT NULL,
                state TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE (workspace_id, operation_id)
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS session_projects (
                workspace_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                PRIMARY KEY (workspace_id, session_id)
            )
        """)


def register_workspace(path: Path, owner_id: str) -> WorkspaceBinding:
    """Single-owner pilot: the control database holds at most one workspace.

    The bootstrap session is a server-side binding without a token; issue_session
    mints revocable token-backed sessions for transports. Repeat registration by
    the same owner returns the existing binding; any other owner sees nothing.
    """
    if not owner_id or len(owner_id) > 256:
        raise ValueError("Invalid owner identity")
    with _transaction(path) as connection:
        existing = connection.execute("SELECT workspace_id, owner_id FROM workspaces LIMIT 1").fetchone()
        if existing is not None:
            if existing["owner_id"] != owner_id:
                raise ScopeUnavailable("Workspace unavailable")
            registered = connection.execute(
                "SELECT bootstrap_session_id FROM registry_workspaces WHERE workspace_id=? AND owner_id=?",
                (existing["workspace_id"], owner_id),
            ).fetchone()
            if registered is None:
                raise StateConflict("Workspace exists outside the registry")
            return WorkspaceBinding(
                owner_id=owner_id,
                workspace_id=existing["workspace_id"],
                session_id=registered["bootstrap_session_id"],
            )
        binding = WorkspaceBinding(owner_id=owner_id, workspace_id=str(uuid4()), session_id=str(uuid4()))
        connection.execute(
            "INSERT INTO workspaces VALUES (?, ?, 1, 0, 'none', 'recovery_required', NULL, 0, NULL, NULL)",
            (binding.workspace_id, binding.owner_id),
        )
        connection.execute(
            "INSERT INTO sessions VALUES (?, ?)", (binding.workspace_id, binding.session_id)
        )
        connection.execute(
            "INSERT INTO registry_workspaces VALUES (?, ?, ?, ?)",
            (binding.workspace_id, binding.owner_id, binding.session_id, _now()),
        )
        return binding


def issue_session(path: Path, owner_id: str, workspace_id: str) -> dict:
    """Mint a token-backed session; the raw token leaves this function exactly once."""
    token = secrets.token_urlsafe(WORKSPACE_TOKEN_BYTES)
    binding = WorkspaceBinding(owner_id=owner_id, workspace_id=workspace_id, session_id=str(uuid4()))
    now = _now()
    expires_at = (datetime.now(timezone.utc) + WORKSPACE_SESSION_TTL).timestamp()
    with _transaction(path) as connection:
        if not connection.execute(
            """SELECT 1 FROM workspaces w JOIN registry_workspaces r USING (workspace_id)
               WHERE w.workspace_id=? AND w.owner_id=? AND r.owner_id=?""",
            (workspace_id, owner_id, owner_id),
        ).fetchone():
            raise ScopeUnavailable("Workspace unavailable")
        connection.execute("INSERT INTO sessions VALUES (?, ?)", (workspace_id, binding.session_id))
        connection.execute(
            "INSERT INTO registry_sessions VALUES (?, ?, ?, ?, ?, ?, NULL)",
            (workspace_id, binding.session_id, owner_id, _digest(token), now, expires_at),
        )
    return {"token": token, "binding": binding, "expires_at": expires_at}


def _registry_session(connection: sqlite3.Connection, binding: WorkspaceBinding) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM registry_sessions WHERE workspace_id=? AND session_id=? AND owner_id=?",
        (binding.workspace_id, binding.session_id, binding.owner_id),
    ).fetchone()


def _session_active(row: sqlite3.Row) -> bool:
    return row["revoked_at"] is None and row["expires_at"] > _now()


def _authorize(connection: sqlite3.Connection, binding: WorkspaceBinding) -> ControllerLease:
    """Scope check: underlying workspace/owner/session plus registry revocation and expiry."""
    lease = _lease(connection, binding)
    row = _registry_session(connection, binding)
    bootstrap = connection.execute(
        "SELECT 1 FROM registry_workspaces WHERE workspace_id=? AND owner_id=? AND bootstrap_session_id=?",
        (binding.workspace_id, binding.owner_id, binding.session_id),
    ).fetchone()
    if (row is None and bootstrap is None) or (row is not None and not _session_active(row)):
        raise ScopeUnavailable("Session unavailable")
    return lease


def resolve_session(path: Path, owner_id: str, workspace_id: str, token: str) -> WorkspaceBinding:
    """Map a presented token to its binding; every failure is indistinguishable."""
    if not token or len(token) > 256:
        raise ScopeUnavailable("Session unavailable")
    digest = _digest(token)
    with _transaction(path, write=False) as connection:
        row = connection.execute(
            "SELECT * FROM registry_sessions WHERE token_hash=?", (digest,)
        ).fetchone()
        if (
            row is None
            or not hmac.compare_digest(row["token_hash"], digest)
            or row["workspace_id"] != workspace_id
            or row["owner_id"] != owner_id
            or not _session_active(row)
        ):
            raise ScopeUnavailable("Session unavailable")
        binding = WorkspaceBinding(owner_id=owner_id, workspace_id=workspace_id, session_id=row["session_id"])
        try:
            _lease(connection, binding)
        except ScopeUnavailable:
            raise ScopeUnavailable("Session unavailable") from None
        selected = connection.execute(
            """SELECT p.project_id FROM session_projects s JOIN projects p
               ON p.project_id=s.project_id AND p.workspace_id=s.workspace_id
               WHERE s.workspace_id=? AND s.session_id=? AND p.state='ready'""",
            (workspace_id, binding.session_id),
        ).fetchone()
        return binding.model_copy(update={"project_id": selected["project_id"] if selected else None})


def revoke_session(path: Path, binding: WorkspaceBinding) -> ControllerLease:
    """Revoke a token-backed session and fence any control it held.

    Fencing is a journal action only: it never asserts that the remote desktop,
    viewer, or dispatcher stopped. Running operations become outcome_unknown and
    require reconciliation; the sessions row survives for trusted cleanup.
    """
    with _transaction(path) as connection:
        lease = _lease(connection, binding)
        row = _registry_session(connection, binding)
        if row is None:
            raise ScopeUnavailable("Session unavailable")
        if row["revoked_at"] is not None:
            return lease
        now = _now()
        connection.execute(
            "UPDATE registry_sessions SET revoked_at=? WHERE workspace_id=? AND session_id=?",
            (now, binding.workspace_id, binding.session_id),
        )
        connection.execute(
            """UPDATE operations SET state=CASE WHEN state='running'
               THEN 'outcome_unknown' ELSE 'cancelled' END, updated_at=?
               WHERE workspace_id=? AND session_id=? AND state IN ('accepted', 'running')""",
            (now, binding.workspace_id, binding.session_id),
        )
        if lease.session_id == binding.session_id:
            connection.execute(
                """UPDATE operations SET state='cancelled', updated_at=?
                   WHERE workspace_id=? AND state='accepted'""",
                (now, binding.workspace_id),
            )
            connection.execute(
                """UPDATE workspaces SET desktop_epoch=desktop_epoch+1,
                   lease_epoch=lease_epoch+1, controller='none', state='recovery_required',
                   session_id=NULL, expires_at=0, transition_id=NULL, revocation_receipt=NULL
                   WHERE workspace_id=?""",
                (binding.workspace_id,),
            )
        return _lease(connection, binding)


def _project(row: sqlite3.Row) -> ProjectRecord:
    return ProjectRecord(
        workspace_id=row["workspace_id"],
        project_id=row["project_id"],
        name=row["name"],
        state=row["state"],
        operation_id=row["operation_id"],
    )


def _validate_name(name: str) -> None:
    if not name or len(name) > 128 or name != name.strip() or _NAME_FORBIDDEN & set(name):
        raise ValueError("Invalid project name")
    if not name.isprintable():
        raise ValueError("Invalid project name")


def reserve_project(
    path: Path, binding: WorkspaceBinding, name: str, operation_id: str
) -> ProjectRecord:
    """Reserve an opaque project ID against a journaled project.create operation.

    Idempotent by workspace operation ID for the same name and session; the
    server generates the project ID, so the record never carries a root path.
    """
    _validate_name(name)
    if not operation_id or len(operation_id) > 128:
        raise ValueError("Invalid operation identity")
    with _transaction(path) as connection:
        _authorize(connection, binding)
        existing = connection.execute(
            "SELECT * FROM projects WHERE workspace_id=? AND operation_id=?",
            (binding.workspace_id, operation_id),
        ).fetchone()
        if existing is not None:
            if existing["name"] != name or existing["session_id"] != binding.session_id:
                raise StateConflict("Operation ID already reserved a different project")
            return _project(existing)
        operation = connection.execute(
            "SELECT action_type, state FROM operations WHERE workspace_id=? AND operation_id=? AND session_id=?",
            (binding.workspace_id, operation_id, binding.session_id),
        ).fetchone()
        if operation is None:
            raise ScopeUnavailable("Operation unavailable")
        if operation["action_type"] != WORKSPACE_PROJECT_CREATE_ACTION:
            raise StateConflict("Operation is not a project creation")
        if operation["state"] not in ("accepted", "running"):
            raise StateConflict("Operation is not active")
        count = connection.execute(
            "SELECT count(*) FROM projects WHERE workspace_id=?", (binding.workspace_id,)
        ).fetchone()[0]
        if count >= WORKSPACE_MAX_PROJECTS:
            raise StateConflict("Project quota exhausted")
        now = _now()
        project_id = str(uuid4())
        connection.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
            (binding.workspace_id, project_id, operation_id, binding.session_id, name, now, now),
        )
        return ProjectRecord(
            workspace_id=binding.workspace_id, project_id=project_id, name=name,
            state="pending", operation_id=operation_id,
        )


def list_projects(path: Path, binding: WorkspaceBinding) -> list[ProjectRecord]:
    with _transaction(path, write=False) as connection:
        _authorize(connection, binding)
        rows = connection.execute(
            "SELECT * FROM projects WHERE workspace_id=? ORDER BY created_at, project_id",
            (binding.workspace_id,),
        ).fetchall()
        return [_project(row) for row in rows]


def get_project(path: Path, binding: WorkspaceBinding, project_id: str) -> ProjectRecord:
    with _transaction(path, write=False) as connection:
        _authorize(connection, binding)
        row = connection.execute(
            "SELECT * FROM projects WHERE workspace_id=? AND project_id=?",
            (binding.workspace_id, project_id),
        ).fetchone()
        if row is None:
            raise ScopeUnavailable("Project unavailable")
        return _project(row)


def open_project(path: Path, binding: WorkspaceBinding, project_id: str) -> WorkspaceBinding:
    """Persist this session's selected registered project; never select a host path."""
    with _transaction(path) as connection:
        _authorize(connection, binding)
        project = connection.execute(
            "SELECT state FROM projects WHERE workspace_id=? AND project_id=?",
            (binding.workspace_id, project_id),
        ).fetchone()
        if project is None:
            raise ScopeUnavailable("Project unavailable")
        if project["state"] != "ready":
            raise StateConflict("Project is not ready")
        connection.execute(
            """INSERT INTO session_projects VALUES (?, ?, ?)
               ON CONFLICT(workspace_id, session_id) DO UPDATE SET project_id=excluded.project_id""",
            (binding.workspace_id, binding.session_id, project_id),
        )
        return binding.model_copy(update={"project_id": project_id})


def finish_project(
    path: Path,
    binding: WorkspaceBinding,
    project_id: str,
    state: Literal["ready", "outcome_unknown"],
) -> ProjectRecord:
    """Only pending projects change; identical terminal states are idempotent."""
    if state not in ("ready", "outcome_unknown"):
        raise ValueError("Terminal project state required")
    with _transaction(path) as connection:
        _authorize(connection, binding)
        row = connection.execute(
            "SELECT * FROM projects WHERE workspace_id=? AND project_id=?",
            (binding.workspace_id, project_id),
        ).fetchone()
        if row is None:
            raise ScopeUnavailable("Project unavailable")
        project = _project(row)
        if project.state == state:
            return project
        if project.state != "pending":
            raise StateConflict("Conflicting terminal project state")
        connection.execute(
            "UPDATE projects SET state=?, updated_at=? WHERE workspace_id=? AND project_id=?",
            (state, _now(), binding.workspace_id, project_id),
        )
        return project.model_copy(update={"state": state})


def finish_project_operation(path: Path, binding: WorkspaceBinding, project_id: str, *, succeeded: bool):
    """Trusted dispatch completion: settle project and journal atomically.

    Cleanup can arrive after session expiry/revocation. Never turn an operation
    made unknown by revocation into success, even with a late service response.
    """
    with _transaction(path) as connection:
        _lease(connection, binding)
        project = connection.execute(
            "SELECT * FROM projects WHERE workspace_id=? AND project_id=? AND session_id=?",
            (binding.workspace_id, project_id, binding.session_id),
        ).fetchone()
        if project is None:
            raise ScopeUnavailable("Project unavailable")
        row = connection.execute(
            "SELECT * FROM operations WHERE workspace_id=? AND operation_id=? AND session_id=?",
            (binding.workspace_id, project["operation_id"], binding.session_id),
        ).fetchone()
        if row is None:
            raise ScopeUnavailable("Operation unavailable")
        operation = _operation(row)
        if operation.state not in ("running", "outcome_unknown"):
            raise StateConflict("Project operation is not completing")
        known = succeeded and operation.state == "running"
        project_state = "ready" if known else "outcome_unknown"
        if project["state"] not in ("pending", project_state):
            raise StateConflict("Conflicting terminal project state")
        now = _now()
        connection.execute(
            "UPDATE projects SET state=?, updated_at=? WHERE project_id=?",
            (project_state, now, project_id),
        )
        if operation.state == "outcome_unknown":
            return operation
        state = "succeeded" if known else "outcome_unknown"
        result = {"project_id": project_id} if known else None
        connection.execute(
            "UPDATE operations SET state=?, result=?, updated_at=? WHERE workspace_id=? AND operation_id=?",
            (state, _json(result), now, binding.workspace_id, operation.operation_id),
        )
        return operation.model_copy(update={"state": state, "result": result, "updated_at": now})
