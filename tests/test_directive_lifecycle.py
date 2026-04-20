"""Unit tests for directive lifecycle persistence (Gap 1).

Tests schema migration, stamp methods, view rendering, and query methods
for the `directive_lifecycle` view and supporting columns/methods on
AuditStore. Uses a temp SQLite DB per test — no Redis, no QDrant.
"""
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Install qdrant/fastembed stubs before importing observability package
from tests.test_filter_parsing import _install_stubs
_install_stubs()

from observability.audit_store import AuditStore  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store():
    """Fresh AuditStore with a temp DB."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    s = AuditStore(db_path=tmp.name)
    try:
        yield s
    finally:
        s._conn.close()
        Path(tmp.name).unlink(missing_ok=True)


def _columns(store, table):
    rows = store._conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def _view_exists(store, name):
    row = store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='view' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _seed_directive(store, **overrides):
    """Insert a directive row directly (bypasses archive_directive)."""
    now = datetime.now(timezone.utc).isoformat()
    defaults = {
        "directive_id": "D1",
        "stream_id": "stream-1",
        "timestamp": now,
        "source": "director",
        "directive_type": "DIRECTIVE",
        "target_agent": "architect",
        "project": "rpi",
        "content": "Do the thing",
        "required_action": "Confirm",
        "compliance_due": "2099-12-31T00:00:00+00:00",
        "status": "PENDING",
        "confidence": 0.95,
        "supersedes": None,
        "triggered_by_finding": "F1",
        "supporting_metrics": "{}",
        "verification_criteria": "Behavior X",
        "payload": "{}",
        "archived_at": now,
        "audit_cycle_id": "cycle-001",
        "metadata": "{}",
    }
    # Tests may pass verification_window_sessions as a convenience even though
    # it's not a real column — silently drop it.
    overrides.pop("verification_window_sessions", None)
    defaults.update(overrides)
    cols = ",".join(defaults.keys())
    qs = ",".join(["?"] * len(defaults))
    store._conn.execute(
        f"INSERT OR REPLACE INTO directives ({cols}) VALUES ({qs})",
        tuple(defaults.values()),
    )
    store._conn.commit()


def _seed_compliance(store, **overrides):
    now = datetime.now(timezone.utc).isoformat()
    defaults = {
        "compliance_id": "C1",
        "stream_id": "stream-c1",
        "timestamp": now,
        "directive_id": "D1",
        "agent": "architect",
        "agent_version": None,
        "session_id": "sess-1",
        "project": "rpi",
        "action_taken": "Acknowledged and implemented",
        "conflict_reason": None,
        "is_verification": 0,
        "verification_passed": None,
        "sessions_examined": None,
        "verification_evidence": "",
        "payload": "{}",
        "archived_at": now,
        "audit_cycle_id": "cycle-002",
    }
    defaults.update(overrides)
    cols = ",".join(defaults.keys())
    qs = ",".join(["?"] * len(defaults))
    store._conn.execute(
        f"INSERT OR REPLACE INTO compliance ({cols}) VALUES ({qs})",
        tuple(defaults.values()),
    )
    store._conn.commit()


def _seed_archive_log(store, cycle_ids, stream="audit:findings"):
    """Seed archive_log with rows for the given cycle IDs."""
    now = datetime.now(timezone.utc).isoformat()
    for cid in cycle_ids:
        store._conn.execute(
            "INSERT INTO archive_log (stream, messages_archived, archived_at, audit_cycle_id)"
            " VALUES (?, ?, ?, ?)",
            (stream, 0, now, cid),
        )
    store._conn.commit()


def _seed_verification_finding(store, **overrides):
    now = datetime.now(timezone.utc).isoformat()
    defaults = {
        "finding_id": "F-VER-1",
        "stream_id": "stream-f1",
        "audit_cycle_id": "cycle-003",
        "timestamp": now,
        "source": "auditor:policy",
        "auditor_type": "policy",
        "finding_type": "verification",
        "severity": "info",
        "confidence": 0.9,
        "target_agent": "architect",
        "target_session": "sess-2",
        "project": "rpi",
        "claim": "Behavior change confirmed",
        "evidence": "Observed compliance in sessions X, Y",
        "recommendation": "",
        "target_event_ids": "[]",
        "qdrant_refs": "{}",
        "payload": "{}",
        "archived_at": now,
        "target_directive_id": "D1",
        "verification_result": "compliant",
        "verification_evidence": "Observed compliance in sessions X, Y",
        "sessions_examined": 3,
    }
    defaults.update(overrides)
    cols = ",".join(defaults.keys())
    qs = ",".join(["?"] * len(defaults))
    store._conn.execute(
        f"INSERT OR REPLACE INTO findings ({cols}) VALUES ({qs})",
        tuple(defaults.values()),
    )
    store._conn.commit()


# ---------------------------------------------------------------------------
# Schema migration tests
# ---------------------------------------------------------------------------

class TestSchemaMigration:
    def test_directives_has_audit_cycle_id_column(self, store):
        assert "audit_cycle_id" in _columns(store, "directives")

    def test_directives_has_metadata_column(self, store):
        assert "metadata" in _columns(store, "directives")

    def test_compliance_has_audit_cycle_id_column(self, store):
        assert "audit_cycle_id" in _columns(store, "compliance")

    def test_findings_has_target_directive_id(self, store):
        assert "target_directive_id" in _columns(store, "findings")

    def test_findings_has_verification_result(self, store):
        assert "verification_result" in _columns(store, "findings")

    def test_findings_has_verification_evidence(self, store):
        assert "verification_evidence" in _columns(store, "findings")

    def test_findings_has_sessions_examined(self, store):
        assert "sessions_examined" in _columns(store, "findings")

    def test_directive_lifecycle_view_exists(self, store):
        assert _view_exists(store, "directive_lifecycle")

    def test_view_queryable_on_empty_db(self, store):
        rows = store._conn.execute("SELECT * FROM directive_lifecycle").fetchall()
        assert list(rows) == []


# ---------------------------------------------------------------------------
# View rendering tests
# ---------------------------------------------------------------------------

class TestViewRendering:
    def test_directive_alone_produces_pending_row(self, store):
        _seed_directive(store)
        rows = store._conn.execute(
            "SELECT to_status, trigger, source_ref FROM directive_lifecycle"
            " WHERE directive_id = 'D1' ORDER BY transition_timestamp"
        ).fetchall()
        to_statuses = [r["to_status"] for r in rows]
        assert "PENDING" in to_statuses
        pending = [r for r in rows if r["to_status"] == "PENDING"][0]
        assert pending["trigger"] == "directive_published"
        assert pending["source_ref"] == "D1"

    def test_compliance_without_conflict_produces_ack_and_vp(self, store):
        _seed_directive(store)
        _seed_compliance(store)
        rows = store._conn.execute(
            "SELECT to_status, trigger FROM directive_lifecycle"
            " WHERE directive_id = 'D1' ORDER BY transition_timestamp, to_status"
        ).fetchall()
        to_statuses = [r["to_status"] for r in rows]
        assert "ACKNOWLEDGED" in to_statuses
        assert "VERIFICATION_PENDING" in to_statuses
        triggers = {r["to_status"]: r["trigger"] for r in rows if r["to_status"] in ("ACKNOWLEDGED", "VERIFICATION_PENDING")}
        assert triggers["ACKNOWLEDGED"] == "compliance_ack"
        assert triggers["VERIFICATION_PENDING"] == "ack_auto_verification"

    def test_compliance_with_conflict_produces_escalated(self, store):
        _seed_directive(store)
        _seed_compliance(store, compliance_id="C2", conflict_reason="Violates core directive")
        rows = store._conn.execute(
            "SELECT to_status FROM directive_lifecycle WHERE directive_id='D1'"
        ).fetchall()
        to_statuses = {r["to_status"] for r in rows}
        assert "ESCALATED" in to_statuses
        # Conflict case should NOT produce ACKNOWLEDGED transitions
        assert "ACKNOWLEDGED" not in to_statuses

    def test_verification_finding_compliant_produces_verified_compliant(self, store):
        _seed_directive(store)
        _seed_verification_finding(store, verification_result="compliant")
        rows = store._conn.execute(
            "SELECT to_status, trigger FROM directive_lifecycle"
            " WHERE directive_id = 'D1' AND to_status LIKE 'VERIFIED%'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["to_status"] == "VERIFIED_COMPLIANT"
        assert rows[0]["trigger"] == "verification_finding"

    def test_verification_finding_non_compliant(self, store):
        _seed_directive(store)
        _seed_verification_finding(store, verification_result="non_compliant")
        rows = store._conn.execute(
            "SELECT to_status FROM directive_lifecycle WHERE directive_id='D1' AND to_status LIKE 'VERIFIED%'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["to_status"] == "VERIFIED_NON_COMPLIANT"

    def test_supersede_produces_superseded_row_on_old(self, store):
        _seed_directive(store, directive_id="D1")
        _seed_directive(store, directive_id="D2", supersedes="D1")
        rows = store._conn.execute(
            "SELECT directive_id, to_status, source_ref FROM directive_lifecycle"
            " WHERE to_status = 'SUPERSEDED'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["directive_id"] == "D1"
        assert rows[0]["source_ref"] == "D2"

    def test_non_verification_findings_do_not_render(self, store):
        _seed_directive(store)
        _seed_verification_finding(
            store,
            finding_id="F-NORMAL",
            target_directive_id=None,
            verification_result=None,
        )
        rows = store._conn.execute(
            "SELECT to_status FROM directive_lifecycle WHERE directive_id='D1' AND to_status LIKE 'VERIFIED%'"
        ).fetchall()
        assert rows == []


# ---------------------------------------------------------------------------
# Stamp method tests
# ---------------------------------------------------------------------------

class TestStampDismissal:
    def test_stamp_dismissal_writes_metadata_and_flips_status(self, store):
        _seed_directive(store, status="VERIFICATION_PENDING")
        store.stamp_dismissal(
            directive_id="D1",
            timestamp="2026-04-11T12:00:00+00:00",
            reason="User decision",
            cycle_id=None,
            previous_status="VERIFICATION_PENDING",
        )
        row = store._conn.execute(
            "SELECT status, metadata FROM directives WHERE directive_id='D1'"
        ).fetchone()
        assert row["status"] == "DISMISSED"
        meta = json.loads(row["metadata"])
        assert meta["dismissal"]["timestamp"] == "2026-04-11T12:00:00+00:00"
        assert meta["dismissal"]["reason"] == "User decision"
        assert meta["dismissal"]["previous_status"] == "VERIFICATION_PENDING"

    def test_stamp_dismissal_produces_dismissed_transition_with_correct_from_status(self, store):
        _seed_directive(store, status="ACKNOWLEDGED")
        store.stamp_dismissal(
            directive_id="D1",
            timestamp="2026-04-11T12:00:00+00:00",
            reason="User decision",
            cycle_id=None,
            previous_status="ACKNOWLEDGED",
        )
        rows = store._conn.execute(
            "SELECT from_status, to_status FROM directive_lifecycle"
            " WHERE directive_id='D1' AND to_status='DISMISSED'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["from_status"] == "ACKNOWLEDGED"
        assert rows[0]["to_status"] == "DISMISSED"


class TestStampDeadlineCheck:
    def test_stamp_deadline_check_writes_metadata_and_flips_status(self, store):
        _seed_directive(store, status="PENDING")
        store.stamp_deadline_check(
            directive_id="D1",
            timestamp="2026-04-11T12:00:00+00:00",
            cycle_id="cycle-999",
            previous_status="PENDING",
        )
        row = store._conn.execute(
            "SELECT status, metadata FROM directives WHERE directive_id='D1'"
        ).fetchone()
        assert row["status"] == "NON_COMPLIANT"
        meta = json.loads(row["metadata"])
        assert meta["deadline_check"]["first_seen_at"] == "2026-04-11T12:00:00+00:00"
        assert meta["deadline_check"]["cycle_id"] == "cycle-999"
        assert meta["deadline_check"]["previous_status"] == "PENDING"

    def test_stamp_deadline_check_produces_non_compliant_transition(self, store):
        _seed_directive(store)
        store.stamp_deadline_check(
            directive_id="D1",
            timestamp="2026-04-11T12:00:00+00:00",
            cycle_id="cycle-999",
            previous_status="PENDING",
        )
        rows = store._conn.execute(
            "SELECT from_status, to_status, audit_cycle_id FROM directive_lifecycle"
            " WHERE directive_id='D1' AND to_status='NON_COMPLIANT'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["from_status"] == "PENDING"
        assert rows[0]["audit_cycle_id"] == "cycle-999"


# ---------------------------------------------------------------------------
# Query method tests
# ---------------------------------------------------------------------------

class TestQueryDirectiveLifecycle:
    def test_returns_empty_for_unknown_directive(self, store):
        result = store.query_directive_lifecycle("nonexistent")
        assert result == []

    def test_returns_ordered_transitions_for_happy_path(self, store):
        _seed_directive(store)
        _seed_compliance(store)
        _seed_verification_finding(store)
        result = store.query_directive_lifecycle("D1")
        to_statuses = [r["to_status"] for r in result]
        # Should include PENDING first, then ACK/VP, then VERIFIED
        assert to_statuses[0] == "PENDING"
        assert "ACKNOWLEDGED" in to_statuses
        assert "VERIFICATION_PENDING" in to_statuses
        assert to_statuses[-1] == "VERIFIED_COMPLIANT"


class TestFindingPayloadVerificationFields:
    def test_finding_payload_accepts_verification_fields(self):
        from observability.messages import FindingPayload
        payload = FindingPayload(
            auditor_type="policy",
            finding_type="verification",
            target_agent="architect",
            target_directive_id="D1",
            verification_result="compliant",
            verification_evidence="observed in sessions X, Y",
            sessions_examined=5,
        )
        assert payload.target_directive_id == "D1"
        assert payload.verification_result == "compliant"
        assert payload.verification_evidence == "observed in sessions X, Y"
        assert payload.sessions_examined == 5

    def test_finding_payload_defaults_verification_fields_to_none_zero(self):
        from observability.messages import FindingPayload
        payload = FindingPayload(
            auditor_type="safety",
            finding_type="violation",
            target_agent="architect",
        )
        assert payload.target_directive_id is None
        assert payload.verification_result is None
        assert payload.verification_evidence == ""
        assert payload.sessions_examined == 0

    def test_finding_payload_rejects_invalid_verification_result(self):
        from observability.messages import FindingPayload
        with pytest.raises(Exception):
            FindingPayload(
                auditor_type="policy",
                finding_type="verification",
                target_agent="architect",
                verification_result="maybe",  # not in Literal
            )

    def test_audit_finding_accepts_verification_fields(self):
        from observability.schemas import AuditFinding, AuditorType, FindingType
        finding = AuditFinding(
            auditor_type=AuditorType.POLICY,
            finding_type=FindingType.INFO,
            target_directive_id="D1",
            verification_result="compliant",
            verification_evidence="observed",
            sessions_examined=5,
        )
        assert finding.target_directive_id == "D1"
        assert finding.verification_result == "compliant"
        assert finding.verification_evidence == "observed"
        assert finding.sessions_examined == 5


class TestAuditCycleIdInjection:
    """Verifies stream_publish stamps audit_cycle_id on ALL message types,
    not just findings. Gap 1 Issue #2."""

    def test_inject_audit_cycle_id_from_env_var(self, monkeypatch):
        monkeypatch.setenv("AUDIT_CYCLE_ID", "cycle-test-123")
        import audit_tools
        payload = {"directive_id": "D1"}
        audit_tools._inject_audit_cycle_id(payload)
        assert payload["audit_cycle_id"] == "cycle-test-123"

    def test_inject_does_not_override_existing(self, monkeypatch):
        monkeypatch.setenv("AUDIT_CYCLE_ID", "cycle-env-999")
        import audit_tools
        payload = {"directive_id": "D1", "audit_cycle_id": "cycle-original"}
        audit_tools._inject_audit_cycle_id(payload)
        assert payload["audit_cycle_id"] == "cycle-original"

    def test_inject_no_op_when_env_var_missing(self, monkeypatch):
        monkeypatch.delenv("AUDIT_CYCLE_ID", raising=False)
        import audit_tools
        payload = {"directive_id": "D1"}
        audit_tools._inject_audit_cycle_id(payload)
        assert "audit_cycle_id" not in payload

    def test_inject_handles_non_dict_payload(self, monkeypatch):
        monkeypatch.setenv("AUDIT_CYCLE_ID", "cycle-x")
        import audit_tools
        payload = "not a dict"
        # Should not raise
        audit_tools._inject_audit_cycle_id(payload)


