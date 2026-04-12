"""
ObservabilityClient — unified interface for recording and querying agent telemetry.

Combines Prometheus metrics (structured/numeric) with Qdrant (semantic/vector)
into a single API. Project adapters use this client to record events.
"""

import logging
from datetime import datetime, timezone

from observability.metrics import (
    record_tool_call, record_hallucination, record_agent_spawn,
    record_eval, record_session_end, flush_metrics,
)
from observability.qdrant_backend import QdrantBackend
from observability.schemas import _enum_val
from observability.schemas import (
    ToolCallEvent, HallucinationEvent, AgentSpawnEvent,
    EvalResult, SessionSummary, CodeChangeEvent, BugEvent,
)

logger = logging.getLogger(__name__)


class ObservabilityClient:
    """Unified observability client for recording and querying agent telemetry.

    Usage:
        client = ObservabilityClient(project="my-project")
        client.record_tool_call(event)
        client.record_hallucination(event)

        # Query
        similar = client.find_similar_hallucinations("schema mismatch on CaptureResponse")
    """

    def __init__(
        self,
        project: str,
        qdrant_url: str | None = None,
        qdrant_path: str | None = None,
    ):
        self.project = project
        self._qdrant = QdrantBackend(url=qdrant_url, path=qdrant_path)
        self._session_start = datetime.now(timezone.utc)
        self._session_tool_calls: list[ToolCallEvent] = []
        self._session_hallucinations: list[HallucinationEvent] = []
        self._session_agent_spawns: list[AgentSpawnEvent] = []
        self._session_evals: list[EvalResult] = []
        logger.info("ObservabilityClient initialized for project: %s", project)

    # ── Recording ──

    def record_tool_call(self, event: ToolCallEvent) -> None:
        """Record a tool call to both Prometheus and Qdrant."""
        event.project = self.project
        agent = event.agent if isinstance(event.agent, str) else event.agent.value

        # Prometheus
        record_tool_call(
            tool_name=event.tool_name,
            agent=agent,
            status=event.status.value,
            duration_ms=event.duration_ms,
            project=self.project,
        )

        # Qdrant (semantic)
        self._qdrant.add_tool_call(
            text=event.semantic_text(),
            payload=event.qdrant_payload(),
        )

        # Session accumulator
        self._session_tool_calls.append(event)

    def record_hallucination(self, event: HallucinationEvent) -> None:
        """Record a hallucination detection."""
        event.project = self.project
        agent = event.agent if isinstance(event.agent, str) else event.agent.value

        record_hallucination(
            h_type=event.hallucination_type.value,
            agent=agent,
            severity=event.severity,
            project=self.project,
        )

        self._qdrant.add_hallucination(
            text=event.semantic_text(),
            payload=event.qdrant_payload(),
        )

        self._session_hallucinations.append(event)
        logger.warning(
            "Hallucination detected [%s] by %s: %s",
            event.hallucination_type.value, agent, event.claim,
        )

    def record_agent_spawn(self, event: AgentSpawnEvent) -> None:
        """Record a sub-agent launch + store full prompt in prompts collection."""
        event.project = self.project
        parent = _enum_val(event.parent_agent)
        child = _enum_val(event.child_agent)

        record_agent_spawn(parent, child, self.project)

        self._qdrant.add_agent_spawn(
            text=event.semantic_text(),
            payload=event.qdrant_payload(),
        )

        # Store full prompt separately for semantic search
        if event.prompt:
            self._qdrant.add_prompt(
                text=event.prompt,
                payload={
                    "session_id": event.session_id,
                    "agent": child,
                    "parent_agent": parent,
                    "project": self.project,
                    "description": event.description,
                    "timestamp": event.timestamp.isoformat(),
                    "type": "agent_prompt",
                },
            )

        self._session_agent_spawns.append(event)

    def record_eval(self, event: EvalResult) -> None:
        """Record an eval check result."""
        event.project = self.project
        agent = event.agent if isinstance(event.agent, str) else event.agent.value

        record_eval(event.eval_name, agent, event.passed, self.project)

        self._qdrant.add_eval(
            text=event.semantic_text(),
            payload=event.qdrant_payload(),
        )

        self._session_evals.append(event)

    def record_code_change(self, event: CodeChangeEvent) -> None:
        """Record a code change (Write/Edit) at the per-change level."""
        event.project = self.project
        self._qdrant.add_code_change(
            text=event.semantic_text(),
            payload=event.qdrant_payload(),
        )

    def record_bug(self, event: BugEvent) -> None:
        """Record a bug discovery (dev-time or production)."""
        event.project = self.project
        agent = _enum_val(event.agent)

        record_hallucination(
            h_type=f"bug_{event.stage.value}",
            agent=agent,
            severity=event.severity,
            project=self.project,
        )

        self._qdrant.add_bug(
            text=event.semantic_text(),
            payload=event.qdrant_payload(),
        )

        logger.warning(
            "Bug recorded [%s] [%s] by %s: %s",
            event.stage.value, event.severity, agent, event.description,
        )

    # ── Session Lifecycle ──

    def end_session(self, session_id: str) -> SessionSummary:
        """Finalize the session, compute summary, flush metrics."""
        now = datetime.now(timezone.utc)
        duration = (now - self._session_start).total_seconds()

        # Build tool breakdown
        tool_breakdown: dict[str, int] = {}
        agent_breakdown: dict[str, int] = {}
        tool_failures = 0
        files_created = 0
        files_modified = 0
        files_read = 0

        for tc in self._session_tool_calls:
            tool_breakdown[tc.tool_name] = tool_breakdown.get(tc.tool_name, 0) + 1
            agent_name = tc.agent if isinstance(tc.agent, str) else tc.agent.value
            agent_breakdown[agent_name] = agent_breakdown.get(agent_name, 0) + 1
            if tc.status != "success":
                tool_failures += 1
            if tc.tool_name == "Write":
                files_created += 1
            elif tc.tool_name == "Edit":
                files_modified += 1
            elif tc.tool_name == "Read":
                files_read += 1

        # Count eval results
        evals_passed = sum(1 for e in self._session_evals if e.passed)
        evals_failed = sum(1 for e in self._session_evals if not e.passed)

        summary = SessionSummary(
            session_id=session_id,
            project=self.project,
            start_time=self._session_start,
            end_time=now,
            duration_seconds=duration,
            total_tool_calls=len(self._session_tool_calls),
            tool_call_breakdown=tool_breakdown,
            tool_failures=tool_failures,
            agents_spawned=len(self._session_agent_spawns),
            agent_breakdown=agent_breakdown,
            hallucinations_detected=len(self._session_hallucinations),
            evals_passed=evals_passed,
            evals_failed=evals_failed,
            files_created=files_created,
            files_modified=files_modified,
            files_read=files_read,
        )

        # Store in Qdrant
        self._qdrant.add_session(
            text=summary.semantic_text(),
            payload=summary.qdrant_payload(),
        )

        # Flush OTel metrics and traces
        record_session_end(duration, tool_failures, self.project)
        flush_metrics()
        logger.info("Session %s ended. OTel metrics flushed.", session_id)

        return summary

    # ── Querying ──

    def find_similar_hallucinations(
        self, query: str, limit: int = 5, agent: str | None = None,
    ) -> list[dict]:
        """Find past hallucinations similar to a description."""
        return self._qdrant.search_similar_hallucinations(query, limit, agent)

    def find_similar_failures(
        self, error_description: str, limit: int = 5,
    ) -> list[dict]:
        """Find past tool failures similar to an error."""
        return self._qdrant.search_similar_failures(
            error_description, limit, self.project
        )

    def find_similar_sessions(
        self, description: str, limit: int = 5,
    ) -> list[dict]:
        """Find past sessions with similar characteristics."""
        return self._qdrant.search_similar("sessions", description, limit)

    def find_similar_prompts(
        self, query: str, limit: int = 5, agent: str | None = None,
    ) -> list[dict]:
        """Find past prompts semantically similar to a query."""
        return self._qdrant.search_similar_prompts(query, limit, agent, self.project)

    def find_similar_code_changes(
        self, query: str, limit: int = 5,
        file_path: str | None = None, agent: str | None = None,
    ) -> list[dict]:
        """Find past code changes similar to a description."""
        return self._qdrant.search_similar_code_changes(
            query, limit, file_path, agent, self.project
        )

    def find_similar_bugs(
        self, query: str, limit: int = 5,
        stage: str | None = None, agent: str | None = None,
    ) -> list[dict]:
        """Find past bugs similar to a description."""
        return self._qdrant.search_similar_bugs(
            query, limit, stage, agent, self.project
        )

    def get_stats(self) -> dict:
        """Get current telemetry stats."""
        return {
            "session_tool_calls": len(self._session_tool_calls),
            "session_hallucinations": len(self._session_hallucinations),
            "session_agent_spawns": len(self._session_agent_spawns),
            "session_evals": len(self._session_evals),
            "qdrant_tool_calls": self._qdrant.get_collection_count("tool_calls"),
            "qdrant_hallucinations": self._qdrant.get_collection_count("hallucinations"),
            "qdrant_sessions": self._qdrant.get_collection_count("sessions"),
        }

    def close(self) -> None:
        """Flush and close all backends."""
        flush_metrics()
        self._qdrant.close()
