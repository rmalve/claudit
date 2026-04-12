#!/usr/bin/env python3
"""
Session end hook — generates session summary from QDrant data.

Called by Claude Code's Stop hook. Queries QDrant for all tool calls,
agent spawns, and hallucinations recorded during this session, then
produces an aggregated summary stored in QDrant + OTel metrics.

Reads session_id from stdin JSON (same source as post_tool_use),
falling back to CLAUDE_SESSION_ID env var.
"""

import json
import logging
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from observability.client import ObservabilityClient
from observability.qdrant_backend import QdrantBackend
from observability.schemas import SessionSummary
from observability.metrics import record_session_end, flush_metrics

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def parse_stdin() -> dict | None:
    """Read and parse hook input from stdin."""
    try:
        if not sys.stdin.isatty():
            return json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def main() -> None:
    project = os.environ.get("OBSERVABILITY_PROJECT", "")

    # Get session_id from stdin (preferred) or env var (fallback)
    data = parse_stdin()
    if data and data.get("session_id"):
        session_id = data["session_id"]
    else:
        session_id = os.environ.get("CLAUDE_SESSION_ID", str(uuid.uuid4())[:8])

    try:
        # Query QDrant directly for this session's actual data
        qb = QdrantBackend()

        # Find tool calls for this session (scroll for exact count)
        session_filter = {"session_id": session_id}
        tool_call_results = qb.scroll_all(
            "tool_calls", filters=session_filter, limit=5000,
        )

        # Find agent spawns for this session
        spawn_results = qb.scroll_all(
            "agent_spawns", filters=session_filter, limit=500,
        )

        # Find hallucinations for this session
        hallucination_results = qb.scroll_all(
            "hallucinations", filters=session_filter, limit=500,
        )

        # Build tool breakdown and file counts from actual data
        tool_breakdown: dict[str, int] = {}
        agent_breakdown: dict[str, int] = {}
        tool_failures = 0
        files_created = 0
        files_modified = 0
        files_read = 0
        claude_md_reads = 0
        jsonl_self_reads = 0
        earliest_ts = None
        latest_ts = None

        for r in tool_call_results:
            p = r.get("payload", {})
            tool_name = p.get("tool_name", "unknown")
            tool_breakdown[tool_name] = tool_breakdown.get(tool_name, 0) + 1

            agent_name = p.get("agent", "main")
            agent_breakdown[agent_name] = agent_breakdown.get(agent_name, 0) + 1

            if p.get("status") == "failure":
                tool_failures += 1
            if tool_name == "Write":
                files_created += 1
            elif tool_name == "Edit":
                files_modified += 1
            elif tool_name == "Read":
                files_read += 1

            # Context saturation signals
            file_path = p.get("file_path", "")
            if tool_name == "Read" and file_path:
                if file_path.lower().endswith("claude.md"):
                    claude_md_reads += 1
                elif ".jsonl" in file_path.lower() and ".claude" in file_path.lower():
                    jsonl_self_reads += 1

            ts = p.get("timestamp")
            if ts:
                if earliest_ts is None or ts < earliest_ts:
                    earliest_ts = ts
                if latest_ts is None or ts > latest_ts:
                    latest_ts = ts

        # Flag context saturation if thresholds exceeded
        context_saturation = claude_md_reads >= 10 or jsonl_self_reads >= 1

        # Compute duration from earliest to latest tool call
        duration = 0.0
        start_time = datetime.now(timezone.utc)
        if earliest_ts and latest_ts:
            try:
                start_time = datetime.fromisoformat(earliest_ts)
                end_time = datetime.fromisoformat(latest_ts)
                duration = (end_time - start_time).total_seconds()
            except (ValueError, TypeError):
                pass

        # Capture latest git commit SHA for session-level linkage
        latest_commit_sha = None
        try:
            result = subprocess.run(
                ["git", "log", "-1", "--format=%H"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                latest_commit_sha = result.stdout.strip()
        except Exception:
            pass

        summary = SessionSummary(
            session_id=session_id,
            project=project,
            start_time=start_time,
            duration_seconds=duration,
            total_tool_calls=len(tool_call_results),
            tool_call_breakdown=tool_breakdown,
            tool_failures=tool_failures,
            agents_spawned=len(spawn_results),
            agent_breakdown=agent_breakdown,
            hallucinations_detected=len(hallucination_results),
            files_created=files_created,
            files_modified=files_modified,
            files_read=files_read,
            claude_md_reads=claude_md_reads,
            jsonl_self_reads=jsonl_self_reads,
            context_saturation=context_saturation,
            latest_commit_sha=latest_commit_sha,
        )

        # Store in QDrant
        qb.add_session(
            text=summary.semantic_text(),
            payload=summary.qdrant_payload(),
        )

        # Parse session JSONL for conversation turns
        try:
            from observability.jsonl_parser import find_session_jsonl, parse_session_jsonl, find_subagent_jsonls, parse_subagent_jsonl
            from observability.schemas import ConversationTurnEvent
            from datetime import datetime as dt

            jsonl_path = find_session_jsonl(session_id, project_root=os.getcwd())
            if jsonl_path:
                def _serialize_events(turn_obj):
                    return [
                        {k: v for k, v in {
                            "type": e.type, "timestamp": e.timestamp, "text": e.text,
                            "tool_name": e.tool_name, "tool_id": e.tool_id,
                            "input_summary": e.input_summary, "is_error": e.is_error,
                            "subagent_type": e.subagent_type,
                        }.items() if v}
                        for e in turn_obj.events
                    ]

                conversation = parse_session_jsonl(jsonl_path)
                for turn in conversation.turns:
                    turn_event = ConversationTurnEvent(
                        session_id=session_id,
                        prompt_id=turn.prompt_id,
                        turn_index=turn.turn_index,
                        timestamp_start=dt.fromisoformat(turn.timestamp_start) if turn.timestamp_start else dt.now(timezone.utc),
                        timestamp_end=dt.fromisoformat(turn.timestamp_end) if turn.timestamp_end else dt.now(timezone.utc),
                        project=project,
                        user_prompt=turn.user_text[:2000],
                        assistant_response="\n".join(turn.assistant_texts)[:2000],
                        thinking_count=turn.thinking_count,
                        tool_call_count=len(turn.tool_calls),
                        tool_call_names=list(set(tc["name"] for tc in turn.tool_calls)),
                        subagent_spawns=turn.subagent_spawns,
                        entry_count=turn.entry_count,
                        events=_serialize_events(turn),
                    )
                    qb.add_conversation_turn(
                        text=turn_event.semantic_text(),
                        payload=turn_event.qdrant_payload(),
                    )

                # Parse subagent conversations
                sub_files = find_subagent_jsonls(session_id, project_root=os.getcwd())
                for sub_path, meta in sub_files:
                    sub_conv = parse_subagent_jsonl(sub_path, meta)
                    for turn in sub_conv.turns:
                        turn_event = ConversationTurnEvent(
                            session_id=session_id,
                            prompt_id=turn.prompt_id or f"sub-{sub_conv.agent_id[:12]}-{turn.turn_index}",
                            turn_index=turn.turn_index,
                            timestamp_start=dt.fromisoformat(turn.timestamp_start) if turn.timestamp_start else dt.now(timezone.utc),
                            timestamp_end=dt.fromisoformat(turn.timestamp_end) if turn.timestamp_end else dt.now(timezone.utc),
                            project=project,
                            user_prompt=turn.user_text[:2000],
                            assistant_response="\n".join(turn.assistant_texts)[:2000],
                            thinking_count=turn.thinking_count,
                            tool_call_count=len(turn.tool_calls),
                            tool_call_names=list(set(tc["name"] for tc in turn.tool_calls)),
                            subagent_spawns=turn.subagent_spawns,
                            entry_count=turn.entry_count,
                            events=_serialize_events(turn),
                            is_subagent=True,
                            parent_session_id=session_id,
                            agent_type=sub_conv.agent_type or sub_conv.description[:100],
                        )
                        qb.add_conversation_turn(
                            text=turn_event.semantic_text(),
                            payload=turn_event.qdrant_payload(),
                        )

                logger.info("Parsed JSONL: %d turns, %d subagents", len(conversation.turns), len(sub_files))
            else:
                logger.debug("No JSONL file found for session %s", session_id)
        except Exception as e:
            logger.warning("JSONL parsing failed (non-fatal): %s", e)

        # Flush OTel metrics
        record_session_end(duration, tool_failures, project)
        flush_metrics()

        qb.close()

        logger.info("Session summary for %s:", session_id)
        logger.info("  Duration: %.0fs", summary.duration_seconds)
        logger.info("  Tool calls: %d (%d failures)", summary.total_tool_calls, summary.tool_failures)
        logger.info("  Agents spawned: %d", summary.agents_spawned)
        logger.info("  Hallucinations: %d", summary.hallucinations_detected)
        logger.info("  Files: %d created, %d modified, %d read",
                     summary.files_created, summary.files_modified, summary.files_read)

        # Output for Claude Code hook system
        output = {
            "hookSpecificOutput": {
                "hookEventName": "Stop",
                "additionalContext": (
                    f"Session telemetry: {summary.total_tool_calls} tool calls, "
                    f"{summary.tool_failures} failures, "
                    f"{summary.hallucinations_detected} hallucinations, "
                    f"{summary.agents_spawned} agents spawned. "
                    f"Duration: {summary.duration_seconds:.0f}s."
                ),
            }
        }
        print(json.dumps(output))

    except Exception as e:
        logger.error("Session end hook error: %s", e)
        sys.exit(0)  # Never block Claude Code


if __name__ == "__main__":
    main()