class TestProjectIdInjection:
    """HARDEN-001: stream_publish must auto-inject `project` from
    OBSERVABILITY_PROJECT so auditor prompts don't need to repeat it."""

    def test_inject_project_from_env_var(self, monkeypatch):
        monkeypatch.setenv("OBSERVABILITY_PROJECT", "rpi")
        import audit_tools
        payload = {"finding_id": "F1"}
        audit_tools._inject_project_id(payload)
        assert payload["project"] == "rpi"

    def test_inject_does_not_override_existing(self, monkeypatch):
        monkeypatch.setenv("OBSERVABILITY_PROJECT", "rpi")
        import audit_tools
        payload = {"finding_id": "F1", "project": "other"}
        audit_tools._inject_project_id(payload)
        assert payload["project"] == "other"

    def test_inject_no_op_when_env_var_missing(self, monkeypatch):
        monkeypatch.delenv("OBSERVABILITY_PROJECT", raising=False)
        import audit_tools
        payload = {"finding_id": "F1"}
        audit_tools._inject_project_id(payload)
        assert "project" not in payload

    def test_inject_no_op_when_existing_is_empty_string(self, monkeypatch):
        """Empty string is falsy — treat as unset and inject."""
        monkeypatch.setenv("OBSERVABILITY_PROJECT", "rpi")
        import audit_tools
        payload = {"finding_id": "F1", "project": ""}
        audit_tools._inject_project_id(payload)
        assert payload["project"] == "rpi"

    def test_inject_handles_non_dict_payload(self, monkeypatch):
        monkeypatch.setenv("OBSERVABILITY_PROJECT", "rpi")
        import audit_tools
        payload = "not a dict"
        audit_tools._inject_project_id(payload)  # must not raise


