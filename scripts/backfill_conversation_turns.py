#!/usr/bin/env python3
"""
Backfill conversation_turns from existing JSONL files.

Parses all session JSONL files found under ~/.claude/projects/ and
stores structured conversation turns in the conversation_turns QDrant
collection. Idempotent — deterministic IDs mean re-running is safe.

Usage:
    python scripts/backfill_conversation_turns.py
    python scripts/backfill_conversation_turns.py --project-root /path/to/your/project
    python scripts/backfill_conversation_turns.py --session-id a5cf9a43-cc8b-4d8d-b13b-80fdcf3f9936
"""

import argparse
import glob
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from observability.jsonl_parser import (
    parse_session_jsonl, parse_subagent_jsonl,
    find_subagent_jsonls, _compute_project_hash,
)
from observability.schemas import ConversationTurnEvent
from observability.qdrant_backend import QdrantBackend

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("backfill")


def backfill_session(qb: QdrantBackend, jsonl_path: str, project: str = "") -> int:
    """Parse a single session JSONL and store turns in QDrant. Returns turn count."""
    conversation = parse_session_jsonl(jsonl_path)
    if not conversation.turns:
        return 0

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

    session_id = conversation.session_id
    count = 0

    for turn in conversation.turns:
        turn_event = ConversationTurnEvent(
            session_id=session_id,
            prompt_id=turn.prompt_id,
            turn_index=turn.turn_index,
            timestamp_start=datetime.fromisoformat(turn.timestamp_start) if turn.timestamp_start else datetime.now(timezone.utc),
            timestamp_end=datetime.fromisoformat(turn.timestamp_end) if turn.timestamp_end else datetime.now(timezone.utc),
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
        count += 1

    # Parse subagents
    session_dir = Path(jsonl_path).stem
    parent_dir = Path(jsonl_path).parent
    subagent_dir = parent_dir / session_dir / "subagents"
    if subagent_dir.exists():
        for sub_jsonl in sorted(subagent_dir.glob("*.jsonl")):
            meta_path = sub_jsonl.with_suffix("").with_suffix(".meta.json")
            meta = {}
            if meta_path.exists():
                import json
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    pass

            sub_conv = parse_subagent_jsonl(str(sub_jsonl), meta)
            for turn in sub_conv.turns:
                turn_event = ConversationTurnEvent(
                    session_id=session_id,
                    prompt_id=turn.prompt_id or f"sub-{sub_conv.agent_id[:12]}-{turn.turn_index}",
                    turn_index=turn.turn_index,
                    timestamp_start=datetime.fromisoformat(turn.timestamp_start) if turn.timestamp_start else datetime.now(timezone.utc),
                    timestamp_end=datetime.fromisoformat(turn.timestamp_end) if turn.timestamp_end else datetime.now(timezone.utc),
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
                count += 1

    return count


def main():
    parser = argparse.ArgumentParser(description="Backfill conversation_turns from JSONL files")
    parser.add_argument("--project-root", help="Specific project root to backfill (e.g. /path/to/your/project)")
    parser.add_argument("--session-id", help="Specific session ID to backfill")
    parser.add_argument("--project-name", default="", help="Project name for QDrant payload (e.g. 'my-project')")
    args = parser.parse_args()

    qb = QdrantBackend()
    claude_dir = Path.home() / ".claude" / "projects"

    if args.session_id and args.project_root:
        project_hash = _compute_project_hash(args.project_root)
        jsonl_path = claude_dir / project_hash / f"{args.session_id}.jsonl"
        if jsonl_path.exists():
            count = backfill_session(qb, str(jsonl_path), args.project_name)
            logger.info("Backfilled session %s: %d turns", args.session_id[:12], count)
        else:
            logger.error("JSONL not found: %s", jsonl_path)
    elif args.project_root:
        project_hash = _compute_project_hash(args.project_root)
        project_dir = claude_dir / project_hash
        if not project_dir.exists():
            logger.error("Project dir not found: %s", project_dir)
            return
        jsonl_files = sorted(project_dir.glob("*.jsonl"))
        logger.info("Found %d session files in %s", len(jsonl_files), project_dir)
        total = 0
        for jsonl_path in jsonl_files:
            count = backfill_session(qb, str(jsonl_path), args.project_name)
            total += count
            logger.info("  %s: %d turns", jsonl_path.stem[:12], count)
        logger.info("Total: %d turns backfilled", total)
    else:
        # Scan all project directories
        if not claude_dir.exists():
            logger.error("No .claude/projects directory found")
            return
        total = 0
        for project_dir in sorted(claude_dir.iterdir()):
            if not project_dir.is_dir():
                continue
            jsonl_files = list(project_dir.glob("*.jsonl"))
            if not jsonl_files:
                continue
            logger.info("Project: %s (%d sessions)", project_dir.name, len(jsonl_files))
            for jsonl_path in sorted(jsonl_files):
                count = backfill_session(qb, str(jsonl_path), args.project_name)
                total += count
                if count:
                    logger.info("  %s: %d turns", jsonl_path.stem[:12], count)
        logger.info("Total: %d turns backfilled across all projects", total)


if __name__ == "__main__":
    main()
