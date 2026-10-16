import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from pydantic import ValidationError

from config import WORKSPACE_MAX_PROJECTS, WORKSPACE_SESSION_TTL
from workspace_registry import (
    ProjectRecord,
    finish_project,
    get_project,
    initialize_registry,
    issue_session,
    list_projects,
    register_workspace,
    reserve_project,
    resolve_session,
    revoke_session,
)
from workspace_state import (
    ScopeUnavailable,
    StateConflict,
    accept_operation,
    begin_control_transition,
    complete_control_transition,
    finish_operation,
    get_lease,
    get_operation,
    initialize_state,
    start_operation,
)


@pytest.fixture
def registry(tmp_path):
    path = tmp_path / "control.db"
    initialize_state(path)
    initialize_registry(path)
    return path


@pytest.fixture
def session(registry):
    bootstrap = register_workspace(registry, "owner")
    issued = issue_session(registry, "owner", bootstrap.workspace_id)
    return registry, issued["binding"], issued["token"]


def activate(path, binding):
    lease = get_lease(path, binding)
    transition = begin_control_transition(path, binding, lease.desktop_epoch, lease.lease_epoch)
    return complete_control_transition(
        path, binding, transition.transition_id, "agent", revocation_receipt="test-native-revocation"
    )


def create_operation(path, binding, lease, operation_id, action_type="project.create", name="demo"):
    return accept_operation(
        path, binding, operation_id, lease.desktop_epoch, lease.lease_epoch,
        "agent", action_type, {"name": name},
    )


def test_initialize_registry_never_creates_a_database(tmp_path):
    path = tmp_path / "control.db"
    with pytest.raises(sqlite3.OperationalError):
        initialize_registry(path)
    assert not path.exists()
    initialize_state(path)
    initialize_registry(path)
    initialize_registry(path)
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        connection.execute("PRAGMA user_version=2")
    assert {"registry_workspaces", "registry_sessions", "projects"} <= tables
    with pytest.raises(StateConflict, match="schema"):
        initialize_registry(path)


def test_registry_functions_fail_closed_without_initialize_registry(tmp_path):
    path = tmp_path / "control.db"
    initialize_state(path)
    with pytest.raises(sqlite3.OperationalError):
        register_workspace(path, "owner")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM workspaces").fetchone()[0] == 0


def test_single_owner_registration_is_idempotent_and_exclusive(registry):
    first = register_workspace(registry, "owner")
    assert first.owner_id == "owner" and first.project_id is None
    assert register_workspace(registry, "owner") == first
    with pytest.raises(ScopeUnavailable):
        register_workspace(registry, "intruder")
    with pytest.raises(ValueError):
        register_workspace(registry, "")
    assert get_lease(registry, first).state == "recovery_required"
    with sqlite3.connect(registry) as connection:
        assert connection.execute("SELECT count(*) FROM workspaces").fetchone()[0] == 1


def test_concurrent_registration_creates_exactly_one_workspace(registry):
    barrier = Barrier(4)

    def register(owner):
        barrier.wait()
        try:
            return register_workspace(registry, owner)
        except ScopeUnavailable:
            return "unavailable"

    with ThreadPoolExecutor(max_workers=4) as executor:
        outcomes = list(executor.map(register, ["a", "a", "b", "b"]))
    bindings = [outcome for outcome in outcomes if outcome != "unavailable"]
    assert len(bindings) == 2 and bindings[0] == bindings[1]
    assert outcomes.count("unavailable") == 2
    with sqlite3.connect(registry) as connection:
        assert connection.execute("SELECT count(*) FROM workspaces").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM registry_workspaces").fetchone()[0] == 1


