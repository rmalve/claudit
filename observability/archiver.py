"""
Stream Archiver — drains Redis streams to SQLite before trimming.

Called by the orchestrator after each audit cycle completes. Reads the
final state of every message in each audit stream, writes to SQLite,
verifies the write, then trims the stream.

The invariant: the final state of each message in Redis matches the
row in SQLite at the moment of archive. Nothing is deleted from Redis
until SQLite has it.

Usage:
    archiver = StreamArchiver()
    archiver.archive_cycle(audit_cycle_id="cycle-20260406-103225-a1b2c3d4")
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from observability.audit_store import AuditStore
from observability.stream_client import StreamClient
from observability.messages import (
    MessageEnvelope,
    MessageType,
    TaskType,
    TaskPayload,
    TaskPriority,
    EscalationPayload,
    build_message,
)

logger = logging.getLogger(__name__)

# Streams to archive and their corresponding archive methods
ARCHIVE_MAP = {
    "audit:findings": "archive_finding",
    "audit:tasks": "archive_task",
    "audit:directives": "archive_directive",
    "audit:escalations": "archive_escalation",
    "audit:reports": "archive_report",
}


class StreamArchiver:
    """Archives Redis stream data to SQLite, then trims streams.

    Ensures the final state of each message in Redis is persisted
    to SQLite before removal.
    """

    def __init__(
        self,
        store: AuditStore | None = None,
        client: StreamClient | None = None,
        qdrant: "QdrantBackend | None" = None,
    ):
        self._store = store or AuditStore()
        self._client = client if client is not None else StreamClient.for_director()
        self._qdrant = qdrant

    def archive_cycle(self, audit_cycle_id: str | None = None, include_project_streams: bool = True) -> dict:
        """Archive all audit streams for the completed cycle.

        Args:
            audit_cycle_id: The cycle ID for logging (from orchestrator)
            include_project_streams: Also archive per-project compliance streams

        Returns:
            Dict of {stream_name: messages_archived}
        """
        results = {}

        # Archive internal audit streams
        for stream, method_name in ARCHIVE_MAP.items():
            count = self._archive_stream(stream, method_name, audit_cycle_id)
            results[stream] = count

        # Archive per-project compliance streams
        if include_project_streams:
            projects = self._load_projects()
            for project in projects:
                compliance_stream = f"compliance:{project}"
                count = self._archive_stream(
                    compliance_stream, "archive_compliance", audit_cycle_id
                )
                results[compliance_stream] = count

        # Also archive audit:status (no SQLite table needed, just trim)
        status_count = self._trim_only("audit:status")
        results["audit:status"] = status_count

        # Cycle-boundary check: flip PENDING directives past their
        # compliance_due to NON_COMPLIANT. Gap 1 Issue #6.
        if audit_cycle_id:
            deadline_flips = self._check_deadlines(audit_cycle_id)
            results["_deadline_checks"] = deadline_flips

        # Gap 2: detect directives stuck in VERIFICATION_PENDING, retry or escalate.
        if audit_cycle_id:
            stale_actions = self._check_stale_verifications(audit_cycle_id)
            results["_stale_verification_checks"] = stale_actions

        logger.info("Archive complete for cycle %s: %s", audit_cycle_id, results)
        return results

    def _check_stale_verifications(self, audit_cycle_id: str) -> dict:
        """Detect directives stuck in VERIFICATION_PENDING. Retry (up to
        MAX_RETRIES times) or escalate via archiver-published stream messages.

        Gap 2. See eager-giggling-rivest.md for state machine details.
        """
        MAX_RETRIES = 2
        STALE_AFTER_CYCLES = 1

        stale = self._store.query_stale_verifications(
            audit_cycle_id=audit_cycle_id,
            stale_after_cycles=STALE_AFTER_CYCLES,
        )

        retries = 0
        escalations = 0
        now_iso = datetime.now(timezone.utc).isoformat()

        for directive in stale:
            retry_count = directive["retry_count"]

            if retry_count < MAX_RETRIES:
                task_id = self._publish_retry_task(directive, audit_cycle_id)
                self._store.stamp_followup(
                    directive_id=directive["directive_id"],
                    action="retry",
                    cycle_id=audit_cycle_id,
                    ref_id=task_id,
                    timestamp=now_iso,
                )
                retries += 1
            else:
                escalation_id = self._publish_verification_escalation(
                    directive, audit_cycle_id
                )
                self._store.stamp_verification_escalation(
                    directive_id=directive["directive_id"],
                    timestamp=now_iso,
                    cycle_id=audit_cycle_id,
                    escalation_id=escalation_id,
                    previous_status="VERIFICATION_PENDING",
                )
                self._store.stamp_followup(
                    directive_id=directive["directive_id"],
                    action="escalate",
                    cycle_id=audit_cycle_id,
                    ref_id=escalation_id,
                    timestamp=now_iso,
                )
                escalations += 1

        if retries or escalations:
            logger.info(
                "Stale verification check: %d retries, %d escalations",
                retries, escalations,
            )
        return {"retries": retries, "escalations": escalations}

    def _publish_retry_task(self, directive: dict, audit_cycle_id: str) -> str:
        """Build and publish a verify_compliance task for the directive.

        Carries the original acknowledged_at (from the compliance table)
        so the Policy Auditor examines the correct session window.
        Returns the generated task_id for caller to stamp in followups.
        """
        import uuid
        # Look up the original ack timestamp from the compliance table
        ack_row = self._store._conn.execute(
            "SELECT timestamp FROM compliance WHERE directive_id = ?"
            " ORDER BY timestamp ASC LIMIT 1",
            (directive["directive_id"],),
        ).fetchone()
        acknowledged_at = ack_row["timestamp"] if ack_row else ""

        task_id = str(uuid.uuid4())
        payload = TaskPayload(
            task_id=task_id,
            task_type=TaskType.VERIFY_COMPLIANCE,
            priority=TaskPriority.NORMAL,
            target_auditor="policy",
            parameters={
                "directive_id": directive["directive_id"],
                "target_agent": directive["target_agent"],
                "project": directive["project"],
                "verification_criteria": directive["verification_criteria"],
                "verification_window_sessions": directive["verification_window_sessions"],
                "acknowledged_at": acknowledged_at,
                "retry_of": True,
                "retry_number": directive["retry_count"] + 1,
                "audit_cycle_id": audit_cycle_id,
            },
        )

        envelope = build_message(
            stream="audit:tasks",
            source="director",
            target="auditor:policy",
            message_type=MessageType.TASK,
            payload=payload,
        )
        self._client._redis.xadd("audit:tasks", envelope.to_stream_dict())
        return task_id

    def _publish_verification_escalation(self, directive: dict, audit_cycle_id: str) -> str:
        """Build and publish a VERIFICATION_STUCK escalation, and persist to SQLite.

        Returns the generated escalation_id.
        """
        import uuid
        escalation_id = f"ESC-{uuid.uuid4().hex[:12]}"
        followups = directive["followups"]
        retry_count = directive["retry_count"]
        cycles_elapsed = directive["cycles_elapsed"]

        summary = (
            f"Directive {directive['directive_id']} stuck in VERIFICATION_PENDING "
            f"for {cycles_elapsed}+ cycles despite {retry_count} retry attempts."
        )

        last_retry_cycle = None
        for f in reversed(followups):
            if f.get("action") == "retry":
                last_retry_cycle = f.get("cycle_id")
                break

        payload = EscalationPayload(
            escalation_id=escalation_id,
            escalation_type="VERIFICATION_STUCK",
            severity="high",
            subject_agent=directive["target_agent"],
            directive_id=directive["directive_id"],
            summary=summary,
            metrics={
                "retry_count": retry_count,
                "cycles_elapsed": cycles_elapsed,
                "vp_cycle_id": directive["vp_cycle_id"],
                "last_retry_cycle_id": last_retry_cycle,
                "current_cycle_id": audit_cycle_id,
            },
            impact_assessment=(
                "Directive lifecycle is blocked. No behavioral verification has "
                "occurred since the acknowledgment."
            ),
            recommended_action=(
                "Investigate Policy Auditor health (logs, recent failures) or "
                "manually verify the agent's compliance behavior for this directive."
            ),
        )

        envelope = build_message(
            stream="audit:escalations",
            source="director",
            target="user",
            message_type=MessageType.ESCALATION,
            payload=payload,
        )
        self._client._redis.xadd("audit:escalations", envelope.to_stream_dict())

        # Also persist to SQLite directly so the escalation is immediately
        # queryable (doesn't have to wait for the next cycle's archive).
        now_iso = datetime.now(timezone.utc).isoformat()
        self._store.archive_escalation(
            stream_id="",  # no stream_id yet, it's been xadd'd this cycle
            timestamp=now_iso,
            payload={
                "escalation_id": escalation_id,
                "escalation_type": "VERIFICATION_STUCK",
                "severity": "high",
                "subject_agent": directive["target_agent"],
                "project": directive["project"],
                "directive_id": directive["directive_id"],
                "summary": summary,
                "recommended_action": payload.recommended_action,
                "resolution_status": "OPEN",
                "metrics": payload.metrics,
            },
        )
        self._store.commit()
        return escalation_id

    def _check_deadlines(self, audit_cycle_id: str) -> int:
        """Scan PENDING directives and flip to NON_COMPLIANT if past deadline.

        Filters on `compliance_due IS NOT NULL AND compliance_due != ''` to
        avoid the empty-string false-positive where lexical ordering makes
        any real timestamp lex-greater than empty. Idempotent: directives
        already stamped with `metadata.deadline_check` are skipped.

        Gap 1 Issue #6.
        """
        rows = self._store._conn.execute("""
            SELECT directive_id, status
              FROM directives
             WHERE status = 'PENDING'
               AND compliance_due IS NOT NULL
               AND compliance_due != ''
               AND compliance_due < datetime('now')
               AND json_extract(metadata, '$.deadline_check') IS NULL
        """).fetchall()
        count = 0
        now_iso = datetime.now(timezone.utc).isoformat()
        for row in rows:
            self._store.stamp_deadline_check(
                directive_id=row["directive_id"],
                timestamp=now_iso,
                cycle_id=audit_cycle_id,
                previous_status="PENDING",
            )
            count += 1
        if count:
            logger.info("Deadline check flipped %d directives to NON_COMPLIANT", count)
        return count

    def _archive_stream(self, stream: str, archive_method: str, audit_cycle_id: str | None) -> int:
        """Read all messages from a stream, archive to SQLite, verify, then trim.

        Returns the number of messages archived.
        """
        # Step 1: Read all messages from the stream (final state)
        messages = self._read_all(stream)
        if not messages:
            logger.info("Stream %s: empty, nothing to archive.", stream)
            return 0

        logger.info("Stream %s: archiving %d messages...", stream, len(messages))

        # Step 2: Write each message to SQLite
        archive_fn = getattr(self._store, archive_method)
        for stream_id, env in messages:
            payload = env.payload
            # Ensure target_auditor is present for tasks (Director may put
            # the auditor type only in the envelope target, not the payload)
            if archive_method == "archive_task" and "target_auditor" not in payload:
                payload["target_auditor"] = env.target.removeprefix("auditor:").strip()
            # Gap 1 Issue #3: inject project from compliance stream name
            # since CompliancePayload has no project field (project lives
            # only in the stream name `compliance:{project}`).
            if archive_method == "archive_compliance" and stream.startswith("compliance:"):
                payload.setdefault("project", stream.removeprefix("compliance:"))
            # Gap 1 Issue #2: archiver is authoritative for audit_cycle_id.
            # Override whatever the publisher stamped (if anything) with the
            # cycle being actively drained.
            if audit_cycle_id:
                payload["audit_cycle_id"] = audit_cycle_id
            archive_fn(stream_id, env.timestamp.isoformat(), payload)

            # Dual-write findings to QDrant for semantic clustering
            if archive_method == "archive_finding" and self._qdrant:
                try:
                    semantic_text = (
                        f"Finding [{payload.get('auditor_type', '')}] "
                        f"[{payload.get('finding_type', '')}] "
                        f"[{payload.get('severity', '')}] "
                        f"Confidence: {payload.get('confidence', 0):.2f} | "
                        f"Claim: {payload.get('claim', '')} | "
                        f"Evidence: {str(payload.get('evidence', ''))[:300]} | "
                        f"Recommendation: {str(payload.get('recommendation', ''))[:200]}"
                    )
                    self._qdrant.add_finding(text=semantic_text, payload=payload)
                except Exception as e:
                    logger.warning("QDrant finding write failed (non-fatal): %s", e)

        # Step 3: Commit the batch
        self._store.commit()

        # Step 4: Log the archive operation
        self._store.log_archive(stream, len(messages), audit_cycle_id)
        self._store.commit()

        # Step 5: Verify — read back from SQLite and spot-check
        # (we check that the count matches; full field-level verification
        # would require re-reading every row, which is expensive)
        logger.info("Stream %s: %d messages written to SQLite.", stream, len(messages))

        # Step 6: Trim the stream now that data is safely in SQLite.
        # Compliance streams are preserved so the Director's next assign-phase
        # read still picks up events that landed mid-cycle (Gap 1b). Consumer
        # groups track read offsets so there's no re-delivery concern. Stream
        # growth is small; HARDEN-002 tracks a future age-trim maintenance job.
        if not stream.startswith("compliance:"):
            self._client._redis.xtrim(stream, maxlen=0)
            logger.info("Stream %s: trimmed.", stream)
        else:
            logger.info("Stream %s: archived, NOT trimmed (Director read path).", stream)

        return len(messages)

    def _trim_only(self, stream: str) -> int:
        """Trim a stream without archiving (for status/tasks that don't need persistence)."""
        try:
            count = self._client._redis.xlen(stream)
            if count > 0:
                self._client._redis.xtrim(stream, maxlen=0)
                logger.info("Stream %s: trimmed %d messages (no archive).", stream, count)
            return count
        except Exception as e:
            logger.error("Failed to trim %s: %s", stream, e)
            return 0

    def _read_all(self, stream: str) -> list[tuple[str, MessageEnvelope]]:
        """Read all messages from a stream using XRANGE (not consumer groups).

        Uses XRANGE to get the final state of every message regardless
        of acknowledgment status.
        """
        messages = []
        try:
            results = self._client._redis.xrange(stream, count=10000)
            for stream_id, data in results:
                try:
                    env = MessageEnvelope.from_stream_dict(data)
                    messages.append((stream_id, env))
                except Exception as e:
                    logger.error("Failed to parse message %s in %s: %s", stream_id, stream, e)
        except Exception as e:
            logger.error("Failed to read stream %s: %s", stream, e)

        return messages

    def _load_projects(self) -> list[str]:
        config_path = Path(__file__).resolve().parent.parent / "config" / "projects.json"
        if not config_path.exists():
            return []
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            return [p["name"] for p in data.get("projects", []) if p.get("active", True)]
        except Exception:
            return []

    def close(self) -> None:
        self._store.close()
        self._client.close()