class TestArchiveFindingPersistsVerificationFields:
    def test_archive_finding_with_verification_payload(self, store):
        now = datetime.now(timezone.utc).isoformat()
        store.archive_finding(
            stream_id="stream-f1",
            timestamp=now,
            payload={
                "finding_id": "F-VER-100",
                "auditor_type": "policy",
                "finding_type": "verification",
                "target_agent": "architect",
                "target_session": "sess-x",
                "project": "rpi",
                "claim": "Verified compliance",
                "evidence": "Behavior changed",
                "target_directive_id": "D1",
                "verification_result": "compliant",
                "verification_evidence": "Observed in sessions A, B, C",
                "sessions_examined": 3,
            },
        )
        store.commit()
        row = store._conn.execute(
            "SELECT target_directive_id, verification_result, verification_evidence, sessions_examined"
            " FROM findings WHERE finding_id = 'F-VER-100'"
        ).fetchone()
        assert row["target_directive_id"] == "D1"
        assert row["verification_result"] == "compliant"
        assert row["verification_evidence"] == "Observed in sessions A, B, C"
        assert row["sessions_examined"] == 3

    def test_archive_finding_verification_finding_renders_in_view(self, store):
        _seed_directive(store, directive_id="D42")
        store.archive_finding(
            stream_id="stream-f2",
            timestamp=datetime.now(timezone.utc).isoformat(),
            payload={
                "finding_id": "F-VER-200",
                "auditor_type": "policy",
                "finding_type": "verification",
                "target_agent": "architect",
                "project": "rpi",
                "target_directive_id": "D42",
                "verification_result": "non_compliant",
                "verification_evidence": "Agent reverted",
                "sessions_examined": 4,
            },
        )
        store.commit()
        rows = store._conn.execute(
            "SELECT to_status FROM directive_lifecycle WHERE directive_id='D42' AND to_status LIKE 'VERIFIED%'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["to_status"] == "VERIFIED_NON_COMPLIANT"


class TestStampFollowup:
    def test_creates_followups_array_on_first_call(self, store):
        _seed_directive(store, directive_id="D-FU1")
        store.stamp_followup(
            directive_id="D-FU1",
            action="retry",
            cycle_id="cycle-1",
            ref_id="task-uuid-1",
            timestamp="2026-04-11T10:00:00+00:00",
        )
        row = store._conn.execute(
            "SELECT metadata FROM directives WHERE directive_id='D-FU1'"
        ).fetchone()
        meta = json.loads(row["metadata"])
        assert "followups" in meta
        assert len(meta["followups"]) == 1
        assert meta["followups"][0]["action"] == "retry"
        assert meta["followups"][0]["cycle_id"] == "cycle-1"
        assert meta["followups"][0]["task_id"] == "task-uuid-1"

    def test_appends_to_existing_followups(self, store):
        _seed_directive(store, directive_id="D-FU2")
        store.stamp_followup(directive_id="D-FU2", action="retry", cycle_id="cycle-1", ref_id="t1", timestamp="2026-04-11T10:00:00+00:00")
        store.stamp_followup(directive_id="D-FU2", action="retry", cycle_id="cycle-2", ref_id="t2", timestamp="2026-04-11T11:00:00+00:00")
        row = store._conn.execute("SELECT metadata FROM directives WHERE directive_id='D-FU2'").fetchone()
        meta = json.loads(row["metadata"])
        assert len(meta["followups"]) == 2
        assert meta["followups"][0]["task_id"] == "t1"
        assert meta["followups"][1]["task_id"] == "t2"

    def test_escalate_action_uses_escalation_id_key(self, store):
        _seed_directive(store, directive_id="D-FU3")
        store.stamp_followup(directive_id="D-FU3", action="escalate", cycle_id="cycle-5", ref_id="esc-uuid-1", timestamp="2026-04-11T12:00:00+00:00")
        row = store._conn.execute("SELECT metadata FROM directives WHERE directive_id='D-FU3'").fetchone()
        meta = json.loads(row["metadata"])
        entry = meta["followups"][0]
        assert entry["action"] == "escalate"
        assert entry["escalation_id"] == "esc-uuid-1"
        assert "task_id" not in entry

    def test_preserves_other_metadata_keys(self, store):
        _seed_directive(store, directive_id="D-FU4")
        # Manually seed a dismissal key
        store._conn.execute(
            "UPDATE directives SET metadata = ? WHERE directive_id = ?",
            (json.dumps({"dismissal": {"timestamp": "2026-04-01T00:00:00Z", "reason": "x", "cycle_id": None, "previous_status": "PENDING"}}), "D-FU4"),
        )
        store._conn.commit()
        store.stamp_followup(directive_id="D-FU4", action="retry", cycle_id="cycle-1", ref_id="t1", timestamp="2026-04-11T10:00:00+00:00")
        row = store._conn.execute("SELECT metadata FROM directives WHERE directive_id='D-FU4'").fetchone()
        meta = json.loads(row["metadata"])
        assert "dismissal" in meta
        assert "followups" in meta
        assert meta["dismissal"]["reason"] == "x"

    def test_does_not_flip_status(self, store):
        _seed_directive(store, directive_id="D-FU5", status="VERIFICATION_PENDING")
        store.stamp_followup(directive_id="D-FU5", action="retry", cycle_id="cycle-1", ref_id="t1", timestamp="2026-04-11T10:00:00+00:00")
        row = store._conn.execute("SELECT status FROM directives WHERE directive_id='D-FU5'").fetchone()
        assert row["status"] == "VERIFICATION_PENDING"


class TestStampVerificationEscalation:
    def test_flips_status_to_escalated(self, store):
        _seed_directive(store, directive_id="D-VE1", status="VERIFICATION_PENDING")
        store.stamp_verification_escalation(
            directive_id="D-VE1",
            timestamp="2026-04-11T14:00:00+00:00",
            cycle_id="cycle-3",
            escalation_id="esc-x",
            previous_status="VERIFICATION_PENDING",
        )
        row = store._conn.execute("SELECT status FROM directives WHERE directive_id='D-VE1'").fetchone()
        assert row["status"] == "ESCALATED"

    def test_writes_metadata_key(self, store):
        _seed_directive(store, directive_id="D-VE2", status="VERIFICATION_PENDING")
        store.stamp_verification_escalation(
            directive_id="D-VE2",
            timestamp="2026-04-11T14:00:00+00:00",
            cycle_id="cycle-3",
            escalation_id="esc-y",
            previous_status="VERIFICATION_PENDING",
        )
        row = store._conn.execute("SELECT metadata FROM directives WHERE directive_id='D-VE2'").fetchone()
        meta = json.loads(row["metadata"])
        assert "verification_escalation" in meta
        ve = meta["verification_escalation"]
        assert ve["timestamp"] == "2026-04-11T14:00:00+00:00"
        assert ve["cycle_id"] == "cycle-3"
        assert ve["escalation_id"] == "esc-y"
        assert ve["previous_status"] == "VERIFICATION_PENDING"

    def test_preserves_existing_followups(self, store):
        _seed_directive(store, directive_id="D-VE3", status="VERIFICATION_PENDING")
        store.stamp_followup(directive_id="D-VE3", action="retry", cycle_id="cycle-1", ref_id="t1", timestamp="2026-04-11T10:00:00+00:00")
        store.stamp_followup(directive_id="D-VE3", action="retry", cycle_id="cycle-2", ref_id="t2", timestamp="2026-04-11T11:00:00+00:00")
        store.stamp_verification_escalation(
            directive_id="D-VE3",
            timestamp="2026-04-11T12:00:00+00:00",
            cycle_id="cycle-3",
            escalation_id="esc-z",
            previous_status="VERIFICATION_PENDING",
        )
        row = store._conn.execute("SELECT metadata FROM directives WHERE directive_id='D-VE3'").fetchone()
        meta = json.loads(row["metadata"])
        assert len(meta["followups"]) == 2
        assert "verification_escalation" in meta


class TestViewVerificationEscalationBranch:
    def test_view_renders_escalated_from_stuck_verification(self, store):
        _seed_directive(store, directive_id="D-VSB1", status="VERIFICATION_PENDING")
        store.stamp_verification_escalation(
            directive_id="D-VSB1",
            timestamp="2026-04-11T14:00:00+00:00",
            cycle_id="cycle-STUCK",
            escalation_id="esc-STUCK",
            previous_status="VERIFICATION_PENDING",
        )
        rows = store._conn.execute(
            "SELECT from_status, to_status, trigger, source_ref, audit_cycle_id"
            " FROM directive_lifecycle"
            " WHERE directive_id='D-VSB1' AND trigger='verification_stuck'"
        ).fetchall()
        assert len(rows) == 1
        r = rows[0]
        assert r["from_status"] == "VERIFICATION_PENDING"
        assert r["to_status"] == "ESCALATED"
        assert r["source_ref"] == "esc-STUCK"
        assert r["audit_cycle_id"] == "cycle-STUCK"

    def test_view_does_not_render_without_metadata(self, store):
        _seed_directive(store, directive_id="D-VSB2", status="VERIFICATION_PENDING")
        rows = store._conn.execute(
            "SELECT to_status FROM directive_lifecycle"
            " WHERE directive_id='D-VSB2' AND trigger='verification_stuck'"
        ).fetchall()
        assert rows == []


class TestDismissDirective:
    """Atomic dismissal: read current status + stamp metadata + flip status."""

    def test_returns_none_for_unknown_directive(self, store):
        result = store.dismiss_directive(directive_id="nonexistent", reason="x")
        assert result is None

    def test_flips_status_and_returns_result_dict(self, store):
        _seed_directive(store, directive_id="D-D1", status="VERIFICATION_PENDING")
        result = store.dismiss_directive(
            directive_id="D-D1",
            reason="Irrelevant now",
            cycle_id=None,
        )
        assert result is not None
        assert result["directive_id"] == "D-D1"
        assert result["previous_status"] == "VERIFICATION_PENDING"
        assert "dismissed_at" in result

        row = store._conn.execute(
            "SELECT status, metadata FROM directives WHERE directive_id='D-D1'"
        ).fetchone()
        assert row["status"] == "DISMISSED"
        meta = json.loads(row["metadata"])
        assert meta["dismissal"]["reason"] == "Irrelevant now"
        assert meta["dismissal"]["previous_status"] == "VERIFICATION_PENDING"

    def test_renders_correct_from_status_in_view(self, store):
        _seed_directive(store, directive_id="D-D2", status="ACKNOWLEDGED")
        store.dismiss_directive(directive_id="D-D2", reason="user choice")
        rows = store._conn.execute(
            "SELECT from_status, to_status FROM directive_lifecycle"
            " WHERE directive_id='D-D2' AND to_status='DISMISSED'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["from_status"] == "ACKNOWLEDGED"


class TestArchiverDeadlineCheck:
    """Verifies archiver._check_deadlines handles the edge cases from
    Gap 1 Issue #6 (empty-string compliance_due false-positive)."""

    def _make_archiver(self, store):
        from unittest.mock import MagicMock
        from observability.archiver import StreamArchiver
        return StreamArchiver(store=store, client=MagicMock())

    def test_flips_pending_to_non_compliant_when_past_due(self, store):
        past = "2020-01-01T00:00:00+00:00"
        _seed_directive(store, directive_id="D-PAST", compliance_due=past, status="PENDING")
        archiver = self._make_archiver(store)
        count = archiver._check_deadlines(audit_cycle_id="cycle-now")
        assert count == 1
        row = store._conn.execute(
            "SELECT status, metadata FROM directives WHERE directive_id='D-PAST'"
        ).fetchone()
        assert row["status"] == "NON_COMPLIANT"
        meta = json.loads(row["metadata"])
        assert meta["deadline_check"]["cycle_id"] == "cycle-now"
        assert meta["deadline_check"]["previous_status"] == "PENDING"

    def test_skips_directive_with_empty_compliance_due(self, store):
        """Gap 1 Issue #6: empty string must NOT lex-compare as past."""
        _seed_directive(store, directive_id="D-EMPTY", compliance_due="", status="PENDING")
        archiver = self._make_archiver(store)
        count = archiver._check_deadlines(audit_cycle_id="cycle-now")
        assert count == 0
        row = store._conn.execute(
            "SELECT status FROM directives WHERE directive_id='D-EMPTY'"
        ).fetchone()
        assert row["status"] == "PENDING"

    def test_skips_directive_with_null_compliance_due(self, store):
        _seed_directive(store, directive_id="D-NULL", compliance_due=None, status="PENDING")
        archiver = self._make_archiver(store)
        count = archiver._check_deadlines(audit_cycle_id="cycle-now")
        assert count == 0

    def test_skips_directive_with_future_compliance_due(self, store):
        _seed_directive(store, directive_id="D-FUTURE", compliance_due="2099-12-31T00:00:00+00:00", status="PENDING")
        archiver = self._make_archiver(store)
        count = archiver._check_deadlines(audit_cycle_id="cycle-now")
        assert count == 0

    def test_skips_non_pending_directive(self, store):
        past = "2020-01-01T00:00:00+00:00"
        _seed_directive(store, directive_id="D-ACK", compliance_due=past, status="ACKNOWLEDGED")
        archiver = self._make_archiver(store)
        count = archiver._check_deadlines(audit_cycle_id="cycle-now")
        assert count == 0

    def test_idempotent_when_already_stamped(self, store):
        past = "2020-01-01T00:00:00+00:00"
        _seed_directive(store, directive_id="D-STAMPED", compliance_due=past, status="PENDING")
        archiver = self._make_archiver(store)
        archiver._check_deadlines(audit_cycle_id="cycle-1")
        # Second run should be a no-op (status is now NON_COMPLIANT, and metadata set)
        count = archiver._check_deadlines(audit_cycle_id="cycle-2")
        assert count == 0
        row = store._conn.execute(
            "SELECT metadata FROM directives WHERE directive_id='D-STAMPED'"
        ).fetchone()
        meta = json.loads(row["metadata"])
        assert meta["deadline_check"]["cycle_id"] == "cycle-1"  # not cycle-2


class TestQueryStaleVerifications:
    """Gap 2: query_stale_verifications returns VP directives needing retry/escalation."""

    def test_returns_empty_when_no_vp_directives(self, store):
        _seed_archive_log(store, ["cycle-1", "cycle-2", "cycle-3"])
        result = store.query_stale_verifications(audit_cycle_id="cycle-3")
        assert result == []

    def test_excludes_already_escalated_directive(self, store):
        _seed_directive(store, directive_id="D-SV1", status="VERIFICATION_PENDING", audit_cycle_id="cycle-1")
        _seed_compliance(store, compliance_id="C-SV1", directive_id="D-SV1", audit_cycle_id="cycle-2")
        store.stamp_verification_escalation(
            directive_id="D-SV1",
            timestamp="2026-04-11T14:00:00+00:00",
            cycle_id="cycle-3",
            escalation_id="esc-1",
            previous_status="VERIFICATION_PENDING",
        )
        _seed_archive_log(store, ["cycle-1", "cycle-2", "cycle-3", "cycle-4"])
        result = store.query_stale_verifications(audit_cycle_id="cycle-4")
        directive_ids = [r["directive_id"] for r in result]
        assert "D-SV1" not in directive_ids

    def test_returns_stale_vp_directive(self, store):
        _seed_directive(store, directive_id="D-SV2", status="VERIFICATION_PENDING", audit_cycle_id="cycle-1")
        _seed_compliance(store, compliance_id="C-SV2", directive_id="D-SV2", audit_cycle_id="cycle-2")
        _seed_archive_log(store, ["cycle-1", "cycle-2", "cycle-3"])
        result = store.query_stale_verifications(audit_cycle_id="cycle-3")
        assert len(result) == 1
        assert result[0]["directive_id"] == "D-SV2"
        assert result[0]["cycles_elapsed"] >= 1
        assert result[0]["retry_count"] == 0

    def test_excludes_vp_directive_from_current_cycle(self, store):
        """Directive entered VP in the current cycle — cycles_elapsed = 0."""
        _seed_directive(store, directive_id="D-SV3", status="VERIFICATION_PENDING", audit_cycle_id="cycle-1")
        _seed_compliance(store, compliance_id="C-SV3", directive_id="D-SV3", audit_cycle_id="cycle-3")
        _seed_archive_log(store, ["cycle-1", "cycle-2", "cycle-3"])
        result = store.query_stale_verifications(audit_cycle_id="cycle-3")
        directive_ids = [r["directive_id"] for r in result]
        assert "D-SV3" not in directive_ids

    def test_respects_last_followup_cycle(self, store):
        """A retry in cycle-2 should reset the 'last action'; cycle-3 sees elapsed=1."""
        _seed_directive(store, directive_id="D-SV4", status="VERIFICATION_PENDING", audit_cycle_id="cycle-1")
        _seed_compliance(store, compliance_id="C-SV4", directive_id="D-SV4", audit_cycle_id="cycle-1")
        store.stamp_followup(
            directive_id="D-SV4",
            action="retry",
            cycle_id="cycle-2",
            ref_id="task-1",
            timestamp="2026-04-11T11:00:00+00:00",
        )
        _seed_archive_log(store, ["cycle-1", "cycle-2", "cycle-3"])
        result = store.query_stale_verifications(audit_cycle_id="cycle-3")
        assert len(result) == 1
        assert result[0]["directive_id"] == "D-SV4"
        assert result[0]["last_action_cycle_id"] == "cycle-2"

    def test_excludes_when_followup_is_current_cycle(self, store):
        """If last followup is in the current cycle, not stale yet."""
        _seed_directive(store, directive_id="D-SV5", status="VERIFICATION_PENDING", audit_cycle_id="cycle-1")
        _seed_compliance(store, compliance_id="C-SV5", directive_id="D-SV5", audit_cycle_id="cycle-1")
        store.stamp_followup(
            directive_id="D-SV5",
            action="retry",
            cycle_id="cycle-3",
            ref_id="task-1",
            timestamp="2026-04-11T11:00:00+00:00",
        )
        _seed_archive_log(store, ["cycle-1", "cycle-2", "cycle-3"])
        result = store.query_stale_verifications(audit_cycle_id="cycle-3")
        directive_ids = [r["directive_id"] for r in result]
        assert "D-SV5" not in directive_ids

    def test_retry_count_reflected(self, store):
        _seed_directive(store, directive_id="D-SV6", status="VERIFICATION_PENDING", audit_cycle_id="cycle-1")
        _seed_compliance(store, compliance_id="C-SV6", directive_id="D-SV6", audit_cycle_id="cycle-1")
        store.stamp_followup(directive_id="D-SV6", action="retry", cycle_id="cycle-2", ref_id="t1", timestamp="2026-04-11T11:00:00+00:00")
        store.stamp_followup(directive_id="D-SV6", action="retry", cycle_id="cycle-3", ref_id="t2", timestamp="2026-04-11T12:00:00+00:00")
        _seed_archive_log(store, ["cycle-1", "cycle-2", "cycle-3", "cycle-4"])
        result = store.query_stale_verifications(audit_cycle_id="cycle-4")
        assert len(result) == 1
        assert result[0]["retry_count"] == 2
        assert result[0]["last_action_cycle_id"] == "cycle-3"

    def test_enriched_result_includes_directive_fields(self, store):
        _seed_directive(
            store,
            directive_id="D-SV7",
            status="VERIFICATION_PENDING",
            audit_cycle_id="cycle-1",
            target_agent="architect",
            project="rpi",
            verification_criteria="Check X",
        )
        _seed_compliance(store, compliance_id="C-SV7", directive_id="D-SV7", audit_cycle_id="cycle-1")
        _seed_archive_log(store, ["cycle-1", "cycle-2"])
        result = store.query_stale_verifications(audit_cycle_id="cycle-2")
        assert len(result) == 1
        r = result[0]
        assert r["target_agent"] == "architect"
        assert r["project"] == "rpi"
        assert r["verification_criteria"] == "Check X"
        assert "verification_window_sessions" in r
        assert "followups" in r


class TestArchiverStaleVerificationCheck:
    """Gap 2: _check_stale_verifications state machine — retry, escalate, skip."""

    def _make_archiver(self, store):
        from unittest.mock import MagicMock
        from observability.archiver import StreamArchiver
        mock_client = MagicMock()
        archiver = StreamArchiver(store=store, client=mock_client)
        return archiver, mock_client

    def _seed_stale_vp_directive(self, store, directive_id="D-STALE"):
        """Seed a VP directive with acknowledged compliance in cycle-1,
        archive_log for cycles 1-4, ready for a current-cycle check."""
        _seed_directive(
            store,
            directive_id=directive_id,
            status="VERIFICATION_PENDING",
            audit_cycle_id="cycle-1",
            target_agent="architect",
            project="rpi",
            verification_criteria="Read before edit",
        )
        _seed_compliance(
            store,
            compliance_id=f"C-{directive_id}",
            directive_id=directive_id,
            audit_cycle_id="cycle-1",
        )
        _seed_archive_log(store, ["cycle-1", "cycle-2", "cycle-3", "cycle-4"])

    def test_first_cycle_of_stale_publishes_retry_task(self, store):
        self._seed_stale_vp_directive(store, "D-S1")
        archiver, mock = self._make_archiver(store)
        result = archiver._check_stale_verifications("cycle-2")
        assert result["retries"] == 1
        assert result["escalations"] == 0
        # xadd should have been called at least once with audit:tasks
        audit_tasks_calls = [
            c for c in mock._redis.xadd.call_args_list if c[0][0] == "audit:tasks"
        ]
        assert len(audit_tasks_calls) == 1
        # metadata followups should have 1 entry
        row = store._conn.execute(
            "SELECT metadata FROM directives WHERE directive_id='D-S1'"
        ).fetchone()
        meta = json.loads(row["metadata"])
        assert len(meta["followups"]) == 1
        assert meta["followups"][0]["action"] == "retry"

    def test_second_cycle_publishes_second_retry(self, store):
        self._seed_stale_vp_directive(store, "D-S2")
        # Pre-seed one existing retry
        store.stamp_followup(
            directive_id="D-S2",
            action="retry",
            cycle_id="cycle-2",
            ref_id="task-existing",
            timestamp="2026-04-11T11:00:00+00:00",
        )
        archiver, mock = self._make_archiver(store)
        result = archiver._check_stale_verifications("cycle-3")
        assert result["retries"] == 1
        assert result["escalations"] == 0
        row = store._conn.execute(
            "SELECT metadata FROM directives WHERE directive_id='D-S2'"
        ).fetchone()
        meta = json.loads(row["metadata"])
        assert len(meta["followups"]) == 2

    def test_third_cycle_publishes_escalation(self, store):
        self._seed_stale_vp_directive(store, "D-S3")
        store.stamp_followup(directive_id="D-S3", action="retry", cycle_id="cycle-2", ref_id="t1", timestamp="2026-04-11T11:00:00+00:00")
        store.stamp_followup(directive_id="D-S3", action="retry", cycle_id="cycle-3", ref_id="t2", timestamp="2026-04-11T12:00:00+00:00")
        archiver, mock = self._make_archiver(store)
        result = archiver._check_stale_verifications("cycle-4")
        assert result["retries"] == 0
        assert result["escalations"] == 1
        # xadd should have been called with audit:escalations
        escalation_calls = [c for c in mock._redis.xadd.call_args_list if c[0][0] == "audit:escalations"]
        assert len(escalation_calls) == 1
        # Status flipped to ESCALATED
        row = store._conn.execute(
            "SELECT status, metadata FROM directives WHERE directive_id='D-S3'"
        ).fetchone()
        assert row["status"] == "ESCALATED"
        meta = json.loads(row["metadata"])
        assert "verification_escalation" in meta
        assert len(meta["followups"]) == 3  # 2 retries + 1 escalate

    def test_already_escalated_is_skipped(self, store):
        self._seed_stale_vp_directive(store, "D-S4")
        store.stamp_verification_escalation(
            directive_id="D-S4",
            timestamp="2026-04-11T10:00:00+00:00",
            cycle_id="cycle-2",
            escalation_id="esc-prev",
            previous_status="VERIFICATION_PENDING",
        )
        archiver, mock = self._make_archiver(store)
        result = archiver._check_stale_verifications("cycle-4")
        assert result["retries"] == 0
        assert result["escalations"] == 0
        # No xadd calls
        assert mock._redis.xadd.call_args_list == []

    def test_non_stale_directive_is_skipped(self, store):
        _seed_directive(
            store,
            directive_id="D-S5",
            status="VERIFICATION_PENDING",
            audit_cycle_id="cycle-1",
            target_agent="architect",
            project="rpi",
        )
        _seed_compliance(store, compliance_id="C-S5", directive_id="D-S5", audit_cycle_id="cycle-3")  # same cycle as current
        _seed_archive_log(store, ["cycle-1", "cycle-2", "cycle-3"])
        archiver, mock = self._make_archiver(store)
        result = archiver._check_stale_verifications("cycle-3")
        assert result["retries"] == 0
        assert result["escalations"] == 0

    def test_retry_task_payload_includes_verification_criteria(self, store):
        self._seed_stale_vp_directive(store, "D-S6")
        archiver, mock = self._make_archiver(store)
        archiver._check_stale_verifications("cycle-2")
        xadd_call = [c for c in mock._redis.xadd.call_args_list if c[0][0] == "audit:tasks"][0]
        # xadd(stream, dict). The payload in the envelope dict has JSON-serialized payload.
        stream_dict = xadd_call[0][1]
        assert stream_dict["stream"] == "audit:tasks"
        assert stream_dict["source"] == "director"
        assert stream_dict["message_type"] == "task"
        payload = json.loads(stream_dict["payload"])
        # TaskPayload has `parameters` dict carrying verify details
        params = payload.get("parameters", {})
        assert params.get("directive_id") == "D-S6"
        assert params.get("verification_criteria") == "Read before edit"
        assert params.get("target_agent") == "architect"

    def test_retry_task_target_is_auditor_policy(self, store):
        self._seed_stale_vp_directive(store, "D-S7")
        archiver, mock = self._make_archiver(store)
        archiver._check_stale_verifications("cycle-2")
        xadd_call = [c for c in mock._redis.xadd.call_args_list if c[0][0] == "audit:tasks"][0]
        stream_dict = xadd_call[0][1]
        assert stream_dict["target"] == "auditor:policy"


class TestArchiverComplianceStreamNotTrimmed:
    """Gap 1b: archiver must NOT trim compliance:* streams so the Director's
    next assign-phase read can still pick up events that landed mid-cycle."""

    def test_compliance_stream_is_not_trimmed(self, store):
        from unittest.mock import MagicMock
        from observability.archiver import StreamArchiver
        from observability.messages import MessageEnvelope, MessageType

        env = MessageEnvelope(
            stream="compliance:rpi",
            source="project:rpi:architect",
            target="director",
            message_type=MessageType.STATUS,
            payload={"compliance_id": "C-NOTRIM", "directive_id": "D-NT", "agent": "architect"},
        )
        mock_client = MagicMock()
        mock_client._redis.xrange.return_value = [("1-0", env.to_stream_dict())]

        archiver = StreamArchiver(store=store, client=mock_client)
        archiver._archive_stream("compliance:rpi", "archive_compliance", "cycle-nt")

        # xtrim must NOT have been called for compliance:rpi
        xtrim_calls = [c for c in mock_client._redis.xtrim.call_args_list if c[0][0] == "compliance:rpi"]
        assert xtrim_calls == [], f"Expected no xtrim on compliance:rpi, got: {xtrim_calls}"

    def test_audit_findings_stream_is_still_trimmed(self, store):
        """Regression guard: non-compliance streams still get trimmed as before."""
        from unittest.mock import MagicMock
        from observability.archiver import StreamArchiver
        from observability.messages import MessageEnvelope, MessageType

        env = MessageEnvelope(
            stream="audit:findings",
            source="auditor:safety",
            target="director",
            message_type=MessageType.FINDING,
            payload={
                "finding_id": "F-TRIM",
                "auditor_type": "safety",
                "finding_type": "violation",
                "target_agent": "architect",
                "project": "rpi",
            },
        )
        mock_client = MagicMock()
        mock_client._redis.xrange.return_value = [("1-0", env.to_stream_dict())]

        archiver = StreamArchiver(store=store, client=mock_client)
        archiver._archive_stream("audit:findings", "archive_finding", "cycle-t")

        xtrim_calls = [c for c in mock_client._redis.xtrim.call_args_list if c[0][0] == "audit:findings"]
        assert len(xtrim_calls) == 1, f"Expected xtrim on audit:findings, got: {mock_client._redis.xtrim.call_args_list}"


class TestArchiverComplianceProjectInjection:
    """Verifies the archiver injects project from compliance stream name
    into the payload before calling archive_compliance. Gap 1 Issue #3."""

    def test_archiver_injects_project_for_compliance_stream(self, store):
        from unittest.mock import MagicMock
        from observability.archiver import StreamArchiver
        from observability.messages import MessageEnvelope, MessageType

        # Build a fake envelope that xrange would return
        env = MessageEnvelope(
            stream="compliance:rpi",
            source="project:rpi:architect",
            target="director",
            message_type=MessageType.STATUS,
            payload={"compliance_id": "C-INJ", "directive_id": "D1", "agent": "architect", "action_taken": "done"},
        )
        mock_client = MagicMock()
        mock_client._redis.xrange.return_value = [("1-0", env.to_stream_dict())]
        mock_client._redis.xtrim.return_value = 0

        archiver = StreamArchiver(store=store, client=mock_client)
        archiver._archive_stream("compliance:rpi", "archive_compliance", "cycle-arch")

        # Query compliance table — project should have been injected as "rpi"
        row = store._conn.execute(
            "SELECT project, audit_cycle_id FROM compliance WHERE compliance_id='C-INJ'"
        ).fetchone()
        assert row is not None
        assert row["project"] == "rpi"
        assert row["audit_cycle_id"] == "cycle-arch"

    def test_archiver_preserves_publisher_audit_cycle_id(self, store):
        """When the payload already has an audit_cycle_id, the archiver must
        preserve it. Overriding with the current cycle collapses historical
        findings to the archival day and erases per-day reporting."""
        from unittest.mock import MagicMock
        from observability.archiver import StreamArchiver
        from observability.messages import MessageEnvelope, MessageType

        env = MessageEnvelope(
            stream="audit:findings",
            source="auditor:policy",
            target="director",
            message_type=MessageType.FINDING,
            payload={
                "finding_id": "F-PRESERVE",
                "auditor_type": "policy",
                "finding_type": "verification",
                "target_agent": "architect",
                "project": "rpi",
                "audit_cycle_id": "cycle-original",  # publisher stamped this
            },
        )
        mock_client = MagicMock()
        mock_client._redis.xrange.return_value = [("1-0", env.to_stream_dict())]
        mock_client._redis.xtrim.return_value = 0

        archiver = StreamArchiver(store=store, client=mock_client)
        archiver._archive_stream("audit:findings", "archive_finding", "cycle-current")

        row = store._conn.execute(
            "SELECT audit_cycle_id FROM findings WHERE finding_id='F-PRESERVE'"
        ).fetchone()
        assert row["audit_cycle_id"] == "cycle-original"

    def test_archiver_fills_missing_audit_cycle_id(self, store):
        """When the payload has no audit_cycle_id, the archiver fills it in
        with the current cycle — covers publishers that don't stamp it."""
        from unittest.mock import MagicMock
        from observability.archiver import StreamArchiver
        from observability.messages import MessageEnvelope, MessageType

        env = MessageEnvelope(
            stream="audit:findings",
            source="auditor:policy",
            target="director",
            message_type=MessageType.FINDING,
            payload={
                "finding_id": "F-FILL",
                "auditor_type": "policy",
                "finding_type": "verification",
                "target_agent": "architect",
                "project": "rpi",
                # no audit_cycle_id — archiver should fill it in
            },
        )
        mock_client = MagicMock()
        mock_client._redis.xrange.return_value = [("1-0", env.to_stream_dict())]
        mock_client._redis.xtrim.return_value = 0

        archiver = StreamArchiver(store=store, client=mock_client)
        archiver._archive_stream("audit:findings", "archive_finding", "cycle-current")

        row = store._conn.execute(
            "SELECT audit_cycle_id FROM findings WHERE finding_id='F-FILL'"
        ).fetchone()
        assert row["audit_cycle_id"] == "cycle-current"


class TestQueryNonVerifiedCounts:
    def test_returns_zeroes_on_empty_db(self, store):
        counts = store.query_non_verified_counts(project="rpi")
        assert counts["non_compliant"] == 0
        assert counts["verified_non_compliant"] == 0
        assert counts["escalated"] == 0
        assert counts["dismissed"] == 0
        assert counts["superseded"] == 0

    def test_counts_dismissed_directives(self, store):
        _seed_directive(store, directive_id="D-DISM-1", project="rpi")
        store.dismiss_directive("D-DISM-1", reason="x")
        counts = store.query_non_verified_counts(project="rpi")
        assert counts["dismissed"] == 1

    def test_counts_escalated_from_conflict(self, store):
        _seed_directive(store, directive_id="D-ESC", project="rpi")
        _seed_compliance(store, compliance_id="C-ESC", directive_id="D-ESC", project="rpi", conflict_reason="conflict")
        counts = store.query_non_verified_counts(project="rpi")
        assert counts["escalated"] == 1

    def test_counts_superseded_directives(self, store):
        _seed_directive(store, directive_id="D-OLD", project="rpi")
        _seed_directive(store, directive_id="D-NEW", project="rpi", supersedes="D-OLD")
        counts = store.query_non_verified_counts(project="rpi")
        assert counts["superseded"] == 1

    def test_filters_by_project(self, store):
        _seed_directive(store, directive_id="D-RPI", project="rpi")
        _seed_directive(store, directive_id="D-OTHER", project="other")
        store.dismiss_directive("D-RPI", reason="x")
        store.dismiss_directive("D-OTHER", reason="x")
        counts_rpi = store.query_non_verified_counts(project="rpi")
        counts_other = store.query_non_verified_counts(project="other")
        assert counts_rpi["dismissed"] == 1
        assert counts_other["dismissed"] == 1


class TestQueryCyclesToVerification:
    def test_returns_empty_when_no_verified_directives(self, store):
        result = store.query_cycles_to_verification(project="rpi", last_n_cycles=20)
        assert result == []

    def test_returns_cycles_elapsed_for_verified_directive(self, store):
        # PENDING in cycle-001, VERIFIED in cycle-003
        _seed_directive(store, directive_id="D1", audit_cycle_id="cycle-001")
        _seed_verification_finding(store, target_directive_id="D1", audit_cycle_id="cycle-003")
        result = store.query_cycles_to_verification(project="rpi", last_n_cycles=20)
        # Should contain D1 with both cycle markers
        d1_rows = [r for r in result if r["directive_id"] == "D1"]
        assert len(d1_rows) == 1
        assert d1_rows[0]["published_cycle"] == "cycle-001"
        assert d1_rows[0]["verified_cycle"] == "cycle-003"

    def test_filters_by_project(self, store):
        _seed_directive(store, directive_id="D1", project="rpi", audit_cycle_id="cycle-001")
        _seed_directive(store, directive_id="D2", project="other", audit_cycle_id="cycle-001")
        _seed_verification_finding(store, finding_id="F1", target_directive_id="D1", project="rpi", audit_cycle_id="cycle-002")
        _seed_verification_finding(store, finding_id="F2", target_directive_id="D2", project="other", audit_cycle_id="cycle-002")
        result = store.query_cycles_to_verification(project="rpi", last_n_cycles=20)
        directive_ids = {r["directive_id"] for r in result}
        assert directive_ids == {"D1"}

# ---------------------------------------------------------------------------
# HARDEN-003: symmetric escalation-with-thread creation
# ---------------------------------------------------------------------------


class TestCreateEscalationWithThread:
    """HARDEN-003: both the MCP tool create_escalation and archiver-published
    VERIFICATION_STUCK escalations must leave matching SQLite shape — one
    escalations row AND one initial escalation_messages entry."""

    def test_writes_both_rows_atomically(self, store):
        esc_id = "ESC-harden003a"
        store.create_escalation_with_thread(
            escalation_id=esc_id,
            escalation_type="VERIFICATION_STUCK",
            severity="high",
            project="rpi",
            summary="Stuck verification on D42",
            subject_agent="architect",
            directive_id="D42",
            resolution_status="OPEN",
            initial_message_author="director",
        )

        esc_row = store._conn.execute(
            "SELECT * FROM escalations WHERE escalation_id = ?", (esc_id,),
        ).fetchone()
        assert esc_row is not None
        assert esc_row["escalation_type"] == "VERIFICATION_STUCK"
        assert esc_row["project"] == "rpi"

        messages = store.get_escalation_messages(esc_id)
        assert len(messages) == 1
        assert messages[0]["author"] == "director"
        assert messages[0]["content"] == "Stuck verification on D42"

    def test_defaults_when_optional_fields_omitted(self, store):
        esc_id = "ESC-harden003b"
        store.create_escalation_with_thread(
            escalation_id=esc_id,
            escalation_type="POLICY_VIOLATION",
            severity="medium",
            project="rpi",
            summary="Something happened",
        )

        esc_row = store._conn.execute(
            "SELECT * FROM escalations WHERE escalation_id = ?", (esc_id,),
        ).fetchone()
        assert esc_row["resolution_status"] == "AWAITING_USER"
        assert esc_row["subject_agent"] == ""

        messages = store.get_escalation_messages(esc_id)
        assert len(messages) == 1

    def test_archiver_verification_escalation_creates_thread(self, store):
        """The archiver's _publish_verification_escalation path must also
        seed the thread (was the exact HARDEN-003 asymmetry)."""
        from unittest.mock import MagicMock
        from observability.archiver import StreamArchiver

        archiver = StreamArchiver(store=store, client=MagicMock())
        directive = {
            "directive_id": "D-stuck",
            "target_agent": "architect",
            "project": "rpi",
            "followups": [{"action": "retry", "cycle_id": "cycle-2"}],
            "retry_count": 2,
            "cycles_elapsed": 4,
            "vp_cycle_id": "cycle-0",
        }
        escalation_id = archiver._publish_verification_escalation(
            directive, audit_cycle_id="cycle-4",
        )

        # Archiver wrote to SQLite AND seeded the thread.
        esc_row = store._conn.execute(
            "SELECT * FROM escalations WHERE escalation_id = ?", (escalation_id,),
        ).fetchone()
        assert esc_row is not None
        assert esc_row["escalation_type"] == "VERIFICATION_STUCK"

        messages = store.get_escalation_messages(escalation_id)
        assert len(messages) == 1
        assert messages[0]["author"] == "director"
        assert "stuck in VERIFICATION_PENDING" in messages[0]["content"]

# ---------------------------------------------------------------------------
# HARDEN-002: compliance stream age-trim
# ---------------------------------------------------------------------------


def _seed_archive_log_dated(store, cycle_id: str, archived_at: str,
                             stream: str = "audit:findings") -> None:
    """Seed a single archive_log row with an explicit timestamp."""
    store._conn.execute(
        "INSERT INTO archive_log (stream, messages_archived, archived_at, audit_cycle_id)"
        " VALUES (?, ?, ?, ?)",
        (stream, 0, archived_at, cycle_id),
    )
    store._conn.commit()


class TestComplianceAgeTrim:
    """HARDEN-002: _trim_old_compliance_events trims old entries bounded by
    the Director group's XPENDING min so unread messages aren't destroyed."""

    def _make_archiver(self, store, projects=("rpi",)):
        from unittest.mock import MagicMock
        from observability.archiver import StreamArchiver
        archiver = StreamArchiver(store=store, client=MagicMock())
        archiver._load_projects = lambda: list(projects)
        return archiver

    def test_noop_when_fewer_than_threshold_cycles(self, store):
        """With only a handful of cycles, cutoff is None and no trim happens."""
        from observability.archiver import COMPLIANCE_TRIM_AGE_CYCLES
        now = datetime.now(timezone.utc)
        for i in range(5):
            _seed_archive_log_dated(
                store, f"cycle-{i:03d}", (now - timedelta(hours=i)).isoformat(),
            )

        archiver = self._make_archiver(store)
        result = archiver._trim_old_compliance_events()

        assert result == {"trimmed": 0, "streams": 0, "bounded_by_pending": 0}
        archiver._client._redis.xtrim.assert_not_called()
        assert COMPLIANCE_TRIM_AGE_CYCLES > 5

    def test_trims_when_above_threshold(self, store):
        """With enough cycles, cutoff resolves to an old stream id and xtrim runs."""
        from observability.archiver import COMPLIANCE_TRIM_AGE_CYCLES
        base = datetime.now(timezone.utc)
        # Seed threshold + 10 cycles, each one hour apart, newest first.
        for i in range(COMPLIANCE_TRIM_AGE_CYCLES + 10):
            _seed_archive_log_dated(
                store, f"cycle-{i:04d}", (base - timedelta(hours=i)).isoformat(),
            )

        archiver = self._make_archiver(store)
        archiver._client._redis.xpending.return_value = {
            "pending": 0, "min": None, "max": None, "consumers": [],
        }
        # Simulate 7 events before trim, 4 after.
        archiver._client._redis.xlen.side_effect = [7, 4]

        result = archiver._trim_old_compliance_events()

        assert result["trimmed"] == 3
        assert result["streams"] == 1
        assert result["bounded_by_pending"] == 0
        archiver._client._redis.xtrim.assert_called_once()
        call = archiver._client._redis.xtrim.call_args
        assert call[0][0] == "compliance:rpi"
        assert "minid" in call[1]

    def test_trim_bounded_by_xpending_min(self, store):
        """If xpending.min is older than the age cutoff, trim uses xpending.min
        so unread messages stay put (preserves Gap 1b)."""
        from observability.archiver import COMPLIANCE_TRIM_AGE_CYCLES
        base = datetime.now(timezone.utc)
        for i in range(COMPLIANCE_TRIM_AGE_CYCLES + 10):
            _seed_archive_log_dated(
                store, f"cycle-{i:04d}", (base - timedelta(hours=i)).isoformat(),
            )

        archiver = self._make_archiver(store)
        # xpending.min points way before the age cutoff — trim must honor it.
        archiver._client._redis.xpending.return_value = {
            "pending": 3, "min": "1-0", "max": "999999999999-0", "consumers": [],
        }
        archiver._client._redis.xlen.side_effect = [5, 5]  # nothing trimmed

        result = archiver._trim_old_compliance_events()

        assert result["bounded_by_pending"] == 1
        call = archiver._client._redis.xtrim.call_args
        assert call[1]["minid"] == "1-0"

    def test_skips_empty_stream(self, store):
        """Streams with no entries don't call xtrim at all."""
        from observability.archiver import COMPLIANCE_TRIM_AGE_CYCLES
        base = datetime.now(timezone.utc)
        for i in range(COMPLIANCE_TRIM_AGE_CYCLES + 10):
            _seed_archive_log_dated(
                store, f"cycle-{i:04d}", (base - timedelta(hours=i)).isoformat(),
            )

        archiver = self._make_archiver(store)
        archiver._client._redis.xpending.return_value = {"pending": 0, "min": None, "max": None, "consumers": []}
        archiver._client._redis.xlen.return_value = 0

        result = archiver._trim_old_compliance_events()

        assert result["trimmed"] == 0
        archiver._client._redis.xtrim.assert_not_called()

    def test_xtrim_exception_does_not_abort_cycle(self, store):
        """A Redis hiccup on one project must not prevent others from trimming."""
        from observability.archiver import COMPLIANCE_TRIM_AGE_CYCLES
        base = datetime.now(timezone.utc)
        for i in range(COMPLIANCE_TRIM_AGE_CYCLES + 10):
            _seed_archive_log_dated(
                store, f"cycle-{i:04d}", (base - timedelta(hours=i)).isoformat(),
            )

        archiver = self._make_archiver(store, projects=("rpi", "other"))
        archiver._client._redis.xpending.return_value = {"pending": 0, "min": None, "max": None, "consumers": []}
        # rpi: xlen=5 pre-trim (trim raises, no post-call); other: xlen 8 → 5.
        archiver._client._redis.xlen.side_effect = [5, 8, 5]
        archiver._client._redis.xtrim.side_effect = [Exception("boom"), None]

        result = archiver._trim_old_compliance_events()

        # First raised, second trimmed 3. Loop must have continued.
        assert archiver._client._redis.xtrim.call_count == 2
        assert result["streams"] == 1
        assert result["trimmed"] == 3

    def test_stream_id_sortkey_handles_malformed(self, store):
        from observability.archiver import StreamArchiver
        assert StreamArchiver._stream_id_sortkey(None) == (0, 0)
        assert StreamArchiver._stream_id_sortkey("1234-5") == (1234, 5)
        assert StreamArchiver._stream_id_sortkey(b"9999-0") == (9999, 0)
        assert StreamArchiver._stream_id_sortkey("bogus") == (0, 0)

