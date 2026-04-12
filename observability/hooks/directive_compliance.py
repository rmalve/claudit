#!/usr/bin/env python3
"""
Directive compliance hook — sends acknowledgments back to the Audit Director.

Called by external project agents when they acknowledge or comply with
a directive. Can be invoked explicitly by the agent or triggered by
a PostToolUse hook that detects compliance signals in agent output.

Usage as a standalone script:
    python directive_compliance.py --directive-id "DIRECTIVE-2026-04-05-001" \
        --agent "architect" --action "Implemented rationale logging"

Usage from agent hooks (stdin JSON):
{
    "directive_id": "DIRECTIVE-2026-04-05-001",
    "agent": "architect",
    "action_taken": "Implemented rationale logging for /src/core/ modifications",
    "session_id": "abc123"
}
"""

import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from observability.project_stream_client import ProjectStreamClient
from observability.version_resolver import resolve_version_for_agent

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def parse_stdin() -> dict | None:
    """Read and parse compliance data from stdin."""
    try:
        if not sys.stdin.isatty():
            return json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def send_compliance(
    directive_id: str,
    agent: str,
    action_taken: str = "",
    conflict_reason: str | None = None,
    session_id: str | None = None,
) -> bool:
    """Send a compliance response to the Audit Director.

    Args:
        directive_id: The directive being acknowledged
        agent: Which agent is responding
        action_taken: What was done to comply
        conflict_reason: If non-compliant, why (triggers escalation)
        session_id: Current session ID

    Returns:
        True if successfully sent, False otherwise
    """
    project = os.environ.get("OBSERVABILITY_PROJECT", "")
    agent_version = resolve_version_for_agent(agent)

    try:
        client = ProjectStreamClient(project=project)

        if not client.ping():
            logger.error("Redis unavailable — cannot send compliance")
            return False

        client.send_compliance(
            directive_id=directive_id,
            agent=agent,
            action_taken=action_taken,
            conflict_reason=conflict_reason,
            agent_version=agent_version,
            session_id=session_id,
        )

        client.close()

        if conflict_reason:
            logger.info(
                "Conflict escalated for directive %s by %s: %s",
                directive_id, agent, conflict_reason,
            )
        else:
            logger.info(
                "Compliance sent for directive %s by %s",
                directive_id, agent,
            )

        return True

    except Exception as e:
        logger.error("Compliance hook error: %s", e)
        return False


def main() -> None:
    # Try stdin first (hook mode)
    data = parse_stdin()

    if data:
        send_compliance(
            directive_id=data.get("directive_id", ""),
            agent=data.get("agent", "unknown"),
            action_taken=data.get("action_taken", ""),
            conflict_reason=data.get("conflict_reason"),
            session_id=data.get("session_id"),
        )
        sys.exit(0)

    # CLI mode
    import argparse

    parser = argparse.ArgumentParser(
        description="Send directive compliance to the Audit Director"
    )
    parser.add_argument("--directive-id", required=True, help="Directive ID to acknowledge")
    parser.add_argument("--agent", required=True, help="Agent name acknowledging")
    parser.add_argument("--action", default="", help="Action taken to comply")
    parser.add_argument("--conflict", default=None, help="Conflict reason (triggers escalation)")
    parser.add_argument("--session-id", default=None, help="Current session ID")
    args = parser.parse_args()

    success = send_compliance(
        directive_id=args.directive_id,
        agent=args.agent,
        action_taken=args.action,
        conflict_reason=args.conflict,
        session_id=args.session_id,
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
