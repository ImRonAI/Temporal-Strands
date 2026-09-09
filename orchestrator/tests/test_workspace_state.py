import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from pydantic import ValidationError

from config import DESKTOP_RECORD_MAX_BYTES
from workspace_state import (
    ScopeUnavailable,
    StateConflict,
    WorkspaceBinding,
    accept_operation,
    begin_control_transition,
    bind_session,
    complete_control_transition,
    create_workspace,
    finish_operation,
    get_lease,
    get_operation,
    initialize_state,
    recover_workspace,
    reconcile_operation,
    renew_control_lease,
    start_operation,
)


@pytest.fixture
def workspace(tmp_path):
    path = tmp_path / "control.db"
    initialize_state(path)
    binding = create_workspace(path, "owner", "session")
    lease = get_lease(path, binding)
    assert lease.state == "recovery_required"
    transition = begin_control_transition(path, binding, lease.desktop_epoch, lease.lease_epoch)
    lease = complete_control_transition(
        path, binding, transition.transition_id, "agent", revocation_receipt="test-native-revocation"
    )
    return path, binding, lease


def accept(workspace, operation_id="op", request=None):
    path, binding, lease = workspace
    return accept_operation(
        path, binding, operation_id, lease.desktop_epoch, lease.lease_epoch,
        "agent", "click", request or {"x": 1, "y": 2},
    )


def test_binding_rejects_unknown_schema_and_model_fields():
    for extra in ({"schema_version": 2}, {"host_path": "/"}):
        with pytest.raises(ValidationError):
            WorkspaceBinding(owner_id="owner", workspace_id="workspace", session_id="session", **extra)


