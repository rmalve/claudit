"""
JSONL Conversation Parser — extracts structured conversation turns from
Claude Code session transcript files.

Claude Code stores full conversation transcripts at:
  ~/.claude/projects/{project-hash}/{session_id}.jsonl
  ~/.claude/projects/{project-hash}/{session_id}/subagents/agent-{hash}.jsonl

Each entry has a `promptId` field that definitively marks turn boundaries.
This parser uses promptId (propagated through parentUuid chains) to group
entries into conversation turns with user prompts, assistant responses,
tool calls, and thinking blocks.

Usage:
    from observability.jsonl_parser import parse_session_jsonl, find_session_jsonl

    path = find_session_jsonl("abc123-session-id")
    if path:
        conversation = parse_session_jsonl(path)
        for turn in conversation.turns:
            print(f"Turn {turn.turn_index}: {turn.user_text[:80]}")
"""

import glob
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class TurnEvent:
    """A single chronological event within a conversation turn."""
    type: str = ""          # "user_text", "thinking", "assistant_text", "tool_use", "tool_result"
    timestamp: str = ""
    text: str = ""          # content (truncated)
    tool_name: str = ""     # for tool_use events
    tool_id: str = ""       # for tool_use / tool_result linking
    input_summary: str = "" # for tool_use events
    is_error: bool = False  # for tool_result errors
    subagent_type: str = "" # for Agent tool_use events


@dataclass
class ConversationTurn:
    """A single user-prompt-to-response cycle."""
    prompt_id: str = ""
    turn_index: int = 0
    session_id: str = ""
    timestamp_start: str = ""
    timestamp_end: str = ""
    user_text: str = ""
    assistant_texts: list[str] = field(default_factory=list)
    thinking_count: int = 0
    tool_calls: list[dict] = field(default_factory=list)  # [{name, id, input_summary}]
    tool_results: list[dict] = field(default_factory=list)
    subagent_spawns: list[str] = field(default_factory=list)
    entry_count: int = 0
    events: list[TurnEvent] = field(default_factory=list)  # chronological event stream


@dataclass
class SubagentConversation:
    """Parsed subagent transcript linked to parent."""
    agent_id: str = ""
    agent_type: str = ""
    description: str = ""
    parent_session_id: str = ""
    turns: list[ConversationTurn] = field(default_factory=list)


@dataclass
class SessionConversation:
    """Full parsed conversation for a session."""
    session_id: str = ""
    project_hash: str = ""
    turns: list[ConversationTurn] = field(default_factory=list)
    subagents: list[SubagentConversation] = field(default_factory=list)
    total_entries: int = 0
    parse_time_ms: float = 0.0


def _compute_project_hash(project_root: str) -> str:
    """Compute the Claude Code project directory hash from a project root path.

    Replaces \\, /, and : with - to match Claude Code's directory naming.
    E.g. /home/user/my-project -> -home-user-my-project
    """
    # Normalize to forward slashes, strip trailing
    normalized = project_root.replace("\\", "/").rstrip("/")
    # Replace : and / with - (: before / so C:/ becomes C--)
    return normalized.replace(":", "-").replace("/", "-")


def find_session_jsonl(session_id: str, project_root: str | None = None) -> str | None:
    """Locate the JSONL file for a session_id.

    Args:
        session_id: The Claude Code session ID.
        project_root: The project working directory. If provided, computes
                      the project hash directly. Otherwise scans all project dirs.

    Returns:
        Absolute path to the JSONL file, or None if not found.
    """
    claude_dir = Path.home() / ".claude" / "projects"
    if not claude_dir.exists():
        return None

    if project_root:
        project_hash = _compute_project_hash(project_root)
        candidate = claude_dir / project_hash / f"{session_id}.jsonl"
        if candidate.exists():
            return str(candidate)

    # Fallback: scan all project directories
    for jsonl_path in claude_dir.glob(f"*/{session_id}.jsonl"):
        return str(jsonl_path)

    return None


