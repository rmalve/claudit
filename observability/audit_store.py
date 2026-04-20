"""
SQLite persistent audit store.

Long-term storage for findings, directives, escalations, and compliance
events. Data is archived here from Redis streams after each audit cycle
completes — the final state of each message in Redis must match the
row in SQLite.

Redis remains the communication bus. SQLite is the durable record.

Usage:
    store = AuditStore()
    store.archive_finding({...})     # single record
    store.archive_findings([...])    # batch from stream drain
    findings = store.query_findings(project="rpi", severity="high")
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "audit.db"


class AuditStore:
    """SQLite-backed persistent audit store.

    Tables:
    - findings: all audit findings from all cycles
    - directives: all directives (internal log + delivered)
    - escalations: all escalations to the user
    - compliance: all compliance events (agent acks + verification results)
    - archive_log: tracks which streams were archived and when
    """

    def __init__(self, db_path: str | Path | None = None):
        self._db_path = Path(db_path or os.environ.get("AUDIT_DB_PATH", DEFAULT_DB_PATH))
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._ensure_tables()
        logger.info("AuditStore initialized at %s", self._db_path)

    def _ensure_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS findings (
                finding_id TEXT PRIMARY KEY,
                stream_id TEXT,
                audit_cycle_id TEXT,
                timestamp TEXT NOT NULL,
                source TEXT,
                auditor_type TEXT,
                finding_type TEXT,
                severity TEXT,
                confidence REAL,
                target_agent TEXT,
                target_session TEXT,
                project TEXT,
                claim TEXT,
                evidence TEXT,
                recommendation TEXT,
                target_event_ids TEXT,
                qdrant_refs TEXT,
                payload TEXT NOT NULL,
                archived_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_findings_project ON findings(project);
            CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(severity);
            CREATE INDEX IF NOT EXISTS idx_findings_auditor ON findings(auditor_type);
            CREATE INDEX IF NOT EXISTS idx_findings_cycle ON findings(audit_cycle_id);
            CREATE INDEX IF NOT EXISTS idx_findings_timestamp ON findings(timestamp);
            CREATE INDEX IF NOT EXISTS idx_findings_type ON findings(finding_type);

            CREATE TABLE IF NOT EXISTS directives (
                directive_id TEXT PRIMARY KEY,
                stream_id TEXT,
                timestamp TEXT NOT NULL,
                source TEXT,
                directive_type TEXT,
                target_agent TEXT,
                project TEXT,
                content TEXT,
                required_action TEXT,
                compliance_due TEXT,
                status TEXT,
                confidence REAL,
                supersedes TEXT,
                triggered_by_finding TEXT,
                supporting_metrics TEXT,
                verification_criteria TEXT,
                payload TEXT NOT NULL,
                archived_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_directives_project ON directives(project);
            CREATE INDEX IF NOT EXISTS idx_directives_status ON directives(status);
            CREATE INDEX IF NOT EXISTS idx_directives_target ON directives(target_agent);
            CREATE INDEX IF NOT EXISTS idx_directives_timestamp ON directives(timestamp);

            CREATE TABLE IF NOT EXISTS escalations (
                escalation_id TEXT PRIMARY KEY,
                stream_id TEXT,
                timestamp TEXT NOT NULL,
                severity TEXT,
                escalation_type TEXT,
                subject_agent TEXT,
                project TEXT,
                directive_id TEXT,
                promotion_id TEXT,
                summary TEXT,
                recommended_action TEXT,
                resolution_status TEXT DEFAULT 'OPEN',
                resolution_timestamp TEXT,
                payload TEXT NOT NULL,
                archived_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_escalations_severity ON escalations(severity);
            CREATE INDEX IF NOT EXISTS idx_escalations_project ON escalations(project);
            CREATE INDEX IF NOT EXISTS idx_escalations_timestamp ON escalations(timestamp);

            CREATE TABLE IF NOT EXISTS compliance (
                compliance_id TEXT PRIMARY KEY,
                stream_id TEXT,
                timestamp TEXT NOT NULL,
                directive_id TEXT,
                agent TEXT,
                agent_version TEXT,
                session_id TEXT,
                project TEXT,
                action_taken TEXT,
                conflict_reason TEXT,
                is_verification INTEGER DEFAULT 0,
                verification_passed INTEGER,
                sessions_examined INTEGER,
                verification_evidence TEXT,
                payload TEXT NOT NULL,
                archived_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_compliance_directive ON compliance(directive_id);
            CREATE INDEX IF NOT EXISTS idx_compliance_agent ON compliance(agent);
            CREATE INDEX IF NOT EXISTS idx_compliance_timestamp ON compliance(timestamp);

            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                stream_id TEXT,
                audit_cycle_id TEXT,
                timestamp TEXT NOT NULL,
                task_type TEXT,
                priority TEXT,
                target_auditor TEXT,
                project TEXT,
                scope TEXT,
                session_ids TEXT,
                parameters TEXT,
                payload TEXT NOT NULL,
                archived_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_tasks_auditor ON tasks(target_auditor);
            CREATE INDEX IF NOT EXISTS idx_tasks_cycle ON tasks(audit_cycle_id);
            CREATE INDEX IF NOT EXISTS idx_tasks_timestamp ON tasks(timestamp);
            CREATE INDEX IF NOT EXISTS idx_tasks_type ON tasks(task_type);

            CREATE TABLE IF NOT EXISTS reports (
                report_id TEXT PRIMARY KEY,
                stream_id TEXT,
                audit_cycle_id TEXT,
                timestamp TEXT NOT NULL,
                project TEXT,
                overall_risk TEXT,
                summary TEXT,
                findings_count TEXT,
                directives_issued INTEGER,
                sessions_audited TEXT,
                auditor_status TEXT,
                payload TEXT NOT NULL,
                archived_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_reports_project ON reports(project);
            CREATE INDEX IF NOT EXISTS idx_reports_timestamp ON reports(timestamp);
            CREATE INDEX IF NOT EXISTS idx_reports_cycle ON reports(audit_cycle_id);

            CREATE TABLE IF NOT EXISTS archive_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stream TEXT NOT NULL,
                messages_archived INTEGER NOT NULL,
                archived_at TEXT NOT NULL,
                audit_cycle_id TEXT
            );

            CREATE TABLE IF NOT EXISTS promotion_decisions (
                promotion_id TEXT PRIMARY KEY,
                directive_id TEXT NOT NULL,
                project TEXT NOT NULL,
                decision_type TEXT NOT NULL,
                audit_cycle_id TEXT,
                timestamp TEXT NOT NULL,
                classification_reasoning TEXT,
                supersession_reasoning TEXT,
                alternatives_considered TEXT,
                rationale TEXT,
                add_verbiage TEXT,
                remove_verbiage TEXT,
                target_agents TEXT,
                standing_file_snapshot TEXT,
                conflict_candidates TEXT,
                inputs TEXT,
                outcome_standing_directive_id TEXT,
                outcome_superseded_ids TEXT,
                status TEXT DEFAULT 'PENDING_ACK',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_promotions_project ON promotion_decisions(project);
            CREATE INDEX IF NOT EXISTS idx_promotions_directive ON promotion_decisions(directive_id);
            CREATE INDEX IF NOT EXISTS idx_promotions_status ON promotion_decisions(status);
            CREATE INDEX IF NOT EXISTS idx_promotions_timestamp ON promotion_decisions(timestamp);

            CREATE TABLE IF NOT EXISTS standing_directives (
                standing_directive_id TEXT PRIMARY KEY,
                project TEXT NOT NULL,
                promotion_id TEXT NOT NULL,
                verbiage TEXT NOT NULL,
                status TEXT DEFAULT 'ACTIVE',
                superseded_by TEXT,
                created_at TEXT NOT NULL,
                superseded_at TEXT,
                FOREIGN KEY (promotion_id) REFERENCES promotion_decisions(promotion_id)
            );

            CREATE INDEX IF NOT EXISTS idx_standing_project ON standing_directives(project);
            CREATE INDEX IF NOT EXISTS idx_standing_status ON standing_directives(status);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_standing_promotion_active
                ON standing_directives(promotion_id) WHERE status = 'ACTIVE';

            CREATE TABLE IF NOT EXISTS escalation_messages (
                message_id TEXT PRIMARY KEY,
                escalation_id TEXT NOT NULL,
                author TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (escalation_id) REFERENCES escalations(escalation_id)
            );

            CREATE INDEX IF NOT EXISTS idx_esc_messages_escalation ON escalation_messages(escalation_id);
            CREATE INDEX IF NOT EXISTS idx_esc_messages_timestamp ON escalation_messages(timestamp);
        """)
        self._conn.commit()

        # Migrate existing tables (add columns that didn't exist in earlier schema)
        self._migrate_tables()

        # Create indexes that depend on migrated columns
        try:
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_escalations_type ON escalations(escalation_type)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_escalations_resolution ON escalations(resolution_status)")
            # Directive lifecycle indexes (Gap 1)
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_compliance_cycle ON compliance(audit_cycle_id)")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_findings_target_directive "
                "ON findings(target_directive_id) WHERE target_directive_id IS NOT NULL"
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_directives_cycle ON directives(audit_cycle_id)")
            self._conn.commit()
        except sqlite3.OperationalError:
            pass

        # Create the directive_lifecycle view (Gap 1)
        self._create_directive_lifecycle_view()

    def _create_directive_lifecycle_view(self) -> None:
        """Create the directive_lifecycle view that derives transitions from
        existing source tables. Idempotent via DROP VIEW IF EXISTS so the
        view definition can be updated freely on redeploy.

        See eager-giggling-rivest.md for the full design rationale and the
        trigger vocabulary. Every transition is traceable back to either a
        stream-archived event row or a metadata JSON key on the directive.
        """
        self._conn.execute("DROP VIEW IF EXISTS directive_lifecycle")
        self._conn.execute("""
            CREATE VIEW directive_lifecycle AS
              -- ∅ → PENDING: every directive produces a publication row
              SELECT directive_id,
                     NULL AS from_status,
                     'PENDING' AS to_status,
                     timestamp AS transition_timestamp,
                     audit_cycle_id,
                     'directive_published' AS trigger,
                     directive_id AS source_ref,
                     project
                FROM directives
              UNION ALL
              -- PENDING → ACKNOWLEDGED (compliance event, no conflict)
              SELECT directive_id, 'PENDING', 'ACKNOWLEDGED',
                     timestamp, audit_cycle_id,
                     'compliance_ack', compliance_id,
                     project
                FROM compliance
               WHERE conflict_reason IS NULL AND is_verification = 0
              UNION ALL
              -- ACKNOWLEDGED → VERIFICATION_PENDING (same event, spec auto-step)
              SELECT directive_id, 'ACKNOWLEDGED', 'VERIFICATION_PENDING',
                     timestamp, audit_cycle_id,
                     'ack_auto_verification', compliance_id,
                     project
                FROM compliance
               WHERE conflict_reason IS NULL AND is_verification = 0
              UNION ALL
              -- PENDING → ESCALATED (conflict_reason set)
              SELECT directive_id, 'PENDING', 'ESCALATED',
                     timestamp, audit_cycle_id,
                     'compliance_conflict', compliance_id,
                     project
                FROM compliance
               WHERE conflict_reason IS NOT NULL
              UNION ALL
              -- VERIFICATION_PENDING → VERIFIED_COMPLIANT / VERIFIED_NON_COMPLIANT
              SELECT target_directive_id, 'VERIFICATION_PENDING',
                     CASE verification_result
                          WHEN 'compliant'     THEN 'VERIFIED_COMPLIANT'
                          WHEN 'non_compliant' THEN 'VERIFIED_NON_COMPLIANT'
                     END,
                     timestamp, audit_cycle_id,
                     'verification_finding', finding_id,
                     project
                FROM findings
               WHERE target_directive_id IS NOT NULL AND verification_result IS NOT NULL
              UNION ALL
              -- * → SUPERSEDED (superseding directive points at old one)
              SELECT supersedes, NULL, 'SUPERSEDED',
                     timestamp, audit_cycle_id,
                     'supersede', directive_id,
                     project
                FROM directives
               WHERE supersedes IS NOT NULL
              UNION ALL
              -- PENDING → NON_COMPLIANT (archiver cycle-boundary check)
              SELECT directive_id, 'PENDING', 'NON_COMPLIANT',
                     json_extract(metadata, '$.deadline_check.first_seen_at'),
                     json_extract(metadata, '$.deadline_check.cycle_id'),
                     'deadline_passed', NULL,
                     project
                FROM directives
               WHERE json_extract(metadata, '$.deadline_check.first_seen_at') IS NOT NULL
              UNION ALL
              -- * → DISMISSED (dashboard /dismiss; from_status snapshot in metadata)
              SELECT directive_id,
                     json_extract(metadata, '$.dismissal.previous_status'),
                     'DISMISSED',
                     json_extract(metadata, '$.dismissal.timestamp'),
                     json_extract(metadata, '$.dismissal.cycle_id'),
                     'user_dismiss', NULL,
                     project
                FROM directives
               WHERE json_extract(metadata, '$.dismissal') IS NOT NULL
              UNION ALL
              -- VERIFICATION_PENDING → ESCALATED (archiver stale-verification; Gap 2)
              SELECT directive_id,
                     json_extract(metadata, '$.verification_escalation.previous_status'),
                     'ESCALATED',
                     json_extract(metadata, '$.verification_escalation.timestamp'),
                     json_extract(metadata, '$.verification_escalation.cycle_id'),
                     'verification_stuck',
                     json_extract(metadata, '$.verification_escalation.escalation_id'),
                     project
                FROM directives
               WHERE json_extract(metadata, '$.verification_escalation') IS NOT NULL
        """)
        self._conn.commit()

    def _migrate_tables(self) -> None:
        """Add columns to existing tables that were created before schema updates."""
        migrations = [
            ("escalations", "promotion_id", "TEXT"),
            ("escalations", "resolution_status", "TEXT DEFAULT 'OPEN'"),
            ("escalations", "resolution_timestamp", "TEXT"),
            # Directive lifecycle persistence (Gap 1)
            ("directives", "audit_cycle_id", "TEXT"),
            ("directives", "metadata", "TEXT DEFAULT '{}'"),
            ("compliance", "audit_cycle_id", "TEXT"),
            ("findings", "target_directive_id", "TEXT"),
            ("findings", "verification_result", "TEXT"),
            ("findings", "verification_evidence", "TEXT DEFAULT ''"),
            ("findings", "sessions_examined", "INTEGER DEFAULT 0"),
        ]
        for table, column, col_type in migrations:
            try:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
                logger.info("Migrated: added %s.%s", table, column)
            except sqlite3.OperationalError:
                pass  # column already exists
        self._conn.commit()

    # ── Archive methods (batch, from stream drain) ──

    def archive_finding(self, stream_id: str, timestamp: str, payload: dict) -> None:
        """Archive a single finding from Redis stream."""
        now = datetime.now(timezone.utc).isoformat()
        evidence = payload.get("evidence", "")
        if not isinstance(evidence, str):
            evidence = json.dumps(evidence, default=str)

        self._conn.execute("""
            INSERT OR REPLACE INTO findings
            (finding_id, stream_id, audit_cycle_id, timestamp, source,
             auditor_type, finding_type, severity, confidence,
             target_agent, target_session, project, claim, evidence,
             recommendation, target_event_ids, qdrant_refs, payload, archived_at,
             target_directive_id, verification_result, verification_evidence,
             sessions_examined)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            payload.get("finding_id", stream_id),
            stream_id,
            payload.get("audit_cycle_id"),
            timestamp,
            payload.get("source", ""),
            payload.get("auditor_type", payload.get("auditor", "")),
            payload.get("finding_type", ""),
            payload.get("severity", ""),
            payload.get("confidence"),
            payload.get("target_agent", ""),
            payload.get("target_session", ""),
            payload.get("project", ""),
            payload.get("claim", payload.get("title", "")),
            evidence,
            payload.get("recommendation", payload.get("recommended_action", "")),
            json.dumps(payload.get("target_event_ids", []), default=str),
            json.dumps(payload.get("qdrant_refs", {}), default=str),
            json.dumps(payload, default=str),
            now,
            # Verification fields (Gap 1) — NULL/default when not a verification finding
            payload.get("target_directive_id"),
            payload.get("verification_result"),
            payload.get("verification_evidence", ""),
            payload.get("sessions_examined", 0),
        ))

    def archive_directive(self, stream_id: str, timestamp: str, payload: dict) -> None:
        """Archive a single directive from Redis stream."""
        now = datetime.now(timezone.utc).isoformat()
        # Preserve existing metadata (e.g., dismissal/deadline_check) if this
        # is an update to an already-stamped directive. INSERT OR REPLACE
        # would otherwise wipe the metadata column.
        existing_metadata = self._conn.execute(
            "SELECT metadata FROM directives WHERE directive_id = ?",
            (payload.get("directive_id", stream_id),),
        ).fetchone()
        metadata = existing_metadata["metadata"] if existing_metadata else "{}"
        self._conn.execute("""
            INSERT OR REPLACE INTO directives
            (directive_id, stream_id, timestamp, source, directive_type,
             target_agent, project, content, required_action, compliance_due,
             status, confidence, supersedes, triggered_by_finding,
             supporting_metrics, verification_criteria, payload, archived_at,
             audit_cycle_id, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            payload.get("directive_id", stream_id),
            stream_id,
            timestamp,
            payload.get("source", payload.get("issued_by", "")),
            payload.get("type", payload.get("directive_type", "")),
            payload.get("target_agent", ""),
            payload.get("project", ""),
            payload.get("content", payload.get("description", "")),
            payload.get("required_action", ""),
            payload.get("compliance_due", ""),
            payload.get("status", "PENDING"),
            payload.get("confidence"),
            payload.get("supersedes"),
            payload.get("triggered_by_finding", payload.get("finding_ref", "")),
            json.dumps(payload.get("supporting_metrics", payload.get("metrics", {})), default=str),
            payload.get("verification_criteria", ""),
            json.dumps(payload, default=str),
            now,
            payload.get("audit_cycle_id"),
            metadata,
        ))

    def archive_escalation(self, stream_id: str, timestamp: str, payload: dict) -> None:
        """Archive a single escalation from Redis stream."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute("""
            INSERT OR REPLACE INTO escalations
            (escalation_id, stream_id, timestamp, severity, escalation_type,
             subject_agent, project, directive_id, promotion_id, summary,
             recommended_action, resolution_status, payload, archived_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            payload.get("escalation_id", stream_id),
            stream_id,
            timestamp,
            payload.get("severity", ""),
            payload.get("category", payload.get("escalation_type", "")),
            payload.get("subject_agent", ""),
            payload.get("project", ""),
            payload.get("directive_id"),
            payload.get("promotion_id"),
            payload.get("summary", payload.get("title", "")),
            payload.get("recommended_action", ""),
            payload.get("resolution_status", "OPEN"),
            json.dumps(payload, default=str),
            now,
        ))

    def archive_compliance(self, stream_id: str, timestamp: str, payload: dict) -> None:
        """Archive a single compliance event from Redis stream."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute("""
            INSERT OR REPLACE INTO compliance
            (compliance_id, stream_id, timestamp, directive_id, agent,
             agent_version, session_id, project, action_taken,
             conflict_reason, is_verification, verification_passed,
             sessions_examined, verification_evidence, payload, archived_at,
             audit_cycle_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            payload.get("compliance_id", stream_id),
            stream_id,
            timestamp,
            payload.get("directive_id", ""),
            payload.get("agent", ""),
            payload.get("agent_version"),
            payload.get("session_id"),
            payload.get("project", ""),
            payload.get("action_taken", ""),
            payload.get("conflict_reason"),
            1 if payload.get("is_verification") else 0,
            1 if payload.get("verification_passed") else (0 if payload.get("verification_passed") is False else None),
            payload.get("sessions_examined"),
            payload.get("verification_evidence", ""),
            json.dumps(payload, default=str),
            now,
            payload.get("audit_cycle_id"),
        ))

    def archive_task(self, stream_id: str, timestamp: str, payload: dict) -> None:
        """Archive a single task assignment from Redis stream."""
        now = datetime.now(timezone.utc).isoformat()
        params = payload.get("parameters", {})
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except (json.JSONDecodeError, TypeError):
                params = {}

        self._conn.execute("""
            INSERT OR REPLACE INTO tasks
            (task_id, stream_id, audit_cycle_id, timestamp, task_type,
             priority, target_auditor, project, scope, session_ids,
             parameters, payload, archived_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            payload.get("task_id", stream_id),
            stream_id,
            payload.get("audit_cycle_id"),
            timestamp,
            payload.get("task_type", payload.get("task", "")),
            payload.get("priority", ""),
            payload.get("target_auditor", ""),
            params.get("project", payload.get("project", "")),
            params.get("scope", payload.get("scope", "")),
            json.dumps(params.get("session_ids", payload.get("session_ids", [])), default=str),
            json.dumps(params, default=str),
            json.dumps(payload, default=str),
            now,
        ))

    def archive_report(self, stream_id: str, timestamp: str, payload: dict) -> None:
        """Archive a single session report from Redis stream."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute("""
            INSERT OR REPLACE INTO reports
            (report_id, stream_id, audit_cycle_id, timestamp, project,
             overall_risk, summary, findings_count, directives_issued,
             sessions_audited, auditor_status, payload, archived_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            payload.get("report_id", stream_id),
            stream_id,
            payload.get("audit_cycle", payload.get("audit_cycle_id")),
            timestamp,
            payload.get("project", ""),
            payload.get("overall_risk", ""),
            payload.get("summary", ""),
            json.dumps(payload.get("findings_count", {}), default=str),
            payload.get("directives_issued"),
            json.dumps(payload.get("sessions_audited", []), default=str),
            json.dumps(payload.get("auditor_status", {}), default=str),
            json.dumps(payload, default=str),
            now,
        ))

    def log_archive(self, stream: str, count: int, audit_cycle_id: str | None = None) -> None:
        """Log that an archive operation completed."""
        self._conn.execute("""
            INSERT INTO archive_log (stream, messages_archived, archived_at, audit_cycle_id)
            VALUES (?, ?, ?, ?)
        """, (stream, count, datetime.now(timezone.utc).isoformat(), audit_cycle_id))

    def commit(self) -> None:
        self._conn.commit()

    # ── Query methods (for dashboard API) ──

    def query_findings(
        self,
        project: str | None = None,
        auditor_type: str | None = None,
        severity: str | None = None,
        finding_type: str | None = None,
        audit_cycle_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        conditions = []
        params = []

        if project:
            conditions.append("project = ?")
            params.append(project)
        if auditor_type:
            conditions.append("auditor_type = ?")
            params.append(auditor_type)
        if severity:
            conditions.append("LOWER(severity) = LOWER(?)")
            params.append(severity)
        if finding_type:
            conditions.append("LOWER(finding_type) = LOWER(?)")
            params.append(finding_type)
        if audit_cycle_id:
            conditions.append("audit_cycle_id = ?")
            params.append(audit_cycle_id)
        if start_date:
            conditions.append("timestamp >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("timestamp <= ?")
            params.append(end_date)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)

        rows = self._conn.execute(
            f"SELECT * FROM findings {where} ORDER BY timestamp DESC LIMIT ?", params
        ).fetchall()

        return [self._row_to_dict(r) for r in rows]

    def query_directives(
        self,
        project: str | None = None,
        status: str | None = None,
        directive_type: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        conditions = []
        params = []

        if project:
            conditions.append("project = ?")
            params.append(project)
        if status:
            conditions.append("UPPER(status) = UPPER(?)")
            params.append(status)
        if directive_type:
            conditions.append("UPPER(directive_type) = UPPER(?)")
            params.append(directive_type)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)

        rows = self._conn.execute(
            f"SELECT * FROM directives {where} ORDER BY timestamp DESC LIMIT ?", params
        ).fetchall()

        return [self._row_to_dict(r) for r in rows]

    def update_directive_status(self, directive_id: str, status: str) -> None:
        """Update directive status (e.g. PENDING → DISMISSED)."""
        self._conn.execute(
            "UPDATE directives SET status = ? WHERE directive_id = ?",
            (status, directive_id),
        )
        self._conn.commit()

    # ── Directive lifecycle stamp methods (Gap 1) ──

    def stamp_dismissal(
        self,
        directive_id: str,
        timestamp: str,
        reason: str,
        cycle_id: str | None,
        previous_status: str,
    ) -> None:
        """Atomically record a dismissal in metadata and flip status to DISMISSED.

        The `previous_status` snapshot is load-bearing: the directive_lifecycle
        view reads it as the `from_status` for the DISMISSED transition row.
        Without it, the view would render 'DISMISSED → DISMISSED' after the
        status column is flipped.
        """
        row = self._conn.execute(
            "SELECT metadata FROM directives WHERE directive_id = ?", (directive_id,)
        ).fetchone()
        metadata = json.loads(row["metadata"] or "{}") if row else {}
        metadata["dismissal"] = {
            "timestamp": timestamp,
            "reason": reason,
            "cycle_id": cycle_id,
            "previous_status": previous_status,
        }
        self._conn.execute(
            "UPDATE directives SET status = 'DISMISSED', metadata = ? WHERE directive_id = ?",
            (json.dumps(metadata), directive_id),
        )
        self._conn.commit()

    def dismiss_directive(
        self,
        directive_id: str,
        reason: str,
        cycle_id: str | None = None,
        timestamp: str | None = None,
    ) -> dict | None:
        """Atomic dismissal: read current status, stamp metadata, flip status.

        Returns {directive_id, previous_status, dismissed_at} on success, or
        None if the directive doesn't exist in SQLite. Callers should ensure
        the directive has been archived before calling this — otherwise the
        dismissal no-ops silently (returning None).
        """
        if timestamp is None:
            timestamp = datetime.now(timezone.utc).isoformat()
        row = self._conn.execute(
            "SELECT status FROM directives WHERE directive_id = ?", (directive_id,)
        ).fetchone()
        if row is None:
            return None
        previous_status = row["status"] or "PENDING"
        self.stamp_dismissal(
            directive_id=directive_id,
            timestamp=timestamp,
            reason=reason,
            cycle_id=cycle_id,
            previous_status=previous_status,
        )
        return {
            "directive_id": directive_id,
            "previous_status": previous_status,
            "dismissed_at": timestamp,
        }

    def stamp_deadline_check(
        self,
        directive_id: str,
        timestamp: str,
        cycle_id: str | None,
        previous_status: str,
    ) -> None:
        """Atomically record a deadline-passed NON_COMPLIANT flip.

        Called by the archiver cycle-boundary check when a PENDING directive
        with a past compliance_due has no acknowledgment.
        """
        row = self._conn.execute(
            "SELECT metadata FROM directives WHERE directive_id = ?", (directive_id,)
        ).fetchone()
        metadata = json.loads(row["metadata"] or "{}") if row else {}
        metadata["deadline_check"] = {
            "first_seen_at": timestamp,
            "cycle_id": cycle_id,
            "previous_status": previous_status,
        }
        self._conn.execute(
            "UPDATE directives SET status = 'NON_COMPLIANT', metadata = ? WHERE directive_id = ?",
            (json.dumps(metadata), directive_id),
        )
        self._conn.commit()

    def stamp_followup(
        self,
        directive_id: str,
        action: str,
        cycle_id: str,
        ref_id: str,
        timestamp: str,
    ) -> None:
        """Append a follow-up entry to directives.metadata.followups[].

        `action` is "retry" or "escalate". `ref_id` becomes `task_id` for
        retries or `escalation_id` for escalations. Does NOT flip the
        directive status — that's the caller's job (for escalations, use
        stamp_verification_escalation which also updates metadata here).

        Gap 2.
        """
        row = self._conn.execute(
            "SELECT metadata FROM directives WHERE directive_id = ?", (directive_id,)
        ).fetchone()
        metadata = json.loads(row["metadata"] or "{}") if row else {}
        followups = metadata.setdefault("followups", [])
        entry = {
            "action": action,
            "cycle_id": cycle_id,
            "timestamp": timestamp,
        }
        if action == "retry":
            entry["task_id"] = ref_id
        elif action == "escalate":
            entry["escalation_id"] = ref_id
        followups.append(entry)
        self._conn.execute(
            "UPDATE directives SET metadata = ? WHERE directive_id = ?",
            (json.dumps(metadata), directive_id),
        )
        self._conn.commit()

    def stamp_verification_escalation(
        self,
        directive_id: str,
        timestamp: str,
        cycle_id: str,
        escalation_id: str,
        previous_status: str,
    ) -> None:
        """Atomically mark a directive as stuck-verification-escalated.

        Writes metadata.verification_escalation = {timestamp, cycle_id,
        escalation_id, previous_status} and flips directives.status =
        'ESCALATED' in a single transaction. The view's `verification_stuck`
        branch renders a VERIFICATION_PENDING → ESCALATED transition row
        from this metadata snapshot.

        Gap 2.
        """
        row = self._conn.execute(
            "SELECT metadata FROM directives WHERE directive_id = ?", (directive_id,)
        ).fetchone()
        metadata = json.loads(row["metadata"] or "{}") if row else {}
        metadata["verification_escalation"] = {
            "timestamp": timestamp,
            "cycle_id": cycle_id,
            "escalation_id": escalation_id,
            "previous_status": previous_status,
        }
        self._conn.execute(
            "UPDATE directives SET status = 'ESCALATED', metadata = ? WHERE directive_id = ?",
            (json.dumps(metadata), directive_id),
        )
        self._conn.commit()

    def query_stale_verifications(
        self,
        audit_cycle_id: str,
        stale_after_cycles: int = 1,
    ) -> list[dict]:
        """Return VP directives that need retry or escalation.

        Excludes directives already escalated via metadata.verification_escalation.
        For each candidate, computes `cycles_elapsed` since the last lifecycle
        activity (VP transition or most recent followup) using archive_log as
        the cycle ordinality source. Skips directives where elapsed < threshold.

        Called by archiver._check_stale_verifications in Phase 6. Gap 2.
        """
        # NOTE: verification_window_sessions exists on the DirectiveEvent pydantic
        # model (schemas.py) but is NOT carried on the SQL directives table or in
        # the IPC DirectivePayload. Archiver retry tasks use a default of 5 when
        # building the task payload. Tracked as a schema-gap in Gap 2 plan.
        candidates = self._conn.execute("""
            SELECT d.directive_id, d.target_agent, d.project,
                   d.verification_criteria,
                   d.metadata, d.compliance_due, d.audit_cycle_id AS directive_cycle_id
              FROM directives d
             WHERE d.status = 'VERIFICATION_PENDING'
               AND json_extract(d.metadata, '$.verification_escalation') IS NULL
        """).fetchall()

        results = []
        for row in candidates:
            metadata = json.loads(row["metadata"] or "{}")
            followups = metadata.get("followups", [])
            retry_count = sum(1 for f in followups if f.get("action") == "retry")

            # Find the VP transition's audit_cycle_id from the view.
            vp_row = self._conn.execute(
                "SELECT audit_cycle_id FROM directive_lifecycle"
                " WHERE directive_id = ? AND to_status = 'VERIFICATION_PENDING'"
                " ORDER BY transition_timestamp DESC LIMIT 1",
                (row["directive_id"],),
            ).fetchone()
            if vp_row is None or vp_row["audit_cycle_id"] is None:
                # Directive is VP in status but has no VP transition in the view
                # (e.g., manual status flip, missing compliance row). Skip.
                continue
            vp_cycle_id = vp_row["audit_cycle_id"]

            # Latest followup cycle (if any) — use max() for lex comparison
            followup_cycles = [f["cycle_id"] for f in followups if f.get("cycle_id")]
            if followup_cycles:
                latest_followup_cycle = max(followup_cycles)
                last_action_cycle = max(vp_cycle_id, latest_followup_cycle)
            else:
                last_action_cycle = vp_cycle_id

            # Count distinct archive cycles between last_action (exclusive) and current (inclusive).
            # Lex comparison is safe — cycle-ids are `cycle-YYYYMMDD-HHMMSS-...` so lex == chronological.
            elapsed_row = self._conn.execute(
                "SELECT COUNT(DISTINCT audit_cycle_id) AS n FROM archive_log"
                " WHERE audit_cycle_id IS NOT NULL"
                "   AND audit_cycle_id > ?"
                "   AND audit_cycle_id <= ?",
                (last_action_cycle, audit_cycle_id),
            ).fetchone()
            cycles_elapsed = elapsed_row["n"] if elapsed_row else 0

            if cycles_elapsed < stale_after_cycles:
                continue

            results.append({
                "directive_id": row["directive_id"],
                "target_agent": row["target_agent"],
                "project": row["project"],
                "verification_criteria": row["verification_criteria"],
                "verification_window_sessions": 5,  # default; see NOTE above
                "compliance_due": row["compliance_due"],
                "followups": followups,
                "retry_count": retry_count,
                "cycles_elapsed": cycles_elapsed,
                "last_action_cycle_id": last_action_cycle,
                "vp_cycle_id": vp_cycle_id,
            })
        return results

    def query_directive_lifecycle(self, directive_id: str) -> list[dict]:
        """Return the full transition timeline for a single directive, ordered."""
        rows = self._conn.execute(
            "SELECT * FROM directive_lifecycle WHERE directive_id = ?"
            " ORDER BY transition_timestamp, to_status",
            (directive_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def query_non_verified_counts(self, project: str) -> dict:
        """Count distinct directives in each terminal-non-verified state
        for the Chart C counter strip. Categories are lowercased in the
        returned dict.
        """
        categories = [
            "NON_COMPLIANT",
            "VERIFIED_NON_COMPLIANT",
            "ESCALATED",
            "DISMISSED",
            "SUPERSEDED",
        ]
        counts = {}
        for cat in categories:
            row = self._conn.execute(
                "SELECT COUNT(DISTINCT directive_id) AS n FROM directive_lifecycle"
                " WHERE to_status = ? AND project = ?",
                (cat, project),
            ).fetchone()
            counts[cat.lower()] = row["n"] if row else 0
        return counts

    def query_cycles_to_verification(
        self,
        project: str,
        last_n_cycles: int = 20,
    ) -> list[dict]:
        """Return directives that reached VERIFIED_COMPLIANT with their
        publication and verification cycle markers.

        Dashboard computes cycles-elapsed (verified_cycle - published_cycle)
        and aggregates into median + IQR per cycle bucket. Only directives
        whose project matches are returned. The `last_n_cycles` parameter
        limits the tail of the result set roughly — frontend filters further.
        """
        rows = self._conn.execute("""
            SELECT pub.directive_id AS directive_id,
                   pub.audit_cycle_id AS published_cycle,
                   ver.audit_cycle_id AS verified_cycle,
                   ver.transition_timestamp AS verified_at,
                   pub.project AS project
              FROM directive_lifecycle pub
              JOIN directive_lifecycle ver USING (directive_id)
             WHERE pub.to_status = 'PENDING'
               AND pub.trigger = 'directive_published'
               AND ver.to_status = 'VERIFIED_COMPLIANT'
               AND pub.project = ?
             ORDER BY ver.transition_timestamp DESC
             LIMIT ?
        """, (project, max(last_n_cycles * 100, 100))).fetchall()
        return [dict(r) for r in rows]

    def query_escalations(
        self,
        severity: str | None = None,
        project: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        conditions = []
        params = []

        if severity:
            conditions.append("LOWER(severity) = LOWER(?)")
            params.append(severity)
        if project:
            conditions.append("project = ?")
            params.append(project)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)

        rows = self._conn.execute(
            f"SELECT * FROM escalations {where} ORDER BY timestamp DESC LIMIT ?", params
        ).fetchall()

        return [self._row_to_dict(r) for r in rows]

    def query_compliance(
        self,
        directive_id: str | None = None,
        agent: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        conditions = []
        params = []

        if directive_id:
            conditions.append("directive_id = ?")
            params.append(directive_id)
        if agent:
            conditions.append("agent = ?")
            params.append(agent)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)

        rows = self._conn.execute(
            f"SELECT * FROM compliance {where} ORDER BY timestamp DESC LIMIT ?", params
        ).fetchall()

        return [self._row_to_dict(r) for r in rows]

    def query_reports(
        self,
        project: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        conditions = []
        params = []

        if project:
            conditions.append("project = ?")
            params.append(project)
        if start_date:
            conditions.append("timestamp >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("timestamp <= ?")
            params.append(end_date)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)

        rows = self._conn.execute(
            f"SELECT * FROM reports {where} ORDER BY timestamp DESC LIMIT ?", params
        ).fetchall()

        return [self._row_to_dict(r) for r in rows]

    def get_findings_by_cycle(
        self, project: str | None = None, start_date: str | None = None, end_date: str | None = None,
    ) -> list[dict]:
        """Group findings by audit_cycle_id for the line chart."""
        conditions = []
        params = []

        if project:
            conditions.append("project = ?")
            params.append(project)
        if start_date:
            conditions.append("timestamp >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("timestamp <= ?")
            params.append(end_date)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        rows = self._conn.execute(f"""
            SELECT audit_cycle_id, auditor_type, MIN(timestamp) as first_ts, COUNT(*) as count
            FROM findings
            {where}
            GROUP BY audit_cycle_id, auditor_type
            ORDER BY first_ts
        """, params).fetchall()

        cycles = {}
        for r in rows:
            cid = r["audit_cycle_id"] or "unknown"
            if cid not in cycles:
                cycles[cid] = {"cycle_id": cid, "timestamp": r["first_ts"], "total": 0}
            cycles[cid][r["auditor_type"] or "director"] = r["count"]
            cycles[cid]["total"] += r["count"]

        return sorted(cycles.values(), key=lambda c: c.get("timestamp", ""))

    def get_stats(
        self, project: str | None = None, start_date: str | None = None, end_date: str | None = None,
    ) -> dict:
        """Aggregate stats for the dashboard overview."""
        conditions = []
        params = []

        if project:
            conditions.append("project = ?")
            params.append(project)
        if start_date:
            conditions.append("timestamp >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("timestamp <= ?")
            params.append(end_date)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        # Findings by severity
        sev_rows = self._conn.execute(f"""
            SELECT LOWER(severity) as sev, COUNT(*) as count
            FROM findings {where}
            GROUP BY LOWER(severity)
        """, params).fetchall()
        by_severity = {r["sev"]: r["count"] for r in sev_rows}

        # Findings by auditor
        aud_rows = self._conn.execute(f"""
            SELECT auditor_type, COUNT(*) as count
            FROM findings {where}
            GROUP BY auditor_type
        """, params).fetchall()
        by_auditor = {r["auditor_type"] or "director": r["count"] for r in aud_rows}

        # Findings by type
        type_rows = self._conn.execute(f"""
            SELECT LOWER(finding_type) as ft, COUNT(*) as count
            FROM findings {where}
            GROUP BY LOWER(finding_type)
        """, params).fetchall()
        by_type = {r["ft"]: r["count"] for r in type_rows}

        # Total
        total_row = self._conn.execute(f"""
            SELECT COUNT(*) as count FROM findings {where}
        """, params).fetchone()

        return {
            "findings_by_severity": by_severity,
            "findings_by_auditor": by_auditor,
            "findings_by_type": by_type,
            "total_findings": total_row["count"],
        }

    # ── Promotion methods (direct writes, not archive-from-stream) ──

    def insert_promotion_decision(self, decision: dict) -> None:
        """Record a promotion decision directly to SQLite.

        This bypasses the Redis → archive path because promotion decisions
        require immediate durability — the next external session depends on it.
        """
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute("""
            INSERT OR REPLACE INTO promotion_decisions
            (promotion_id, directive_id, project, decision_type, audit_cycle_id,
             timestamp, classification_reasoning, supersession_reasoning,
             alternatives_considered, rationale, add_verbiage, remove_verbiage,
             target_agents, standing_file_snapshot, conflict_candidates, inputs,
             outcome_standing_directive_id, outcome_superseded_ids, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            decision["promotion_id"],
            decision["directive_id"],
            decision["project"],
            decision["decision_type"],
            decision.get("audit_cycle_id"),
            decision.get("timestamp", now),
            decision.get("classification_reasoning", ""),
            decision.get("supersession_reasoning", ""),
            decision.get("alternatives_considered", ""),
            decision.get("rationale", ""),
            decision.get("add_verbiage", ""),
            decision.get("remove_verbiage"),
            json.dumps(decision.get("target_agents", []), default=str),
            json.dumps(decision.get("standing_file_snapshot", []), default=str),
            json.dumps(decision.get("conflict_candidates", []), default=str),
            json.dumps(decision.get("inputs", {}), default=str),
            decision.get("outcome_standing_directive_id"),
            json.dumps(decision.get("outcome_superseded_ids", []), default=str),
            decision.get("status", "PENDING_ACK"),
            now,
        ))
        self._conn.commit()

    def update_promotion_status(self, promotion_id: str, status: str) -> None:
        """Update promotion decision status (PENDING_ACK → VERIFIED → ESCALATED)."""
        self._conn.execute(
            "UPDATE promotion_decisions SET status = ? WHERE promotion_id = ?",
            (status, promotion_id),
        )
        self._conn.commit()

    def insert_standing_directive(self, directive: dict) -> None:
        """Add an active standing directive after successful promotion verification."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute("""
            INSERT OR REPLACE INTO standing_directives
            (standing_directive_id, project, promotion_id, verbiage, status, created_at)
            VALUES (?, ?, ?, ?, 'ACTIVE', ?)
        """, (
            directive["standing_directive_id"],
            directive["project"],
            directive["promotion_id"],
            directive["verbiage"],
            now,
        ))
        self._conn.commit()

    def supersede_standing_directive(self, standing_directive_id: str, superseded_by: str) -> None:
        """Mark a standing directive as superseded."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute("""
            UPDATE standing_directives
            SET status = 'SUPERSEDED', superseded_by = ?, superseded_at = ?
            WHERE standing_directive_id = ?
        """, (superseded_by, now, standing_directive_id))
        self._conn.commit()

    def get_active_standing_directives(self, project: str) -> list[dict]:
        """Get all ACTIVE standing directives for a project (for file regeneration)."""
        rows = self._conn.execute(
            "SELECT * FROM standing_directives WHERE project = ? AND status = 'ACTIVE' ORDER BY created_at",
            (project,),
        ).fetchall()
        return [dict(r) for r in rows]

    def query_standing_directives(
        self,
        project: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        """Query standing directives with optional filters."""
        conditions = []
        params: list = []

        if project:
            conditions.append("project = ?")
            params.append(project)
        if status:
            conditions.append("UPPER(status) = UPPER(?)")
            params.append(status)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)

        rows = self._conn.execute(
            f"SELECT * FROM standing_directives {where} ORDER BY created_at DESC LIMIT ?", params
        ).fetchall()
        return [dict(r) for r in rows]

    def query_promotion_decisions(
        self,
        project: str | None = None,
        decision_type: str | None = None,
        status: str | None = None,
        directive_id: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        """Query promotion decisions with optional filters."""
        conditions = []
        params: list = []

        if project:
            conditions.append("project = ?")
            params.append(project)
        if decision_type:
            conditions.append("UPPER(decision_type) = UPPER(?)")
            params.append(decision_type)
        if status:
            conditions.append("UPPER(status) = UPPER(?)")
            params.append(status)
        if directive_id:
            conditions.append("directive_id = ?")
            params.append(directive_id)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)

        rows = self._conn.execute(
            f"SELECT * FROM promotion_decisions {where} ORDER BY timestamp DESC LIMIT ?", params
        ).fetchall()
        return [self._row_to_promotion_dict(r) for r in rows]

    # ── Escalation message methods (conversational resolution) ──

    def insert_escalation_message(
        self, escalation_id: str, author: str, content: str,
    ) -> str:
        """Add a message to an escalation conversation thread. Returns message_id."""
        import uuid
        message_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute("""
            INSERT INTO escalation_messages (message_id, escalation_id, author, content, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (message_id, escalation_id, author, content, now))
        self._conn.commit()
        return message_id

    def create_escalation_with_thread(
        self,
        escalation_id: str,
        escalation_type: str,
        severity: str,
        project: str,
        summary: str,
        *,
        subject_agent: str = "",
        directive_id: str = "",
        promotion_id: str = "",
        finding_ids: list | None = None,
        recommended_action: str = "",
        impact_assessment: str = "",
        metrics: dict | None = None,
        resolution_status: str = "AWAITING_USER",
        timestamp: str | None = None,
        initial_message_author: str = "director",
    ) -> str:
        """Atomically insert an escalation AND seed its conversation thread
        with an initial system-authored message.

        HARDEN-003: Director-published escalations (via create_escalation MCP tool)
        and archiver-published escalations (VERIFICATION_STUCK) must both leave the
        same SQLite shape — escalations row + escalation_messages first entry — so
        downstream queries don't need lazy-create fallbacks.
        """
        ts = timestamp or datetime.now(timezone.utc).isoformat()
        self.archive_escalation(
            stream_id="",
            timestamp=ts,
            payload={
                "escalation_id": escalation_id,
                "escalation_type": escalation_type,
                "severity": severity,
                "project": project,
                "subject_agent": subject_agent,
                "directive_id": directive_id,
                "promotion_id": promotion_id,
                "finding_ids": finding_ids or [],
                "summary": summary,
                "recommended_action": recommended_action,
                "impact_assessment": impact_assessment,
                "metrics": metrics or {},
                "resolution_status": resolution_status,
            },
        )
        self.insert_escalation_message(
            escalation_id=escalation_id,
            author=initial_message_author,
            content=summary,
        )
        self.commit()
        return escalation_id

    def get_escalation_messages(self, escalation_id: str) -> list[dict]:
        """Get all messages in an escalation thread, ordered chronologically."""
        rows = self._conn.execute(
            "SELECT * FROM escalation_messages WHERE escalation_id = ? ORDER BY timestamp ASC",
            (escalation_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_escalation_status(
        self, escalation_id: str, status: str,
    ) -> None:
        """Update escalation resolution status (AWAITING_USER → DISMISSED → RESOLVED)."""
        now = datetime.now(timezone.utc).isoformat()
        updates = "resolution_status = ?"
        params: list = [status]
        if status in ("DISMISSED", "RESOLVED"):
            updates += ", resolution_timestamp = ?"
            params.append(now)
        params.append(escalation_id)
        self._conn.execute(
            f"UPDATE escalations SET {updates} WHERE escalation_id = ?", params
        )
        self._conn.commit()

    def query_escalation_history(
        self,
        project: str | None = None,
        escalation_type: str | None = None,
        resolution_status: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        """Query escalations with resolution lifecycle filters."""
        conditions = []
        params: list = []

        if project:
            conditions.append("project = ?")
            params.append(project)
        if escalation_type:
            conditions.append("UPPER(escalation_type) = UPPER(?)")
            params.append(escalation_type)
        if resolution_status:
            conditions.append("UPPER(resolution_status) = UPPER(?)")
            params.append(resolution_status)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)

        rows = self._conn.execute(
            f"SELECT * FROM escalations {where} ORDER BY timestamp DESC LIMIT ?", params
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def _row_to_promotion_dict(self, row: sqlite3.Row) -> dict:
        """Convert a promotion_decisions row, parsing JSON fields."""
        d = dict(row)
        for field in ("target_agents", "standing_file_snapshot", "conflict_candidates",
                       "inputs", "outcome_superseded_ids"):
            if d.get(field):
                try:
                    d[field] = json.loads(d[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        return d

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        """Convert a sqlite3.Row to a dict, parsing the payload JSON."""
        d = dict(row)
        if "payload" in d and d["payload"]:
            try:
                d["payload"] = json.loads(d["payload"])
            except (json.JSONDecodeError, TypeError):
                pass
        return d

    def close(self) -> None:
        self._conn.close()