def test_database_bootstrap_is_explicit_and_private(tmp_path):
    path = tmp_path / "control.db"
    binding = WorkspaceBinding(owner_id="owner", workspace_id="workspace", session_id="session")
    with pytest.raises(sqlite3.OperationalError):
        get_lease(path, binding)
    assert not path.exists()
    initialize_state(path)
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        initialize_state(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        connection.execute("PRAGMA user_version=2")
    with pytest.raises(StateConflict, match="schema"):
        get_lease(path, binding)


def test_owner_and_session_are_authorized_on_every_operation(workspace):
    path, binding, lease = workspace
    accept(workspace)
    for forged in (
        binding.model_copy(update={"owner_id": "other"}),
        binding.model_copy(update={"session_id": "unbound"}),
        binding.model_copy(update={"workspace_id": "missing"}),
    ):
        with pytest.raises(ScopeUnavailable):
            get_lease(path, forged)
        with pytest.raises(ScopeUnavailable):
            get_operation(path, forged, "op")
        with pytest.raises(ScopeUnavailable):
            start_operation(path, forged, "op")
        with pytest.raises(ScopeUnavailable):
            finish_operation(path, forged, "op", "succeeded")
        with pytest.raises(ScopeUnavailable):
            recover_workspace(path, forged, lease.desktop_epoch, lease.lease_epoch)
        with pytest.raises(ScopeUnavailable):
            begin_control_transition(path, forged, lease.desktop_epoch, lease.lease_epoch)
        with pytest.raises(ScopeUnavailable):
            complete_control_transition(path, forged, "unknown", "human", revocation_receipt="receipt")
    second = bind_session(path, "owner", binding.workspace_id, "second-session")
    with pytest.raises(StateConflict):
        accept_operation(path, second, "second-op", lease.desktop_epoch, lease.lease_epoch, "agent", "click", {})
    with pytest.raises(ScopeUnavailable):
        get_operation(path, second, "op")
    with pytest.raises(ScopeUnavailable):
        bind_session(path, "other", binding.workspace_id, "session")


def test_duplicate_id_returns_persisted_result_but_never_redispatches(workspace):
    path, binding, _ = workspace
    accepted = accept(workspace)
    assert accept(workspace) == accepted
    start_operation(path, binding, "op")
    with pytest.raises(StateConflict):
        start_operation(path, binding, "op")
    final = finish_operation(path, binding, "op", "succeeded", {"artifact_id": "evidence"})
    assert accept(workspace) == final
    assert finish_operation(path, binding, "op", "succeeded", final.result) == final
    with pytest.raises(StateConflict):
        accept(workspace, request={"x": 9})
    with pytest.raises(StateConflict):
        finish_operation(path, binding, "op", "failed")
    with pytest.raises(StateConflict, match="Conflicting terminal"):
        finish_operation(path, binding, "op", "succeeded", {"artifact_id": "different"})
    with pytest.raises(StateConflict):
        start_operation(path, binding, "op")


def test_canonical_request_hash_and_no_raw_input_persistence(workspace):
    path, _, _ = workspace
    first = accept(workspace, request={"text": "sensitive-clipboard", "x": 1})
    assert accept(workspace, request={"x": 1, "text": "sensitive-clipboard"}) == first
    with sqlite3.connect(path) as connection:
        data = connection.execute("SELECT * FROM operations").fetchall()
    assert "sensitive-clipboard" not in str(data)
    with pytest.raises(ValueError, match="size limit"):
        accept(workspace, "large", {"text": "x" * DESKTOP_RECORD_MAX_BYTES})
    with pytest.raises(ValueError):
        accept(workspace, "nan", {"x": float("nan")})


def test_stale_expired_and_inactive_leases_reject_dispatch(workspace):
    path, binding, lease = workspace
    accept(workspace)
    for desktop_epoch, lease_epoch in (
        (lease.desktop_epoch - 1, lease.lease_epoch),
        (lease.desktop_epoch, lease.lease_epoch - 1),
    ):
        with pytest.raises(StateConflict):
            accept_operation(path, binding, "stale", desktop_epoch, lease_epoch, "agent", "click", {})
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE workspaces SET expires_at=0")
    with pytest.raises(StateConflict, match="expired"):
        accept(workspace, "expired")
    with pytest.raises(StateConflict, match="expired"):
        start_operation(path, binding, "op")


def test_lease_renewal_cannot_revive_expired_or_revoked_ownership(workspace):
    path, binding, lease = workspace
    renewed = renew_control_lease(path, binding, lease.desktop_epoch, lease.lease_epoch)
    assert renewed.expires_at >= lease.expires_at
    assert renewed.lease_epoch == lease.lease_epoch
    other = bind_session(path, binding.owner_id, binding.workspace_id, "other")
    with pytest.raises(StateConflict):
        renew_control_lease(path, other, lease.desktop_epoch, lease.lease_epoch)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE workspaces SET expires_at=0")
    with pytest.raises(StateConflict, match="expired"):
        renew_control_lease(path, binding, lease.desktop_epoch, lease.lease_epoch)
    transition = begin_control_transition(path, binding, lease.desktop_epoch, lease.lease_epoch)
    with pytest.raises(StateConflict):
        renew_control_lease(path, binding, transition.desktop_epoch, transition.lease_epoch)


def test_takeover_fences_admissions_and_waits_for_running_cleanup(workspace):
    path, binding, lease = workspace
    accept(workspace)
    accept(workspace, "queued")
    start_operation(path, binding, "op")
    transition = begin_control_transition(path, binding, lease.desktop_epoch, lease.lease_epoch)
    assert transition.controller == "none"
    assert transition.state == "transitioning"
    assert transition.lease_epoch > lease.lease_epoch
    assert get_operation(path, binding, "queued").state == "cancelled"
    with pytest.raises(StateConflict):
        start_operation(path, binding, "queued")
    with pytest.raises(StateConflict):
        accept(workspace, "late")
    with pytest.raises(StateConflict, match="cleanup"):
        complete_control_transition(path, binding, transition.transition_id, "human", revocation_receipt="receipt")
    finish_operation(path, binding, "op", "cancelled", {"cleanup": "complete"})
    human = complete_control_transition(
        path, binding, transition.transition_id, "human", revocation_receipt="receipt"
    )
    assert human.controller == "human"
    assert human.desktop_epoch > lease.desktop_epoch
    with pytest.raises(StateConflict):
        complete_control_transition(path, binding, transition.transition_id, "agent", revocation_receipt="receipt")


def test_transition_cannot_be_completed_by_another_session_or_without_receipt(workspace):
    path, binding, lease = workspace
    transition = begin_control_transition(path, binding, lease.desktop_epoch, lease.lease_epoch)
    other = bind_session(path, binding.owner_id, binding.workspace_id, "other")
    with pytest.raises(StateConflict):
        complete_control_transition(path, other, transition.transition_id, "human", revocation_receipt="receipt")
    with pytest.raises(ValueError):
        complete_control_transition(path, binding, transition.transition_id, "agent", revocation_receipt=" ")
    with pytest.raises(StateConflict):
        begin_control_transition(path, binding, transition.desktop_epoch, transition.lease_epoch)


def test_agent_resume_invalidates_human_epoch_and_old_observations(workspace):
    path, binding, lease = workspace
    transition = begin_control_transition(path, binding, lease.desktop_epoch, lease.lease_epoch)
    human = complete_control_transition(
        path, binding, transition.transition_id, "human", revocation_receipt="takeover-receipt"
    )
    transition = begin_control_transition(path, binding, human.desktop_epoch, human.lease_epoch)
    agent = complete_control_transition(
        path, binding, transition.transition_id, "agent", revocation_receipt="resume-revocation"
    )
    assert agent.desktop_epoch > human.desktop_epoch > lease.desktop_epoch
    assert agent.lease_epoch > human.lease_epoch > lease.lease_epoch
    with pytest.raises(StateConflict):
        accept_operation(
            path, binding, "old-human", human.desktop_epoch, human.lease_epoch,
            "human", "click", {},
        )
    with pytest.raises(StateConflict):
        accept(workspace, "old-frame")
    accepted = accept((path, binding, agent), "fresh-frame")
    assert accepted.state == "accepted"


def test_recovery_never_repeats_unknown_mutations(workspace):
    path, binding, lease = workspace
    accept(workspace)
    accept(workspace, "queued")
    start_operation(path, binding, "op")
    recovered = recover_workspace(path, binding, lease.desktop_epoch, lease.lease_epoch)
    assert recovered.state == "recovery_required"
    assert recovered.controller == "none"
    assert recovered.desktop_epoch > lease.desktop_epoch
    assert recovered.lease_epoch > lease.lease_epoch
    assert accept(workspace).state == "outcome_unknown"
    assert get_operation(path, binding, "queued").state == "cancelled"
    with pytest.raises(StateConflict):
        start_operation(path, binding, "op")
    with pytest.raises(StateConflict):
        finish_operation(path, binding, "op", "succeeded")
    transition = begin_control_transition(path, binding, recovered.desktop_epoch, recovered.lease_epoch)
    with pytest.raises(StateConflict, match="reconciliation"):
        complete_control_transition(path, binding, transition.transition_id, "agent", revocation_receipt="receipt")


def test_concurrent_dispatch_is_exclusive(workspace):
    path, binding, _ = workspace
    for operation_id in ("a", "b"):
        accept(workspace, operation_id)

    barrier = Barrier(2)

    def dispatch(operation_id):
        barrier.wait()
        try:
            return start_operation(path, binding, operation_id).state
        except StateConflict:
            return "blocked"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(dispatch, ["a", "b"]))
    assert sorted(outcomes) == ["blocked", "running"]


