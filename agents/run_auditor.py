#!/usr/bin/env python3
"""
Audit Agent — self-contained auditor process.

Runs a single auditor (trace, safety, policy, hallucination, drift, or cost)
as a ClaudeSDKClient session with MCP tools for QDrant queries and Redis
stream communication.

Designed to be spawned by the orchestrator as a subprocess.
Reads its system prompt from agents/{type}-auditor.md (or drift-detector.md).

Usage:
    python agents/run_auditor.py --type safety --projects my-project,other-project
    python agents/run_auditor.py --type trace --projects my-project --max-turns 50
"""

import argparse
import io
import logging
import os
import sys
from pathlib import Path

import anyio

# Force UTF-8 stdout/stderr on Windows to handle emoji in model output
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, ResultMessage, AssistantMessage, TextBlock
from audit_tools import auditor_server, trace_auditor_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

AGENTS_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL = "claude-sonnet-4-6"

AUDITOR_PROMPT_FILES = {
    "trace": "trace-auditor.md",
    "safety": "safety-auditor.md",
    "policy": "policy-auditor.md",
    "hallucination": "hallucination-auditor.md",
    "drift": "drift-detector.md",
    "cost": "cost-auditor.md",
}

HEARTBEAT_INTERVAL = 30  # seconds


def build_task_prompt(auditor_type: str, projects: list[str]) -> str:
    """Build the auditor's initial task prompt."""
    project_list = ", ".join(projects)

    return f"""You are now online as the {auditor_type.title()} Auditor.

You audit across the following active projects: {project_list}.
When querying QDrant, always filter by the project field to scope your analysis.
Pass filters as a JSON string, e.g.: '{{"project": "{projects[0]}"}}'

Your process:
1. Send a heartbeat: publish to audit:status with message_type "status" and payload containing your auditor type and state "healthy"
2. Read your task assignments: read from audit:tasks (stream "audit:tasks", count 20)
3. For each task assigned to you, perform your audit work using qdrant_query to examine the relevant data
4. Publish findings to audit:findings with message_type "finding"
5. After completing each task, report task completion to audit:status with status_type "task_complete" and the task_id

IMPORTANT: Read tasks ONCE. Process all tasks you receive. After completing all your tasks, report a final status with state "cycle_complete" and exit. Do NOT poll repeatedly for more tasks — the Director assigns all tasks at the start of the cycle. If you receive 0 tasks on your first read, publish a status with state "idle" and exit immediately.

Begin now."""


async def run_auditor(auditor_type: str, projects: list[str], max_turns: int = 50) -> None:
    """Run an auditor agent."""
    logger = logging.getLogger(f"auditor:{auditor_type}")

    prompt_filename = AUDITOR_PROMPT_FILES.get(auditor_type)
    if not prompt_filename:
        logger.critical("Unknown auditor type: %s", auditor_type)
        sys.exit(1)

    prompt_file = AGENTS_DIR / prompt_filename
    if not prompt_file.exists():
        logger.critical("Prompt file not found: %s", prompt_file)
        sys.exit(1)

    system_prompt = prompt_file.read_text(encoding="utf-8")
    task_prompt = build_task_prompt(auditor_type, projects)

    logger.info("Starting %s Auditor", auditor_type.title())
    logger.info("Projects: %s", ", ".join(projects))
    logger.info("Model: %s", DEFAULT_MODEL)
    logger.info("Max turns: %d", max_turns)

    mcp_server = trace_auditor_server if auditor_type == "trace" else auditor_server
    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        mcp_servers={"auditor-tools": mcp_server},
        permission_mode="bypassPermissions",
        max_turns=max_turns,
        model=DEFAULT_MODEL,
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query(task_prompt)
        async for message in client.receive_response():
            if isinstance(message, ResultMessage):
                logger.info(
                    "%s Auditor completed. Stop reason: %s",
                    auditor_type.title(), message.stop_reason,
                )
                if message.result:
                    print(message.result)
            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        logger.info("%s: %s", auditor_type.title(), block.text[:200])


def main():
    parser = argparse.ArgumentParser(description="Run an audit agent")
    parser.add_argument(
        "--type", required=True, choices=list(AUDITOR_PROMPT_FILES.keys()),
        help="Auditor type",
    )
    parser.add_argument(
        "--projects", required=True,
        help="Comma-separated list of active project names",
    )
    parser.add_argument(
        "--max-turns", type=int, default=50,
        help="Maximum conversation turns (default: 50)",
    )
    args = parser.parse_args()

    projects = [p.strip() for p in args.projects.split(",") if p.strip()]
    if not projects:
        logging.critical("No projects specified.")
        sys.exit(1)

    anyio.run(run_auditor, args.type, projects, args.max_turns)


if __name__ == "__main__":
    main()
