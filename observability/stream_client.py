"""
Redis Streams client for audit platform IPC.

Provides a typed, scoped interface for auditors and the Director to
communicate via Redis Streams. Each client instance is configured with
a role (director or auditor:X) and enforces stream access patterns.

Usage:
    # Director
    client = StreamClient.for_director()
    client.publish_task(task_payload, target_auditor="safety")
    messages = client.read_findings(count=10)

    # Auditor
    client = StreamClient.for_auditor("safety")
    client.publish_finding(finding_payload)
    tasks = client.read_tasks(count=5)
"""

import json
import logging
import os
from datetime import datetime, timezone

import redis

from observability.messages import (
    ALL_STREAMS,
    STREAM_DIRECTIVES,
    STREAM_ESCALATIONS,
    STREAM_FINDINGS,
    STREAM_STATUS,
    STREAM_TASKS,
    MessageEnvelope,
    MessageType,
    build_message,
    CompliancePayload,
    DirectivePayload,
    EscalationPayload,
    EscalationResolutionPayload,
    FindingPayload,
    PromotionAckPayload,
    PromotionPayload,
    ReportPayload,
    StatusPayload,
    TaskPayload,
    project_compliance_stream,
    project_directive_stream,
    project_escalation_resolution_stream,
    project_promotion_ack_stream,
    project_promotion_stream,
)

logger = logging.getLogger(__name__)