def find_subagent_jsonls(session_id: str, project_root: str | None = None) -> list[tuple[str, dict]]:
    """Find all subagent JSONL files for a session.

    Returns list of (jsonl_path, meta_dict) tuples.
    """
    claude_dir = Path.home() / ".claude" / "projects"
    results = []

    if project_root:
        project_hash = _compute_project_hash(project_root)
        subagent_dir = claude_dir / project_hash / session_id / "subagents"
    else:
        # Scan for the session directory
        subagent_dir = None
        for d in claude_dir.glob(f"*/{session_id}/subagents"):
            subagent_dir = d
            break

    if not subagent_dir or not subagent_dir.exists():
        return results

    for jsonl_path in sorted(subagent_dir.glob("*.jsonl")):
        meta = {}
        meta_path = jsonl_path.with_suffix("").with_suffix(".meta.json")
        if not meta_path.exists():
            # Try alternate naming: agent-{hash}.meta.json
            meta_path = jsonl_path.parent / (jsonl_path.stem + ".meta.json")
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        results.append((str(jsonl_path), meta))

    return results


def _parse_entries(jsonl_path: str) -> tuple[list[dict], list[dict], str]:
    """Stream-read a JSONL file and return all entries, conversation entries, and session_id.

    Returns (all_entries, conversation_entries, session_id).
    all_entries: every entry with a uuid (needed for parentUuid chain resolution).
    conversation_entries: only user/assistant entries (for content extraction).
    """
    all_entries = []
    conversation_entries = []
    session_id = ""

    with open(jsonl_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue

            entry_type = d.get("type", "")

            if not session_id:
                session_id = d.get("sessionId", "")

            # Keep all entries that have uuid/parentUuid for chain resolution
            if d.get("uuid") or d.get("parentUuid") or d.get("promptId"):
                all_entries.append(d)

            if entry_type in ("user", "assistant"):
                conversation_entries.append(d)

    return all_entries, conversation_entries, session_id


def _resolve_prompt_ids(all_entries: list[dict]) -> dict[str, str]:
    """Build uuid -> promptId map by propagating through parentUuid chains.

    Uses ALL entry types (including attachments, system, etc.) so that
    chains like assistant -> attachment -> user resolve correctly.
    Single forward pass — entries are chronologically ordered in JSONL.
    """
    uuid_to_prompt = {}

    for entry in all_entries:
        uuid = entry.get("uuid") or ""
        prompt_id = entry.get("promptId") or ""
        parent_uuid = entry.get("parentUuid") or ""

        if prompt_id:
            uuid_to_prompt[uuid] = prompt_id
        elif parent_uuid in uuid_to_prompt:
            uuid_to_prompt[uuid] = uuid_to_prompt[parent_uuid]

    return uuid_to_prompt


def _group_into_turns(entries: list[dict], uuid_to_prompt: dict[str, str], session_id: str) -> list[ConversationTurn]:
    """Group entries by resolved promptId into conversation turns."""
    turns_by_prompt: dict[str, list[dict]] = {}
    prompt_order: list[str] = []  # preserve first-seen order

    for entry in entries:
        uuid = entry.get("uuid") or ""
        prompt_id = uuid_to_prompt.get(uuid, "")
        if not prompt_id:
            continue

        if prompt_id not in turns_by_prompt:
            turns_by_prompt[prompt_id] = []
            prompt_order.append(prompt_id)
        turns_by_prompt[prompt_id].append(entry)

    # Build ConversationTurn objects
    turns = []
    for i, prompt_id in enumerate(prompt_order):
        turn_entries = turns_by_prompt[prompt_id]
        turn = ConversationTurn(
            prompt_id=prompt_id,
            turn_index=i,
            session_id=session_id,
        )

        timestamps = []
        for entry in turn_entries:
            ts = entry.get("timestamp", "")
            if ts:
                timestamps.append(ts)

            entry_type = entry.get("type", "")
            msg = entry.get("message", {})
            content = msg.get("content", [])
            if not isinstance(content, list):
                continue

            for block in content:
                block_type = block.get("type", "")

                if entry_type == "user" and block_type == "text":
                    text = block.get("text", "")
                    # Prefer actual user input over system-injected context
                    is_system_context = text.startswith("<") and any(
                        text.startswith(tag) for tag in (
                            "<ide_opened_file>", "<ide_selection>",
                            "<system-reminder>", "<user-prompt-submit-hook>",
                        )
                    )
                    if text and not turn.user_text:
                        turn.user_text = text[:2000]
                    if text and not is_system_context:
                        # Override system context with real user input
                        turn.user_text = text[:2000]
                    if text:
                        turn.events.append(TurnEvent(
                            type="user_text", timestamp=ts, text=text[:2000],
                        ))

                elif entry_type == "assistant" and block_type == "text":
                    text = block.get("text", "")
                    if text:
                        turn.assistant_texts.append(text[:2000])
                        turn.events.append(TurnEvent(
                            type="assistant_text", timestamp=ts, text=text[:2000],
                        ))

                elif entry_type == "assistant" and block_type == "thinking":
                    turn.thinking_count += 1
                    thinking_text = block.get("thinking", "")
                    turn.events.append(TurnEvent(
                        type="thinking", timestamp=ts,
                        text=thinking_text[:1000] if thinking_text else "(reasoning)",
                    ))

                elif entry_type == "assistant" and block_type == "tool_use":
                    tool_name = block.get("name", "")
                    tool_id = block.get("id", "")
                    tool_input = block.get("input", {})
                    input_summary = _summarize_tool_input(tool_name, tool_input)
                    turn.tool_calls.append({
                        "name": tool_name,
                        "id": tool_id,
                        "input_summary": input_summary,
                    })
                    subagent = ""
                    if tool_name == "Agent":
                        subagent = tool_input.get("subagent_type", "general")
                        turn.subagent_spawns.append(subagent)
                    turn.events.append(TurnEvent(
                        type="tool_use", timestamp=ts, tool_name=tool_name,
                        tool_id=tool_id, input_summary=input_summary,
                        subagent_type=subagent,
                    ))

                elif entry_type == "user" and block_type == "tool_result":
                    tool_use_id = block.get("tool_use_id", "")
                    result_content = block.get("content", "")
                    is_error = block.get("is_error", False)
                    if isinstance(result_content, list):
                        result_text = " ".join(
                            c.get("text", "")[:300] for c in result_content if c.get("type") == "text"
                        )
                    elif isinstance(result_content, str):
                        result_text = result_content[:300]
                    else:
                        result_text = ""
                    turn.tool_results.append({
                        "tool_use_id": tool_use_id,
                        "content_summary": result_text[:500],
                    })
                    turn.events.append(TurnEvent(
                        type="tool_result", timestamp=ts, text=result_text[:500],
                        tool_id=tool_use_id, is_error=is_error,
                    ))

        turn.entry_count = len(turn_entries)
        if timestamps:
            timestamps.sort()
            turn.timestamp_start = timestamps[0]
            turn.timestamp_end = timestamps[-1]

        turns.append(turn)

    return turns


def _summarize_tool_input(tool_name: str, tool_input: dict) -> str:
    """Build a brief summary of tool input."""
    parts = []
    for key in ("file_path", "command", "pattern", "content", "old_string", "prompt", "description"):
        val = tool_input.get(key)
        if val:
            parts.append(f"{key}: {str(val)[:100]}")
    return " | ".join(parts)[:300] if parts else ""


def parse_session_jsonl(jsonl_path: str) -> SessionConversation:
    """Parse a session JSONL file into structured conversation data.

    Args:
        jsonl_path: Absolute path to the session JSONL file.

    Returns:
        SessionConversation with turns and metadata.
    """
    start_time = time.monotonic()

    all_entries, conversation_entries, session_id = _parse_entries(jsonl_path)
    uuid_to_prompt = _resolve_prompt_ids(all_entries)
    turns = _group_into_turns(conversation_entries, uuid_to_prompt, session_id)

    parse_time = (time.monotonic() - start_time) * 1000

    # Determine project hash from path
    path = Path(jsonl_path)
    project_hash = path.parent.name if path.parent.name != "subagents" else path.parent.parent.parent.name

    conversation = SessionConversation(
        session_id=session_id,
        project_hash=project_hash,
        turns=turns,
        total_entries=len(conversation_entries),
        parse_time_ms=round(parse_time, 1),
    )

    logger.info(
        "Parsed %s: %d entries -> %d turns in %.1fms",
        session_id[:12], len(conversation_entries), len(turns), parse_time,
    )

    return conversation


def parse_subagent_jsonl(jsonl_path: str, meta: dict | None = None) -> SubagentConversation:
    """Parse a subagent JSONL file."""
    all_entries, conversation_entries, session_id = _parse_entries(jsonl_path)
    uuid_to_prompt = _resolve_prompt_ids(all_entries)
    turns = _group_into_turns(conversation_entries, uuid_to_prompt, session_id)

    # Extract agent_id from first entry
    agent_id = ""
    if all_entries:
        agent_id = all_entries[0].get("agentId", "") or ""

    meta = meta or {}

    return SubagentConversation(
        agent_id=agent_id,
        agent_type=meta.get("agentType", ""),
        description=meta.get("description", ""),
        parent_session_id=session_id,
        turns=turns,
    )