def test_issue_session_stores_only_the_token_digest(session):
    path, binding, token = session
    assert len(token) >= 43
    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT * FROM registry_sessions").fetchall()
        assert connection.execute(
            "SELECT 1 FROM sessions WHERE workspace_id=? AND session_id=?",
            (binding.workspace_id, binding.session_id),
        ).fetchone()
    assert len(rows) == 1
    assert token not in str(rows)
    assert hashlib.sha256(token.encode()).hexdigest() in str(rows)
    issued = issue_session(path, "owner", binding.workspace_id)
    assert issued["binding"].session_id != binding.session_id
    assert issued["token"] != token
    assert issued["expires_at"] > issued["binding"].model_dump().get("expires_at", 0)
    ttl = WORKSPACE_SESSION_TTL.total_seconds()
    with sqlite3.connect(path) as connection:
        issued_at, expires_at = connection.execute(
            "SELECT issued_at, expires_at FROM registry_sessions WHERE session_id=?",
            (issued["binding"].session_id,),
        ).fetchone()
    assert expires_at - issued_at == pytest.approx(ttl, abs=1)


def test_issue_session_requires_registered_owner_and_workspace(registry):
    bootstrap = register_workspace(registry, "owner")
    with pytest.raises(ScopeUnavailable):
        issue_session(registry, "other", bootstrap.workspace_id)
    with pytest.raises(ScopeUnavailable):
        issue_session(registry, "owner", "missing")


def test_resolve_session_validates_token_owner_and_workspace(session):
    path, binding, token = session
    assert resolve_session(path, "owner", binding.workspace_id, token) == binding
    for owner, workspace, presented in (
        ("other", binding.workspace_id, token),
        ("owner", "missing", token),
        ("owner", binding.workspace_id, token[:-1] + ("A" if token[-1] != "A" else "B")),
        ("owner", binding.workspace_id, ""),
        ("owner", binding.workspace_id, hashlib.sha256(token.encode()).hexdigest()),
    ):
        with pytest.raises(ScopeUnavailable):
            resolve_session(path, owner, workspace, presented)


def test_expired_session_is_rejected_and_reopen_issues_a_new_token(session):
    path, binding, token = session
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE registry_sessions SET expires_at=0 WHERE session_id=?", (binding.session_id,))
    with pytest.raises(ScopeUnavailable):
        resolve_session(path, "owner", binding.workspace_id, token)
    with pytest.raises(ScopeUnavailable):
        list_projects(path, binding)
    reopened = issue_session(path, "owner", binding.workspace_id)
    assert reopened["binding"].session_id != binding.session_id
    assert resolve_session(path, "owner", binding.workspace_id, reopened["token"]) == reopened["binding"]
    assert list_projects(path, reopened["binding"]) == []


def test_revoke_fences_control_and_preserves_uncertain_outcomes(session):
    path, binding, token = session
    lease = activate(path, binding)
    create_operation(path, binding, lease, "running-op")
    create_operation(path, binding, lease, "queued-op")
    start_operation(path, binding, "running-op")
    fenced = revoke_session(path, binding)
    assert fenced.controller == "none"
    assert fenced.state == "recovery_required"
    assert fenced.session_id is None and fenced.expires_at == 0
    assert fenced.lease_epoch > lease.lease_epoch
    assert fenced.desktop_epoch > lease.desktop_epoch
    assert get_operation(path, binding, "running-op").state == "outcome_unknown"
    assert get_operation(path, binding, "queued-op").state == "cancelled"
    with pytest.raises(ScopeUnavailable):
        resolve_session(path, "owner", binding.workspace_id, token)
    with pytest.raises(ScopeUnavailable):
        list_projects(path, binding)
    # Underlying binding survives for trusted cleanup and reconciliation.
    assert get_lease(path, binding) == fenced
    # No assertion that the remote stopped: unknown stays unknown until reconciled.
    with pytest.raises(StateConflict):
        finish_operation(path, binding, "running-op", "succeeded")
    assert get_operation(path, binding, "running-op").state == "outcome_unknown"
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT 1 FROM sessions WHERE session_id=?", (binding.session_id,)
        ).fetchone()
        assert connection.execute(
            "SELECT revoked_at FROM registry_sessions WHERE session_id=?", (binding.session_id,)
        ).fetchone()[0] is not None
    assert revoke_session(path, binding) == fenced
    with pytest.raises(StateConflict):
        accept_operation(path, binding, "late", lease.desktop_epoch, lease.lease_epoch, "agent", "click", {})


