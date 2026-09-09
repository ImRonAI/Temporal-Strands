"""Trusted control-plane state, not model tools or a desktop execution service.

Call from service/activity threads, never workflow code. The caller supplies an
authenticated owner and server-resolved session binding. The workspace API uses
bounded admission/dispatch; control acquisition stays private until native VNC
revocation passes. No worker tool exposes these functions directly.
Bindings identify owner/session, not roles: only the trusted control service may
authorize human takeover, target roles, and recovery callbacks.
"""

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Iterator, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from config import (
    DESKTOP_LEASE_TTL,
    DESKTOP_RECORD_MAX_BYTES,
    DESKTOP_SQLITE_TIMEOUT_SECONDS,
)


class WorkspaceBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    owner_id: Annotated[str, Field(min_length=1, max_length=256)]
    workspace_id: Annotated[str, Field(min_length=1, max_length=128)]
    project_id: str | None = None
    session_id: Annotated[str, Field(min_length=1, max_length=256)]


class ControllerLease(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    workspace_id: str
    desktop_epoch: int
    lease_epoch: int
    controller: Literal["agent", "human", "none"]
    state: Literal["active", "transitioning", "recovery_required"]
    session_id: str | None
    expires_at: float
    transition_id: str | None


class OperationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    workspace_id: str
    operation_id: str
    request_hash: str
    desktop_epoch: int
    lease_epoch: int
    session_id: str
    actor: Literal["agent", "human"]
    action_type: str
    state: Literal[
        "accepted", "running", "succeeded", "failed", "cancelled", "outcome_unknown"
    ]
    created_at: float
    updated_at: float
    result: JsonValue = None
    reconciliation: JsonValue = None


class StateConflict(Exception):
    """Map to HTTP 409 at an authenticated service boundary."""


class ScopeUnavailable(Exception):
    """Map to HTTP 404 without disclosing another owner's resources."""


@contextmanager
def _transaction(path: Path, *, write: bool = True) -> Iterator[sqlite3.Connection]:
    # mode=rw prevents a missing data disk/database becoming an empty database.
    connection = sqlite3.connect(
        path.resolve().as_uri() + "?mode=rw",
        uri=True,
        timeout=DESKTOP_SQLITE_TIMEOUT_SECONDS,
        isolation_level=None,
    )
    connection.row_factory = sqlite3.Row
    try:
        if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise StateConflict("Unsupported control database schema")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_state(path: Path) -> None:
    """Explicit bootstrap only; parent must already be a trusted mounted directory.

    Interrupted initialization fails closed. An operator must verify and remove
    only the incomplete new database before retrying; never overwrite live state.
    """
    # Exclusive creation also avoids silently adopting an unknown database.
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    connection = sqlite3.connect(path)
    try:
        connection.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            BEGIN IMMEDIATE;
            CREATE TABLE workspaces (
                workspace_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                desktop_epoch INTEGER NOT NULL,
                lease_epoch INTEGER NOT NULL,
                controller TEXT NOT NULL,
                state TEXT NOT NULL,
                session_id TEXT,
                expires_at REAL NOT NULL,
                transition_id TEXT,
                revocation_receipt TEXT
            );
            CREATE TABLE sessions (
                workspace_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                PRIMARY KEY (workspace_id, session_id)
            );
            CREATE TABLE operations (
                workspace_id TEXT NOT NULL,
                operation_id TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                desktop_epoch INTEGER NOT NULL,
                lease_epoch INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                actor TEXT NOT NULL,
                action_type TEXT NOT NULL,
                state TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                result TEXT,
                reconciliation TEXT,
                PRIMARY KEY (workspace_id, operation_id)
            );
            CREATE INDEX operations_state ON operations (workspace_id, state);
            PRAGMA user_version=1;
            COMMIT;
        """)
    finally:
        connection.close()


def create_workspace(path: Path, owner_id: str, session_id: str) -> WorkspaceBinding:
    binding = WorkspaceBinding(
        owner_id=owner_id, workspace_id=str(uuid4()), session_id=session_id
    )
    with _transaction(path) as connection:
        connection.execute(
            "INSERT INTO workspaces VALUES (?, ?, 1, 0, 'none', 'recovery_required', NULL, 0, NULL, NULL)",
            (binding.workspace_id, binding.owner_id),
        )
        connection.execute(
            "INSERT INTO sessions VALUES (?, ?)",
            (binding.workspace_id, binding.session_id),
        )
    return binding


def bind_session(
    path: Path, owner_id: str, workspace_id: str, session_id: str
) -> WorkspaceBinding:
    binding = WorkspaceBinding(
        owner_id=owner_id, workspace_id=workspace_id, session_id=session_id
    )
    with _transaction(path) as connection:
        if not connection.execute(
            "SELECT 1 FROM workspaces WHERE workspace_id=? AND owner_id=?",
            (workspace_id, owner_id),
        ).fetchone():
            raise ScopeUnavailable("Workspace unavailable")
        connection.execute(
            "INSERT OR IGNORE INTO sessions VALUES (?, ?)", (workspace_id, session_id)
        )
    return binding


def _lease(connection: sqlite3.Connection, binding: WorkspaceBinding) -> ControllerLease:
    row = connection.execute(
        """SELECT w.* FROM workspaces w JOIN sessions s USING (workspace_id)
           WHERE w.workspace_id=? AND w.owner_id=? AND s.session_id=?""",
        (binding.workspace_id, binding.owner_id, binding.session_id),
    ).fetchone()
    if row is None:
        raise ScopeUnavailable("Workspace unavailable")
    values = dict(row)
    del values["owner_id"], values["revocation_receipt"]
    return ControllerLease(**values)


def get_lease(path: Path, binding: WorkspaceBinding) -> ControllerLease:
    with _transaction(path, write=False) as connection:
        return _lease(connection, binding)


def _check_epochs(lease: ControllerLease, desktop_epoch: int, lease_epoch: int) -> None:
    if (lease.desktop_epoch, lease.lease_epoch) != (desktop_epoch, lease_epoch):
        raise StateConflict("Stale controller or desktop epoch")


def begin_control_transition(
    path: Path, binding: WorkspaceBinding, desktop_epoch: int, lease_epoch: int
) -> ControllerLease:
    """Fence admissions immediately; never grant control before remote cleanup."""
    with _transaction(path) as connection:
        lease = _lease(connection, binding)
        _check_epochs(lease, desktop_epoch, lease_epoch)
        if lease.state == "transitioning":
            raise StateConflict("Control transition already pending")
        connection.execute(
            """UPDATE workspaces SET lease_epoch=lease_epoch+1, controller='none',
               state='transitioning', session_id=?, expires_at=0,
               transition_id=?, revocation_receipt=NULL WHERE workspace_id=?""",
            (binding.session_id, str(uuid4()), binding.workspace_id),
        )
        connection.execute(
            """UPDATE operations SET state='cancelled', updated_at=?
               WHERE workspace_id=? AND state='accepted'""",
            (datetime.now(timezone.utc).timestamp(), binding.workspace_id),
        )
        return _lease(connection, binding)


def complete_control_transition(
    path: Path,
    binding: WorkspaceBinding,
    transition_id: str,
    controller: Literal["agent", "human", "none"],
    *,
    revocation_receipt: str,
    target_session_id: str | None = None,
) -> ControllerLease:
    """Trusted transport callback ONLY, after revoking viewers and settling jobs.

    The receipt is an audit reference, not proof supplied by a browser/model.
    No transport currently invokes this function. Native revocation remains a
    release gate. Every handoff invalidates observations via desktop_epoch.
    """
    if controller not in ("agent", "human", "none") or not revocation_receipt.strip():
        raise ValueError("Controller and native revocation receipt required")
    if len(revocation_receipt) > 256:
        raise ValueError("Revocation receipt too large")
    with _transaction(path) as connection:
        lease = _lease(connection, binding)
        if (
            lease.state != "transitioning"
            or lease.transition_id != transition_id
            or lease.session_id != binding.session_id
        ):
            raise StateConflict("Stale control transition")
        target = target_session_id or binding.session_id
        if not connection.execute(
            "SELECT 1 FROM sessions WHERE workspace_id=? AND session_id=?",
            (binding.workspace_id, target),
        ).fetchone():
            raise ScopeUnavailable("Target session unavailable")
        if connection.execute(
            """SELECT 1 FROM operations WHERE workspace_id=?
               AND (state='running' OR (state='outcome_unknown' AND reconciliation IS NULL)) LIMIT 1""",
            (binding.workspace_id,),
        ).fetchone():
            raise StateConflict("Operation cleanup or reconciliation required")
        expires_at = (datetime.now(timezone.utc) + DESKTOP_LEASE_TTL).timestamp()
        connection.execute(
            """UPDATE workspaces SET desktop_epoch=desktop_epoch+1, controller=?,
               state='active', expires_at=?, transition_id=NULL, revocation_receipt=?, session_id=?
               WHERE workspace_id=?""",
            (controller, expires_at if controller != "none" else 0, revocation_receipt,
             target if controller != "none" else None, binding.workspace_id),
        )
        return _lease(connection, binding)


def _json(value: JsonValue) -> str:
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("Control record must contain JSON values") from error
    if len(encoded.encode()) > DESKTOP_RECORD_MAX_BYTES:
        raise ValueError("Control record exceeds size limit")
    return encoded


def _operation(row: sqlite3.Row) -> OperationRecord:
    values = dict(row)
    values["result"] = json.loads(values["result"]) if values["result"] else None
    values["reconciliation"] = json.loads(values["reconciliation"]) if values["reconciliation"] else None
    return OperationRecord(**values)


def get_operation(
    path: Path, binding: WorkspaceBinding, operation_id: str
) -> OperationRecord:
    with _transaction(path, write=False) as connection:
        _lease(connection, binding)
        row = connection.execute(
            "SELECT * FROM operations WHERE workspace_id=? AND operation_id=? AND session_id=?",
            (binding.workspace_id, operation_id, binding.session_id),
        ).fetchone()
        if row is None:
            raise ScopeUnavailable("Operation unavailable")
        return _operation(row)


def _check_controller(lease: ControllerLease, session_id: str, actor: str) -> None:
    if (
        lease.state != "active"
        or lease.controller != actor
        or lease.session_id != session_id
        or lease.expires_at <= datetime.now(timezone.utc).timestamp()
    ):
        raise StateConflict("Controller unavailable or expired")


def renew_control_lease(
    path: Path, binding: WorkspaceBinding, desktop_epoch: int, lease_epoch: int
) -> ControllerLease:
    """Renew only the current controller; expired leases require a new handoff."""
    with _transaction(path) as connection:
        lease = _lease(connection, binding)
        _check_epochs(lease, desktop_epoch, lease_epoch)
        if lease.controller == "none":
            raise StateConflict("No controller to renew")
        _check_controller(lease, binding.session_id, lease.controller)
        expires_at = (datetime.now(timezone.utc) + DESKTOP_LEASE_TTL).timestamp()
        connection.execute(
            "UPDATE workspaces SET expires_at=? WHERE workspace_id=?",
            (expires_at, binding.workspace_id),
        )
        return _lease(connection, binding)


def accept_operation(
    path: Path,
    binding: WorkspaceBinding,
    operation_id: str,
    desktop_epoch: int,
    lease_epoch: int,
    actor: Literal["agent", "human"],
    action_type: str,
    request: JsonValue,
    *,
    max_mutations: int | None = None,
    require_active_session: bool = False,
) -> OperationRecord:
    """Journal a trusted server-issued operation ID before any remote dispatch.

    Only the hash is persisted: raw command text, clipboard, or image bytes are
    not journal payloads. Returning an existing record never authorizes dispatch;
    start_operation must win the accepted -> running transition exactly once.
    """
    if not operation_id or len(operation_id) > 128 or not action_type or len(action_type) > 128:
        raise ValueError("Invalid operation identity")
    if max_mutations is not None and (type(max_mutations) is not int or max_mutations < 1):
        raise ValueError("Positive mutation budget required")
    if actor not in ("agent", "human"):
        raise ValueError("Invalid actor")
    digest = hashlib.sha256(_json({
        "request": request, "action_type": action_type, "actor": actor,
        "session_id": binding.session_id, "desktop_epoch": desktop_epoch,
        "lease_epoch": lease_epoch, "project_id": binding.project_id,
    }).encode()).hexdigest()
    with _transaction(path) as connection:
        lease = _lease(connection, binding)
        if require_active_session:
            from workspace_registry import _authorize
            _authorize(connection, binding)
        existing = connection.execute(
            "SELECT * FROM operations WHERE workspace_id=? AND operation_id=?",
            (binding.workspace_id, operation_id),
        ).fetchone()
        if existing:
            if existing["request_hash"] != digest:
                raise StateConflict("Operation ID already has a different request")
            return _operation(existing)
        _check_epochs(lease, desktop_epoch, lease_epoch)
        _check_controller(lease, binding.session_id, actor)
        if max_mutations is not None:
            count = connection.execute(
                "SELECT count(*) FROM operations WHERE workspace_id=? AND session_id=?",
                (binding.workspace_id, binding.session_id),
            ).fetchone()[0]
            if count >= max_mutations:
                raise StateConflict("Session mutation budget exhausted")
        now = datetime.now(timezone.utc).timestamp()
        connection.execute(
            "INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'accepted', ?, ?, NULL, NULL)",
            (binding.workspace_id, operation_id, digest, desktop_epoch, lease_epoch,
             binding.session_id, actor, action_type, now, now),
        )
        row = connection.execute(
            "SELECT * FROM operations WHERE workspace_id=? AND operation_id=?",
            (binding.workspace_id, operation_id),
        ).fetchone()
        return _operation(row)


def start_operation(
    path: Path, binding: WorkspaceBinding, operation_id: str, *, require_active_session: bool = False,
) -> OperationRecord:
    """Only a successful return authorizes one dispatch; running is never replayed."""
    with _transaction(path) as connection:
        lease = _lease(connection, binding)
        if require_active_session:
            from workspace_registry import _authorize
            _authorize(connection, binding)
        row = connection.execute(
            "SELECT * FROM operations WHERE workspace_id=? AND operation_id=? AND session_id=?",
            (binding.workspace_id, operation_id, binding.session_id),
        ).fetchone()
        if row is None:
            raise ScopeUnavailable("Operation unavailable")
        operation = _operation(row)
        _check_epochs(lease, operation.desktop_epoch, operation.lease_epoch)
        _check_controller(lease, binding.session_id, operation.actor)
        if operation.state != "accepted" or connection.execute(
            """SELECT 1 FROM operations WHERE workspace_id=?
               AND (state='running' OR (state='outcome_unknown' AND reconciliation IS NULL)) LIMIT 1""",
            (binding.workspace_id,),
        ).fetchone():
            raise StateConflict("Operation is not dispatchable")
        now = datetime.now(timezone.utc).timestamp()
        connection.execute(
            "UPDATE operations SET state='running', updated_at=? WHERE workspace_id=? AND operation_id=?",
            (now, binding.workspace_id, operation_id),
        )
        return operation.model_copy(update={"state": "running", "updated_at": now})


def finish_operation(
    path: Path,
    binding: WorkspaceBinding,
    operation_id: str,
    state: Literal["succeeded", "failed", "cancelled", "outcome_unknown"],
    result: JsonValue = None,
) -> OperationRecord:
    """Record cleanup even after lease revocation; unknown cannot become success."""
    if state not in ("succeeded", "failed", "cancelled", "outcome_unknown"):
        raise ValueError("Terminal operation state required")
    encoded = _json(result)
    with _transaction(path) as connection:
        _lease(connection, binding)
        row = connection.execute(
            "SELECT * FROM operations WHERE workspace_id=? AND operation_id=? AND session_id=?",
            (binding.workspace_id, operation_id, binding.session_id),
        ).fetchone()
        if row is None:
            raise ScopeUnavailable("Operation unavailable")
        operation = _operation(row)
        if operation.state == state:
            if row["result"] != encoded:
                raise StateConflict("Conflicting terminal operation result")
            return operation
        if operation.state != "running":
            raise StateConflict("Operation is terminal or was never dispatched")
        now = datetime.now(timezone.utc).timestamp()
        connection.execute(
            "UPDATE operations SET state=?, result=?, updated_at=? WHERE workspace_id=? AND operation_id=?",
            (state, encoded, now, binding.workspace_id, operation_id),
        )
        return operation.model_copy(update={"state": state, "result": result, "updated_at": now})


def recover_workspace(
    path: Path, binding: WorkspaceBinding, desktop_epoch: int, lease_epoch: int
) -> ControllerLease:
    """After service loss/restore, fence input and preserve uncertain side effects.

    Requires the prior dispatcher to be stopped externally. This is not a
    substitute for terminating processes or revoking active VNC connections.
    """
    with _transaction(path) as connection:
        lease = _lease(connection, binding)
        _check_epochs(lease, desktop_epoch, lease_epoch)
        if lease.session_id is not None and lease.session_id != binding.session_id:
            raise StateConflict("Recovery must be initiated by the owning session")
        now = datetime.now(timezone.utc).timestamp()
        connection.execute(
            """UPDATE operations SET state=CASE WHEN state='running'
               THEN 'outcome_unknown' ELSE 'cancelled' END, updated_at=?
               WHERE workspace_id=? AND state IN ('accepted', 'running')""",
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


def reconcile_operation(
    path: Path,
    binding: WorkspaceBinding,
    operation_id: str,
    desktop_epoch: int,
    lease_epoch: int,
    *,
    observation: dict[str, JsonValue],
    cleanup_receipt: str,
    disposition: Literal["succeeded", "failed", "cancelled", "unresolved"],
) -> OperationRecord:
    """Trusted recovery callback after catalog verification and remote cleanup.

    Preserve the original unknown outcome. Explicit evidence-backed disposition
    permits new work, never a retry of the original operation. An unresolved
    outcome may be acknowledged only by the authorized owner/control service,
    not inferred by a model. Neither evidence nor receipts are public tool args.
    Even under an active lease, an unknown outcome requires a fenced transition
    before reconciliation and fresh observation before subsequent dispatch.
    """
    from desktop_observation import ObservationRef

    evidence = ObservationRef.model_validate(observation)
    if not cleanup_receipt.strip() or len(cleanup_receipt) > 256:
        raise ValueError("Bounded remote cleanup receipt required")
    if disposition not in ("succeeded", "failed", "cancelled", "unresolved"):
        raise ValueError("Invalid reconciliation disposition")
    with _transaction(path) as connection:
        lease = _lease(connection, binding)
        _check_epochs(lease, desktop_epoch, lease_epoch)
        if lease.state not in ("recovery_required", "transitioning"):
            raise StateConflict("Recovery or control transition required")
        if lease.session_id is not None and lease.session_id != binding.session_id:
            raise StateConflict("Control transition belongs to another session")
        row = connection.execute(
            "SELECT * FROM operations WHERE workspace_id=? AND operation_id=?",
            (binding.workspace_id, operation_id),
        ).fetchone()
        if row is None:
            raise ScopeUnavailable("Operation unavailable")
        operation = _operation(row)
        if operation.state != "outcome_unknown":
            raise StateConflict("Only unknown outcomes can be reconciled")
        if lease.state == "recovery_required" and operation.session_id != binding.session_id:
            raise ScopeUnavailable("Operation unavailable")
        if (
            evidence.desktop_epoch != desktop_epoch
            or evidence.captured_at.timestamp() < operation.updated_at
            or evidence.captured_at.timestamp() > datetime.now(timezone.utc).timestamp()
            or evidence.operation_id == operation_id
        ):
            raise StateConflict("Fresh recovery observation required")
        resolution = {
            "desktop_epoch": desktop_epoch, "lease_epoch": lease_epoch,
            "observation": evidence.model_dump(mode="json"),
            "cleanup_receipt": cleanup_receipt, "disposition": disposition,
        }
        encoded = _json(resolution)
        if operation.reconciliation is not None:
            if row["reconciliation"] != encoded:
                raise StateConflict("Conflicting reconciliation evidence")
            return operation
        connection.execute(
            "UPDATE operations SET reconciliation=? WHERE workspace_id=? AND operation_id=?",
            (encoded, binding.workspace_id, operation_id),
        )
        return operation.model_copy(update={"reconciliation": resolution})
