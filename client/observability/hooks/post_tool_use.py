#!/usr/bin/env python3
"""
PostToolUse telemetry hook — captures every tool call for observability.

Called by Claude Code's PostToolUse hook. Reads tool call data from stdin,
records to Prometheus metrics + Qdrant vector store.

Stdin JSON format (from Claude Code):
{
    "session_id": "abc123",
    "tool_name": "Edit",
    "tool_input": {"file_path": "/path/to/file", ...},
    "tool_response": {"filePath": "/path/to/file", ...}
}
"""

import json
import logging
import os
import subprocess
import sys
import time

# Add parent packages to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))


from observability.schemas import (
    ToolCallEvent, ToolCallStatus, AgentSpawnEvent,
    CodeChangeEvent, ChangeOperation,
)
from observability.client import ObservabilityClient
from observability.version_resolver import (
    resolve_agent_name, resolve_version_for_agent,
    resolve_version_path_for_agent, resolve_all_versions_json,
)
from observability.validation import validate_event, DataQualityEvent
from observability.hallucination_detector import HallucinationDetector

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def parse_stdin() -> dict | None:
    """Read and parse hook input from stdin."""
    try:
        if not sys.stdin.isatty():
            return json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def extract_file_path(data: dict) -> str | None:
    """Extract the target file path from tool input/response."""
    return (
        data.get("tool_input", {}).get("file_path")
        or data.get("tool_response", {}).get("filePath")
    )


def extract_command(data: dict) -> str | None:
    """Extract bash command if this is a Bash tool call."""
    return data.get("tool_input", {}).get("command")


def build_input_summary(data: dict) -> str:
    """Build a truncated summary of tool input for semantic search."""
    tool_input = data.get("tool_input", {})
    parts = []
    for key in ("file_path", "command", "pattern", "content", "old_string", "prompt"):
        val = tool_input.get(key)
        if val:
            parts.append(f"{key}: {str(val)[:150]}")
    return " | ".join(parts)[:500]


def build_output_summary(data: dict) -> str | None:
    """Build a truncated summary of tool output for semantic search."""
    tool_response = data.get("tool_response")
    if tool_response is None:
        return None

    if isinstance(tool_response, str):
        return tool_response[:500] if tool_response else None

    if isinstance(tool_response, dict):
        parts = []
        # Capture key output fields depending on tool type
        for key in ("output", "filePath", "content", "error", "result", "matches"):
            val = tool_response.get(key)
            if val:
                parts.append(f"{key}: {str(val)[:150]}")
        return " | ".join(parts)[:500] if parts else None

    return str(tool_response)[:500]