def test_revoking_a_non_controlling_session_does_not_fence_the_controller(session):
    path, binding, _ = session
    lease = activate(path, binding)
    create_operation(path, binding, lease, "op")
    other = issue_session(path, "owner", binding.workspace_id)["binding"]
    after = revoke_session(path, other)
    assert after == lease
    assert get_operation(path, binding, "op").state == "accepted"
    with pytest.raises(ScopeUnavailable):
        list_projects(path, other)


def test_revoke_requires_a_registry_session(session):
    path, binding, _ = session
    bootstrap = register_workspace(path, "owner")
    with pytest.raises(ScopeUnavailable):
        revoke_session(path, bootstrap)
    with pytest.raises(ScopeUnavailable):
        revoke_session(path, binding.model_copy(update={"owner_id": "other"}))
    with pytest.raises(ScopeUnavailable):
        revoke_session(path, binding.model_copy(update={"session_id": "unknown"}))


def test_project_record_is_strict_frozen_and_pathless():
    record = ProjectRecord(
        workspace_id="w", project_id="p", name="demo", state="pending", operation_id="op"
    )
    with pytest.raises(ValidationError):
        record.model_copy(update={"state": "done"}).model_validate(record.model_dump() | {"state": "done"})
    for bad in ({"schema_version": 2}, {"root_path": "/srv"}, {"state": "succeeded"}, {"project_id": 7}):
        with pytest.raises(ValidationError):
            ProjectRecord(**({**record.model_dump(), **bad}))
    with pytest.raises(ValidationError):
        record.name = "changed"  # type: ignore[misc]
    assert "path" not in record.model_dump()


def test_reserve_project_requires_bound_project_create_operation(session):
    path, binding, _ = session
    lease = activate(path, binding)
    with pytest.raises(ScopeUnavailable):
        reserve_project(path, binding, "demo", "missing")
    create_operation(path, binding, lease, "click-op", action_type="click")
    with pytest.raises(StateConflict, match="project creation"):
        reserve_project(path, binding, "demo", "click-op")
    create_operation(path, binding, lease, "finished-op")
    start_operation(path, binding, "finished-op")
    finish_operation(path, binding, "finished-op", "succeeded")
    with pytest.raises(StateConflict, match="not active"):
        reserve_project(path, binding, "demo", "finished-op")
    create_operation(path, binding, lease, "op")
    accepted = reserve_project(path, binding, "demo", "op")
    assert accepted.state == "pending" and accepted.workspace_id == binding.workspace_id
    assert accepted.operation_id == "op" and len(accepted.project_id) == 36
    create_operation(path, binding, lease, "running")
    start_operation(path, binding, "running")
    assert reserve_project(path, binding, "second", "running").state == "pending"
    for name in ("", "../escape", "a/b", "back\\slash", " padded", "x" * 129, "nul\x00"):
        with pytest.raises(ValueError):
            reserve_project(path, binding, name, "op")


def test_reserve_project_is_idempotent_by_operation_and_rejects_conflicts(session):
    path, binding, _ = session
    lease = activate(path, binding)
    create_operation(path, binding, lease, "op")
    first = reserve_project(path, binding, "demo", "op")
    assert reserve_project(path, binding, "demo", "op") == first
    with pytest.raises(StateConflict):
        reserve_project(path, binding, "renamed", "op")
    other = issue_session(path, "owner", binding.workspace_id)["binding"]
    with pytest.raises(StateConflict):
        reserve_project(path, other, "demo", "op")
    assert list_projects(path, binding) == [first]
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM projects").fetchone()[0] == 1


def test_concurrent_reservation_of_same_operation_creates_one_project(session):
    path, binding, _ = session
    lease = activate(path, binding)
    create_operation(path, binding, lease, "op")
    barrier = Barrier(3)

    def reserve(_):
        barrier.wait()
        return reserve_project(path, binding, "demo", "op")

    with ThreadPoolExecutor(max_workers=3) as executor:
        records = list(executor.map(reserve, range(3)))
    assert records[0] == records[1] == records[2]
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM projects").fetchone()[0] == 1