def test_concurrent_same_id_admission_creates_one_record(workspace):
    path, _, _ = workspace
    with ThreadPoolExecutor(max_workers=2) as executor:
        records = list(executor.map(lambda _: accept(workspace), range(2)))
    assert records[0] == records[1]
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM operations").fetchone()[0] == 1


def test_takeover_and_dispatch_race_never_grants_control_over_running_operation(workspace):
    path, binding, lease = workspace
    accept(workspace)
    barrier = Barrier(2)

    def dispatch():
        barrier.wait()
        try:
            return start_operation(path, binding, "op").state
        except StateConflict:
            return "blocked"

    def takeover():
        barrier.wait()
        return begin_control_transition(path, binding, lease.desktop_epoch, lease.lease_epoch)

    with ThreadPoolExecutor(max_workers=2) as executor:
        dispatch_result = executor.submit(dispatch)
        takeover_result = executor.submit(takeover)
        outcome = dispatch_result.result()
        transition = takeover_result.result()
    operation = get_operation(path, binding, "op")
    assert transition.controller == "none"
    if outcome == "running":
        assert operation.state == "running"
        with pytest.raises(StateConflict, match="cleanup"):
            complete_control_transition(path, binding, transition.transition_id, "human", revocation_receipt="receipt")
    else:
        assert operation.state == "cancelled"
        assert complete_control_transition(
            path, binding, transition.transition_id, "human", revocation_receipt="receipt"
        ).controller == "human"


