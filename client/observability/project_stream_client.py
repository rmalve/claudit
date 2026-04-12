"""
Project-side Redis Streams client for receiving audit directives
and sending compliance responses.

This client is used by external projects' hooks to communicate with
the Audit Director. It has strictly scoped access:
  - READ from directives:{project}
  - WRITE to compliance:{project}

Usage:
    client = ProjectStreamClient(project="my-project")

    # Read pending directives
    directives = client.read_directives(count=10)

    # Send compliance acknowledgment
    client.send_compliance(
        directive_id="DIRECTIVE-2026-04-05-001",
        agent="architect",
        action_taken="Implemented rationale logging for /src/core/ modifications",
    )
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone

import redis

from observability.messages import (
    CompliancePayload,
    MessageEnvelope,
    MessageType,
    PromotionAckPayload,
    build_message,
    project_compliance_stream,
    project_directive_stream,
    project_promotion_ack_stream,
    project_promotion_stream,
)

logger = logging.getLogger(__name__)


class ProjectStreamClient:
    """Redis Streams client for external projects.

    Scoped to a single project's directive and compliance streams.
    Cannot access any audit-internal streams.
    """

    def __init__(
        self,
        project: str,
        redis_url: str | None = None,
        redis_username: str | None = None,
        redis_password: str | None = None,
    ):
        self.project = project
        self._directive_stream = project_directive_stream(project)
        self._compliance_stream = project_compliance_stream(project)
        self._promotion_stream = project_promotion_stream(project)
        self._promotion_ack_stream = project_promotion_ack_stream(project)

        url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379")
        username = redis_username or os.environ.get(
            "REDIS_USERNAME", f"project-{project}"
        )
        password = redis_password or os.environ.get("REDIS_PASSWORD")

        self._redis = redis.Redis.from_url(
            url,
            username=username,
            password=password,
            decode_responses=True,
        )

        self._consumer_group = f"group:project:{project}"
        self._consumer_name = f"project:{project}"
        self._ensure_consumer_group()

        logger.info(
            "ProjectStreamClient initialized for project: %s", project
        )

    def _ensure_consumer_group(self) -> None:
        """Create consumer groups for the directive and promotion streams."""
        for stream in (self._directive_stream, self._promotion_stream):
            try:
                self._redis.xgroup_create(
                    stream,
                    self._consumer_group,
                    id="0",
                    mkstream=True,
                )
            except redis.ResponseError as e:
                if "BUSYGROUP" not in str(e):
                    raise

    def read_directives(
        self, count: int = 10, block_ms: int | None = None
    ) -> list[dict]:
        """Read pending directives from the Director.

        Returns a list of directive dicts, each containing:
        - directive_id, directive_type, target_agent, content,
          required_action, compliance_due, supersedes, confidence,
          supporting_metrics

        Automatically acknowledges messages after reading.
        """
        results = self._redis.xreadgroup(
            groupname=self._consumer_group,
            consumername=self._consumer_name,
            streams={self._directive_stream: ">"},
            count=count,
            block=block_ms,
        )

        directives = []
        if results:
            for _stream_name, entries in results:
                for stream_id, data in entries:
                    try:
                        envelope = MessageEnvelope.from_stream_dict(data)
                        directives.append(envelope.payload)
                        self._redis.xack(
                            self._directive_stream,
                            self._consumer_group,
                            stream_id,
                        )
                    except Exception as e:
                        logger.error(
                            "Failed to parse directive %s: %s", stream_id, e
                        )

        return directives

    def read_directives_for_agent(
        self, agent: str, count: int = 50
    ) -> list[dict]:
        """Read directives targeting a specific agent.

        Reads all pending directives and filters to those targeting
        the specified agent. Non-matching directives are still acknowledged
        (they're for other agents in the same project).
        """
        all_directives = self.read_directives(count=count)
        return [
            d for d in all_directives
            if d.get("target_agent") == agent
        ]

    def send_compliance(
        self,
        directive_id: str,
        agent: str,
        action_taken: str = "",
        conflict_reason: str | None = None,
        agent_version: str | None = None,
        session_id: str | None = None,
    ) -> str:
        """Send a compliance response for a directive.

        Args:
            directive_id: The ID of the directive being acknowledged
            agent: Which agent is responding
            action_taken: What the agent did to comply
            conflict_reason: If the agent can't comply, why not (triggers escalation)
            agent_version: Current agent definition version
            session_id: Session where compliance occurred

        Returns:
            Redis stream ID of the published message
        """
        payload = CompliancePayload(
            directive_id=directive_id,
            agent=agent,
            agent_version=agent_version,
            session_id=session_id,
            action_taken=action_taken,
            conflict_reason=conflict_reason,
        )

        envelope = build_message(
            stream=self._compliance_stream,
            source=f"project:{self.project}:{agent}",
            target="director",
            message_type=MessageType.STATUS,
            payload=payload,
        )

        stream_id = self._redis.xadd(
            self._compliance_stream,
            envelope.to_stream_dict(),
        )
        logger.info(
            "Compliance sent for directive %s by %s", directive_id, agent
        )
        return stream_id

    def read_promotions(
        self, count: int = 10, block_ms: int | None = None,
    ) -> list[dict]:
        """Read pending promotion instructions from the Director.

        Returns a list of promotion dicts containing:
        - promotion_id, directive_id, decision_type, add_verbiage,
          remove_verbiage, target_file

        Automatically acknowledges messages after reading.
        """
        results = self._redis.xreadgroup(
            groupname=self._consumer_group,
            consumername=self._consumer_name,
            streams={self._promotion_stream: ">"},
            count=count,
            block=block_ms,
        )

        promotions = []
        if results:
            for _stream_name, entries in results:
                for stream_id, data in entries:
                    try:
                        envelope = MessageEnvelope.from_stream_dict(data)
                        promotions.append(envelope.payload)
                        self._redis.xack(
                            self._promotion_stream,
                            self._consumer_group,
                            stream_id,
                        )
                    except Exception as e:
                        logger.error(
                            "Failed to parse promotion %s: %s", stream_id, e
                        )

        return promotions

    def send_promotion_ack(
        self,
        promotion_id: str,
        verbiage_added: str,
        verbiage_removed: str | None = None,
        file_state_hash: str = "",
        warning: str | None = None,
    ) -> str:
        """Send a promotion acknowledgment back to the Director.

        Reports exactly what was written to the standing directives file
        so the Director can verify the verbiage matches intent.

        Returns:
            Redis stream ID of the published message.
        """
        payload = PromotionAckPayload(
            promotion_id=promotion_id,
            verbiage_added=verbiage_added,
            verbiage_removed=verbiage_removed,
            file_state_hash=file_state_hash,
            warning=warning,
        )

        envelope = build_message(
            stream=self._promotion_ack_stream,
            source=f"project:{self.project}",
            target="director",
            message_type=MessageType.PROMOTION_ACK,
            payload=payload,
        )

        stream_id = self._redis.xadd(
            self._promotion_ack_stream,
            envelope.to_stream_dict(),
        )
        logger.info(
            "Promotion ack sent for %s", promotion_id
        )
        return stream_id

    def pending_directive_count(self) -> int:
        """Count unread directives."""
        try:
            info = self._redis.xpending(
                self._directive_stream, self._consumer_group
            )
            return info["pending"] if info else 0
        except redis.ResponseError:
            return 0

    def ping(self) -> bool:
        """Check Redis connectivity."""
        try:
            return self._redis.ping()
        except redis.ConnectionError:
            return False

    def close(self) -> None:
        """Close the Redis connection."""
        self._redis.close()