class StreamClient:
    """Redis Streams client scoped to an audit platform role.

    Enforces the stream access matrix:
    - Auditors: write to findings/status, read from tasks
    - Director: read/write all streams
    """

    def __init__(
        self,
        role: str,
        redis_url: str | None = None,
        redis_username: str | None = None,
        redis_password: str | None = None,
    ):
        self.role = role  # "director" or "auditor:{type}"
        self._is_director = role == "director"

        url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379")
        username = redis_username or os.environ.get("REDIS_USERNAME", role)
        password = redis_password or os.environ.get("REDIS_PASSWORD")

        self._redis = redis.Redis.from_url(
            url,
            username=username,
            password=password,
            decode_responses=True,
        )

        self._consumer_group = f"group:{role}"
        self._consumer_name = role
        self._ensure_consumer_groups()

        logger.info("StreamClient initialized for role: %s", role)

    @classmethod
    def for_director(cls, **kwargs) -> "StreamClient":
        """Create a client with Director permissions."""
        return cls(
            role="director",
            redis_password=kwargs.get("password")
            or os.environ.get("REDIS_DIRECTOR_PASSWORD"),
            **{k: v for k, v in kwargs.items() if k != "password"},
        )

    @classmethod
    def for_auditor(cls, auditor_type: str, **kwargs) -> "StreamClient":
        """Create a client with auditor-scoped permissions."""
        role = f"auditor:{auditor_type}"
        password_env = f"REDIS_AUDITOR_{auditor_type.upper()}_PASSWORD"
        return cls(
            role=role,
            redis_username=f"auditor-{auditor_type}",
            redis_password=kwargs.get("password")
            or os.environ.get(password_env),
            **{k: v for k, v in kwargs.items() if k != "password"},
        )

    def _ensure_consumer_groups(self) -> None:
        """Create consumer groups for streams this role reads from."""
        streams_to_read = self._readable_streams()
        for stream in streams_to_read:
            try:
                self._redis.xgroup_create(
                    stream, self._consumer_group, id="0", mkstream=True
                )
                logger.info("Created consumer group %s on %s", self._consumer_group, stream)
            except redis.ResponseError as e:
                if "BUSYGROUP" in str(e):
                    pass  # group already exists
                else:
                    raise

    def _readable_streams(self) -> list[str]:
        """Streams this role is allowed to read from."""
        if self._is_director:
            return [STREAM_FINDINGS, STREAM_TASKS, STREAM_STATUS, STREAM_DIRECTIVES, STREAM_ESCALATIONS]
        else:
            return [STREAM_TASKS]

    # ── Publishing ──

    def _publish(self, envelope: MessageEnvelope) -> str:
        """Publish a message to its target stream. Returns the Redis stream ID."""
        stream_id = self._redis.xadd(
            envelope.stream,
            envelope.to_stream_dict(),
        )
        logger.debug("Published %s to %s: %s", envelope.message_type.value, envelope.stream, stream_id)
        return stream_id

    def publish_finding(self, payload: FindingPayload, correlation_id: str | None = None) -> str:
        """Auditor publishes a finding to the Director."""
        envelope = build_message(
            stream=STREAM_FINDINGS,
            source=self.role,
            target="director",
            message_type=MessageType.FINDING,
            payload=payload,
            correlation_id=correlation_id,
        )
        return self._publish(envelope)

    def publish_task(self, payload: TaskPayload) -> str:
        """Director assigns a task to an auditor."""
        if not self._is_director:
            raise PermissionError("Only the Director can publish tasks")
        envelope = build_message(
            stream=STREAM_TASKS,
            source=self.role,
            target=f"auditor:{payload.target_auditor}",
            message_type=MessageType.TASK,
            payload=payload,
        )
        return self._publish(envelope)

    def publish_status(self, payload: StatusPayload) -> str:
        """Auditor reports status/health to the Director."""
        envelope = build_message(
            stream=STREAM_STATUS,
            source=self.role,
            target="director",
            message_type=MessageType.STATUS,
            payload=payload,
        )
        return self._publish(envelope)

    def publish_directive(self, payload: DirectivePayload, correlation_id: str | None = None) -> str:
        """Director issues a directive to an external agent."""
        if not self._is_director:
            raise PermissionError("Only the Director can publish directives")
        envelope = build_message(
            stream=STREAM_DIRECTIVES,
            source=self.role,
            target=f"external:{payload.target_agent}",
            message_type=MessageType.DIRECTIVE,
            payload=payload,
            correlation_id=correlation_id,
        )
        return self._publish(envelope)

    def publish_escalation(self, payload: EscalationPayload, correlation_id: str | None = None) -> str:
        """Director escalates to the user."""
        if not self._is_director:
            raise PermissionError("Only the Director can publish escalations")
        envelope = build_message(
            stream=STREAM_ESCALATIONS,
            source=self.role,
            target="user",
            message_type=MessageType.ESCALATION,
            payload=payload,
            correlation_id=correlation_id,
        )
        return self._publish(envelope)

    def publish_report(self, payload: ReportPayload) -> str:
        """Director publishes a session audit report."""
        if not self._is_director:
            raise PermissionError("Only the Director can publish reports")
        envelope = build_message(
            stream=STREAM_ESCALATIONS,  # reports go to the escalations stream (user-facing)
            source=self.role,
            target="user",
            message_type=MessageType.REPORT,
            payload=payload,
        )
        return self._publish(envelope)

    def publish_project_directive(
        self, project: str, payload: DirectivePayload, correlation_id: str | None = None,
    ) -> str:
        """Director publishes a directive to an external project's directive queue."""
        if not self._is_director:
            raise PermissionError("Only the Director can publish project directives")
        stream = project_directive_stream(project)
        envelope = build_message(
            stream=stream,
            source=self.role,
            target=f"project:{project}:{payload.target_agent}",
            message_type=MessageType.DIRECTIVE,
            payload=payload,
            correlation_id=correlation_id,
        )
        return self._publish(envelope)

    def read_project_compliance(
        self, project: str, count: int = 10, block_ms: int | None = None,
    ) -> list[MessageEnvelope]:
        """Director reads compliance responses from an external project."""
        if not self._is_director:
            raise PermissionError("Only the Director can read project compliance")
        stream = project_compliance_stream(project)
        # Ensure consumer group exists for this project stream
        try:
            self._redis.xgroup_create(stream, self._consumer_group, id="0", mkstream=True)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise
        return self._read_stream(stream, count, block_ms)

    def publish_project_promotion(
        self, project: str, payload: PromotionPayload,
    ) -> str:
        """Director publishes promotion instructions to an external project."""
        if not self._is_director:
            raise PermissionError("Only the Director can publish promotions")
        stream = project_promotion_stream(project)
        envelope = build_message(
            stream=stream,
            source=self.role,
            target=f"project:{project}",
            message_type=MessageType.PROMOTION,
            payload=payload,
        )
        return self._publish(envelope)

    def read_project_promotion_ack(
        self, project: str, count: int = 10, block_ms: int | None = None,
    ) -> list[MessageEnvelope]:
        """Director reads promotion acknowledgments from an external project."""
        if not self._is_director:
            raise PermissionError("Only the Director can read promotion acks")
        stream = project_promotion_ack_stream(project)
        try:
            self._redis.xgroup_create(stream, self._consumer_group, id="0", mkstream=True)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise
        return self._read_stream(stream, count, block_ms)

    def read_escalation_resolutions(
        self, project: str, count: int = 10, block_ms: int | None = None,
    ) -> list[MessageEnvelope]:
        """Director reads escalation resolution guidance from the user (dashboard)."""
        if not self._is_director:
            raise PermissionError("Only the Director can read escalation resolutions")
        stream = project_escalation_resolution_stream(project)
        try:
            self._redis.xgroup_create(stream, self._consumer_group, id="0", mkstream=True)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise
        return self._read_stream(stream, count, block_ms)

    def publish_escalation_resolution(
        self, project: str, payload: EscalationResolutionPayload,
    ) -> str:
        """Dashboard publishes escalation resolution guidance for the Director."""
        stream = project_escalation_resolution_stream(project)
        envelope = build_message(
            stream=stream,
            source="dashboard",
            target="director",
            message_type=MessageType.ESCALATION_RESOLUTION,
            payload=payload,
        )
        return self._publish(envelope)

    def publish_heartbeat(self) -> str:
        """Auditor sends a heartbeat to prove liveness."""
        payload = StatusPayload(
            status_type="heartbeat",
            auditor=self.role,
        )
        return self.publish_status(payload)

    # ── Reading ──

    def _read_stream(
        self,
        stream: str,
        count: int = 10,
        block_ms: int | None = None,
    ) -> list[MessageEnvelope]:
        """Read new messages from a stream using consumer groups."""
        results = self._redis.xreadgroup(
            groupname=self._consumer_group,
            consumername=self._consumer_name,
            streams={stream: ">"},
            count=count,
            block=block_ms,
        )

        messages = []
        if results:
            for _stream_name, entries in results:
                for stream_id, data in entries:
                    try:
                        envelope = MessageEnvelope.from_stream_dict(data)
                        messages.append(envelope)
                        # Auto-acknowledge
                        self._redis.xack(stream, self._consumer_group, stream_id)
                    except Exception as e:
                        logger.error("Failed to parse message %s: %s", stream_id, e)

        return messages

    def read_findings(self, count: int = 10, block_ms: int | None = None) -> list[MessageEnvelope]:
        """Director reads findings from auditors."""
        if not self._is_director:
            raise PermissionError("Only the Director can read findings")
        return self._read_stream(STREAM_FINDINGS, count, block_ms)

    def read_tasks(self, count: int = 10, block_ms: int | None = None) -> list[MessageEnvelope]:
        """Auditor reads task assignments from the Director."""
        return self._read_stream(STREAM_TASKS, count, block_ms)

    def read_status(self, count: int = 10, block_ms: int | None = None) -> list[MessageEnvelope]:
        """Director reads status updates from auditors."""
        if not self._is_director:
            raise PermissionError("Only the Director can read status")
        return self._read_stream(STREAM_STATUS, count, block_ms)

    def read_all(self, count: int = 10) -> dict[str, list[MessageEnvelope]]:
        """Director reads from all streams. Returns {stream_name: [messages]}."""
        if not self._is_director:
            raise PermissionError("Only the Director can read all streams")
        result = {}
        for stream in self._readable_streams():
            messages = self._read_stream(stream, count)
            if messages:
                result[stream] = messages
        return result

    # ── Stream Info ──

    def stream_length(self, stream: str) -> int:
        """Get the number of messages in a stream."""
        try:
            return self._redis.xlen(stream)
        except redis.ResponseError:
            return 0

    def stream_info(self) -> dict[str, dict]:
        """Get info about all audit streams."""
        info = {}
        for stream in ALL_STREAMS:
            try:
                info[stream] = {
                    "length": self._redis.xlen(stream),
                    "groups": self._redis.xinfo_groups(stream),
                }
            except redis.ResponseError:
                info[stream] = {"length": 0, "groups": []}
        return info

    def pending_count(self, stream: str) -> int:
        """Count pending (unacknowledged) messages for this consumer group."""
        try:
            info = self._redis.xpending(stream, self._consumer_group)
            return info["pending"] if info else 0
        except redis.ResponseError:
            return 0

    # ── Lifecycle ──

    def ping(self) -> bool:
        """Check Redis connectivity."""
        try:
            return self._redis.ping()
        except redis.ConnectionError:
            return False

    def close(self) -> None:
        """Close the Redis connection."""
        self._redis.close()