def test_sqlite_backup_restores_records_and_requires_fencing(workspace, tmp_path):
    path, binding, lease = workspace
    accept(workspace)
    start_operation(path, binding, "op")
    restored = tmp_path / "restored.db"
    with sqlite3.connect(path) as source, sqlite3.connect(restored) as target:
        source.backup(target)
    state = recover_workspace(restored, binding, lease.desktop_epoch, lease.lease_epoch)
    assert state.desktop_epoch > lease.desktop_epoch
    assert get_operation(restored, binding, "op").state == "outcome_unknown"
    assert get_operation(path, binding, "op").state == "running"


def test_recovery_requires_epochs_and_current_session(workspace):
    path, binding, lease = workspace
    other = bind_session(path, binding.owner_id, binding.workspace_id, "other")
    with pytest.raises(StateConflict, match="owning session"):
        recover_workspace(path, other, lease.desktop_epoch, lease.lease_epoch)
    recovered = recover_workspace(path, binding, lease.desktop_epoch, lease.lease_epoch)
    with pytest.raises(StateConflict, match="Stale"):
        recover_workspace(path, binding, lease.desktop_epoch, lease.lease_epoch)
    assert get_lease(path, binding) == recovered


def test_explicit_reconciliation_restores_control_without_rewriting_unknown(workspace):
    path, binding, lease = workspace
    accept(workspace)
    start_operation(path, binding, "op")
    recovered = recover_workspace(path, binding, lease.desktop_epoch, lease.lease_epoch)
    evidence = {
        "artifact_id": "recovery-observation", "generation": "123", "sha256": "a" * 64,
        "mime_type": "image/png", "byte_size": 1024, "width": 800, "height": 600,
        "display_width": 800, "display_height": 600,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "desktop_epoch": recovered.desktop_epoch, "operation_id": "fresh-capture",
    }
    kwargs = {"observation": evidence, "cleanup_receipt": "verified-stop", "disposition": "unresolved"}
    with pytest.raises(StateConflict):
        reconcile_operation(path, binding, "op", lease.desktop_epoch, lease.lease_epoch, **kwargs)
    with pytest.raises(StateConflict, match="Fresh"):
        reconcile_operation(
            path, binding, "op", recovered.desktop_epoch, recovered.lease_epoch,
            **{**kwargs, "observation": {**evidence, "desktop_epoch": lease.desktop_epoch}},
        )
    with pytest.raises(StateConflict, match="Fresh"):
        reconcile_operation(
            path, binding, "op", recovered.desktop_epoch, recovered.lease_epoch,
            **{**kwargs, "observation": {**evidence, "captured_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}},
        )
    reconciled = reconcile_operation(path, binding, "op", recovered.desktop_epoch, recovered.lease_epoch, **kwargs)
    assert reconciled.state == "outcome_unknown"
    assert reconcile_operation(path, binding, "op", recovered.desktop_epoch, recovered.lease_epoch, **kwargs) == reconciled
    with pytest.raises(StateConflict, match="Conflicting reconciliation"):
        reconcile_operation(
            path, binding, "op", recovered.desktop_epoch, recovered.lease_epoch,
            **{**kwargs, "disposition": "succeeded"},
        )
    transition = begin_control_transition(path, binding, recovered.desktop_epoch, recovered.lease_epoch)
    active = complete_control_transition(path, binding, transition.transition_id, "agent", revocation_receipt="revoked")
    assert accept(workspace).state == "outcome_unknown"
    with pytest.raises(StateConflict):
        start_operation(path, binding, "op")
    accept((path, binding, active), "new-op")
    assert start_operation(path, binding, "new-op").state == "running"


@pytest.mark.parametrize("takeover_first", [True, False])
def test_unknown_during_human_takeover_can_be_reconciled_by_transition_owner(workspace, takeover_first):
    path, binding, lease = workspace
    accept(workspace)
    start_operation(path, binding, "op")
    human_binding = bind_session(path, binding.owner_id, binding.workspace_id, "human-session")
    if takeover_first:
        transition = begin_control_transition(path, human_binding, lease.desktop_epoch, lease.lease_epoch)
    finish_operation(path, binding, "op", "outcome_unknown")
    if not takeover_first:
        transition = begin_control_transition(path, human_binding, lease.desktop_epoch, lease.lease_epoch)
    observation = {
        "artifact_id": "observed", "generation": "456", "sha256": "b" * 64,
        "mime_type": "image/png", "byte_size": 100, "width": 800, "height": 600,
        "display_width": 800, "display_height": 600,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "desktop_epoch": transition.desktop_epoch, "operation_id": "new-observation",
    }
    operation = reconcile_operation(
        path, human_binding, "op", transition.desktop_epoch, transition.lease_epoch,
        observation=observation, cleanup_receipt="stopped", disposition="succeeded",
    )
    assert operation.state == "outcome_unknown"
    human = complete_control_transition(path, human_binding, transition.transition_id, "human", revocation_receipt="revoked")
    assert human.controller == "human"
    with pytest.raises(StateConflict):
        start_operation(path, binding, "op")


def test_human_handoff_to_distinct_agent_session_and_release(workspace):
    path, binding, lease = workspace
    human_binding = bind_session(path, binding.owner_id, binding.workspace_id, "human-session")
    transition = begin_control_transition(path, human_binding, lease.desktop_epoch, lease.lease_epoch)
    with pytest.raises(ScopeUnavailable):
        complete_control_transition(
            path, human_binding, transition.transition_id, "human", revocation_receipt="receipt",
            target_session_id="unregistered",
        )
    human = complete_control_transition(path, human_binding, transition.transition_id, "human", revocation_receipt="receipt")
    accept_operation(path, human_binding, "human-op", human.desktop_epoch, human.lease_epoch, "human", "click", {})
    assert start_operation(path, human_binding, "human-op").actor == "human"
    finish_operation(path, human_binding, "human-op", "succeeded")
    with pytest.raises(StateConflict):
        accept_operation(path, human_binding, "wrong-role", human.desktop_epoch, human.lease_epoch, "agent", "click", {})
    transition = begin_control_transition(path, human_binding, human.desktop_epoch, human.lease_epoch)
    agent = complete_control_transition(
        path, human_binding, transition.transition_id, "agent", revocation_receipt="revoked",
        target_session_id=binding.session_id,
    )
    assert agent.session_id == binding.session_id
    accept((path, binding, agent), "agent-op")
    transition = begin_control_transition(path, binding, agent.desktop_epoch, agent.lease_epoch)
    released = complete_control_transition(path, binding, transition.transition_id, "none", revocation_receipt="release")
    assert released.session_id is None and released.expires_at == 0


def test_wal_readers_do_not_acquire_writer_lock(workspace):
    path, binding, lease = workspace
    accept(workspace)
    with sqlite3.connect(path) as writer:
        writer.execute("BEGIN IMMEDIATE")
        assert get_lease(path, binding) == lease
        assert get_operation(path, binding, "op").state == "accepted"


def test_non_json_request_is_a_validation_error(workspace):
    with pytest.raises(ValueError, match="JSON values"):
        accept(workspace, request={"bytes": b"not-json"})


def test_accepted_operation_cannot_be_reported_as_success(workspace):
    path, binding, _ = workspace
    accept(workspace)
    with pytest.raises(StateConflict):
        finish_operation(path, binding, "op", "succeeded")


def test_desktop_activity_budgets_do_not_inherit_unbounded_defaults():
    from config import (
        DESKTOP_JOB_HEARTBEAT_INTERVAL,
        DESKTOP_JOB_HEARTBEAT_TIMEOUT,
        DESKTOP_MAX_MUTATIONS,
        DESKTOP_MUTATION_RETRY_POLICY,
        DESKTOP_MUTATION_TIMEOUT,
        DESKTOP_OBSERVATION_RETRY_POLICY,
        DESKTOP_OBSERVATION_TIMEOUT,
        DESKTOP_TASK_TIMEOUT,
    )

    assert DESKTOP_MAX_MUTATIONS == 100
    assert DESKTOP_MUTATION_RETRY_POLICY.maximum_attempts == 1
    assert DESKTOP_OBSERVATION_RETRY_POLICY.maximum_attempts == 3
    assert DESKTOP_MUTATION_TIMEOUT.total_seconds() == 30
    assert DESKTOP_OBSERVATION_TIMEOUT.total_seconds() == 30
    assert DESKTOP_TASK_TIMEOUT.total_seconds() == 1800
    assert DESKTOP_JOB_HEARTBEAT_INTERVAL < DESKTOP_JOB_HEARTBEAT_TIMEOUT < DESKTOP_TASK_TIMEOUT