def test_project_quota_is_enforced(session, monkeypatch):
    import workspace_registry

    path, binding, _ = session
    lease = activate(path, binding)
    monkeypatch.setattr(workspace_registry, "WORKSPACE_MAX_PROJECTS", 2)
    for index in range(2):
        create_operation(path, binding, lease, f"op-{index}")
        reserve_project(path, binding, f"project-{index}", f"op-{index}")
    create_operation(path, binding, lease, "op-overflow")
    with pytest.raises(StateConflict, match="quota"):
        reserve_project(path, binding, "overflow", "op-overflow")
    # Existing reservations remain idempotent at the quota boundary.
    assert reserve_project(path, binding, "project-0", "op-0").name == "project-0"
    assert len(list_projects(path, binding)) == 2
    assert WORKSPACE_MAX_PROJECTS >= 1


def test_project_scope_follows_owner_workspace_and_session_state(session):
    path, binding, _ = session
    lease = activate(path, binding)
    create_operation(path, binding, lease, "op")
    project = reserve_project(path, binding, "demo", "op")
    peer = issue_session(path, "owner", binding.workspace_id)["binding"]
    assert get_project(path, peer, project.project_id) == project
    assert list_projects(path, peer) == [project]
    for forged in (
        binding.model_copy(update={"owner_id": "other"}),
        binding.model_copy(update={"workspace_id": "missing"}),
        binding.model_copy(update={"session_id": "unbound"}),
    ):
        with pytest.raises(ScopeUnavailable):
            list_projects(path, forged)
        with pytest.raises(ScopeUnavailable):
            get_project(path, forged, project.project_id)
        with pytest.raises(ScopeUnavailable):
            finish_project(path, forged, project.project_id, "ready")
    with pytest.raises(ScopeUnavailable):
        get_project(path, binding, "missing")
    revoke_session(path, peer)
    with pytest.raises(ScopeUnavailable):
        get_project(path, peer, project.project_id)


def test_finish_project_transitions_pending_once(session):
    path, binding, _ = session
    lease = activate(path, binding)
    for operation_id in ("ready-op", "unknown-op"):
        create_operation(path, binding, lease, operation_id)
    ready = reserve_project(path, binding, "ready", "ready-op")
    unknown = reserve_project(path, binding, "unknown", "unknown-op")
    finished = finish_project(path, binding, ready.project_id, "ready")
    assert finished.state == "ready"
    assert finish_project(path, binding, ready.project_id, "ready") == finished
    with pytest.raises(StateConflict, match="Conflicting"):
        finish_project(path, binding, ready.project_id, "outcome_unknown")
    assert finish_project(path, binding, unknown.project_id, "outcome_unknown").state == "outcome_unknown"
    with pytest.raises(StateConflict):
        finish_project(path, binding, unknown.project_id, "ready")
    with pytest.raises(ValueError):
        finish_project(path, binding, unknown.project_id, "pending")  # type: ignore[arg-type]
    with pytest.raises(ScopeUnavailable):
        finish_project(path, binding, "missing", "ready")
    assert [record.state for record in list_projects(path, binding)] == ["ready", "outcome_unknown"]
    assert get_project(path, binding, ready.project_id) == finished


def test_bootstrap_binding_can_manage_projects_without_a_token(registry):
    bootstrap = register_workspace(registry, "owner")
    lease = activate(registry, bootstrap)
    create_operation(registry, bootstrap, lease, "op")
    project = reserve_project(registry, bootstrap, "demo", "op")
    assert list_projects(registry, bootstrap) == [project]
    with sqlite3.connect(registry) as connection:
        assert connection.execute(
            "SELECT count(*) FROM registry_sessions WHERE session_id=?", (bootstrap.session_id,)
        ).fetchone()[0] == 0