def main() -> None:
    data = parse_stdin()
    if not data:
        sys.exit(0)

    tool_name = data.get("tool_name", "unknown")
    session_id = data.get("session_id", "unknown")
    hook_agent_type = data.get("agent_type")  # Claude Code provides for sub-agents
    project = os.environ.get("OBSERVABILITY_PROJECT", "")

    # Set PROJECT_ROOT from hook cwd if not already set, so version resolution
    # finds the external project's .claude/agents/ rather than the observability repo's
    if not os.environ.get("PROJECT_ROOT") and data.get("cwd"):
        os.environ["PROJECT_ROOT"] = data["cwd"]

    # Skip if tool call is from observability itself (prevent recursion)
    file_path = extract_file_path(data)
    if file_path and "observability" in str(file_path):
        sys.exit(0)

    # Determine status from tool_response
    tool_response = data.get("tool_response", {})
    status = ToolCallStatus.SUCCESS
    error_msg = None
    if isinstance(tool_response, dict):
        if tool_response.get("error"):
            status = ToolCallStatus.FAILURE
            error_msg = str(tool_response.get("error", ""))[:500]
    elif isinstance(tool_response, str) and "error" in tool_response.lower():
        status = ToolCallStatus.FAILURE
        error_msg = tool_response[:500]

    # Resolve agent version + path from versioning system
    # Priority: hook stdin agent_type > AGENT_NAME env var > "main"
    agent_name = resolve_agent_name(hook_agent_type)
    agent_version = resolve_version_for_agent(agent_name)
    agent_version_path = resolve_version_path_for_agent(agent_name)
    if agent_version is None and agent_name == "main":
        # No "main" agent found — store full version map as fallback
        agent_version = resolve_all_versions_json()

    # Build event
    event = ToolCallEvent(
        session_id=session_id,
        tool_name=tool_name,
        file_path=file_path,
        command=extract_command(data),
        status=status,
        error_message=error_msg,
        agent_version=agent_version,
        agent_version_path=agent_version_path,
        input_summary=build_input_summary(data),
        output_summary=build_output_summary(data),
        project=project,
    )

    # Record
    try:
        client = ObservabilityClient(project=project)
        client.record_tool_call(event)

        # Validate tool call event and record any data quality issues
        tc_validation = validate_event(event, "tool_call")
        if tc_validation.total_issues > 0:
            dq_event = DataQualityEvent.from_validation_result(
                tc_validation,
                session_id=session_id,
                agent=str(event.agent),
                agent_version=agent_version,
                project=project,
            )
            client._qdrant.add_data_quality_event(
                text=dq_event.semantic_text(),
                payload=dq_event.qdrant_payload(),
            )

        # If this was an Agent tool call, also record the spawn with full prompt + versions
        if tool_name == "Agent":
            tool_input = data.get("tool_input", {})
            child_agent_name = tool_input.get("subagent_type", "general")
            child_version = resolve_version_for_agent(child_agent_name)
            child_version_path = resolve_version_path_for_agent(child_agent_name)
            spawn = AgentSpawnEvent(
                session_id=session_id,
                description=tool_input.get("description", ""),
                child_agent=child_agent_name,
                child_agent_version=child_version,
                child_agent_version_path=child_version_path,
                prompt=tool_input.get("prompt", ""),
                project=project,
            )
            client.record_agent_spawn(spawn)

            # Validate spawn event
            spawn_validation = validate_event(spawn, "agent_spawn")
            if spawn_validation.total_issues > 0:
                dq_event = DataQualityEvent.from_validation_result(
                    spawn_validation,
                    session_id=session_id,
                    agent=child_agent_name,
                    agent_version=child_version,
                    project=project,
                )
                client._qdrant.add_data_quality_event(
                    text=dq_event.semantic_text(),
                    payload=dq_event.qdrant_payload(),
                )

        # If this was a Write or Edit, capture the code change at per-change granularity
        if tool_name in ("Write", "Edit"):
            tool_input = data.get("tool_input", {})
            operation = ChangeOperation.WRITE if tool_name == "Write" else ChangeOperation.EDIT

            # Extract diff content
            if tool_name == "Edit":
                old_content = tool_input.get("old_string", "")
                new_content = tool_input.get("new_string", "")
                diff_summary = f"Edit: replaced '{old_content[:100]}' with '{new_content[:100]}'"
            else:
                old_content = None
                content = tool_input.get("content", "")
                new_content = content[:2000] if content else None
                diff_summary = f"Write: created file with {len(content)} chars"

            # Capture latest git commit SHA for change-level linkage
            commit_sha = None
            try:
                git_result = subprocess.run(
                    ["git", "log", "-1", "--format=%H"],
                    capture_output=True, text=True, timeout=5,
                )
                if git_result.returncode == 0 and git_result.stdout.strip():
                    commit_sha = git_result.stdout.strip()
            except Exception:
                pass

            change = CodeChangeEvent(
                session_id=session_id,
                agent_version=agent_version,
                file_path=file_path or tool_input.get("file_path", "unknown"),
                operation=operation,
                old_content=old_content[:2000] if old_content else None,
                new_content=new_content,
                diff_summary=diff_summary,
                commit_sha=commit_sha,
                project=project,
            )
            client.record_code_change(change)

            # Validate code change event
            cc_validation = validate_event(change, "code_change")
            if cc_validation.total_issues > 0:
                dq_event = DataQualityEvent.from_validation_result(
                    cc_validation,
                    session_id=session_id,
                    agent=str(event.agent),
                    agent_version=agent_version,
                    project=project,
                )
                client._qdrant.add_data_quality_event(
                    text=dq_event.semantic_text(),
                    payload=dq_event.qdrant_payload(),
                )

        # Run hallucination detection on tool response
        if tool_response and isinstance(tool_response, (str, dict)):
            response_text = tool_response if isinstance(tool_response, str) else str(tool_response.get("output", ""))
            if response_text and len(response_text) > 20:
                try:
                    detector = HallucinationDetector(project_root=os.environ.get("PROJECT_ROOT"))
                    result = detector.check_text(response_text, session_id=session_id, agent=agent_name)
                    for h in result.hallucinations:
                        client.record_hallucination(h)
                except Exception:
                    pass

        client.close()
    except Exception as e:
        # Never block Claude Code — observability is best-effort
        logging.error("Telemetry hook error: %s", e)

    sys.exit(0)


if __name__ == "__main__":
    main()
