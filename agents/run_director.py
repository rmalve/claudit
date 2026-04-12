#!/usr/bin/env python3
"""
Audit Director — self-contained agent process.

Runs the Audit Director as a ClaudeSDKClient session with MCP tools
for QDrant queries, Redis stream communication, and file reading.

Supports two modes:
  --mode assign    Phase 1: Query data, assign tasks to auditors, then exit.
  --mode synthesize Phase 2: Read findings, cross-check, issue directives, write report.

Designed to be spawned by the orchestrator as a subprocess.
Reads its system prompt from agents/audit-director.md.

Usage:
    python agents/run_director.py --projects my-project --mode assign
    python agents/run_director.py --projects my-project --mode synthesize
"""

import argparse
import io
import json
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
from audit_tools import director_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("director")

AGENTS_DIR = Path(__file__).resolve().parent
PROMPT_FILE = AGENTS_DIR / "audit-director.md"
DEFAULT_MODEL = "claude-opus-4-6"


def build_assign_prompt(projects: list[str]) -> str:
    """Build the Director's task assignment prompt (Phase 1)."""
    project_list = "\n".join(f"  - {p}" for p in projects)
    project_names = projects

    return f"""You are now online as the Audit Director — ASSIGNMENT PHASE.

You are responsible for auditing the following active projects:
{project_list}

Your audit team consists of 6 auditors: trace, safety, policy, hallucination, drift, cost.

Your job in this phase is ONLY to assign tasks. Do NOT read findings or write reports.

1. Verify connectivity to QDrant by querying the tool_calls collection (use qdrant_query tool)
2. For each active project, query recent sessions and tool_calls filtered by project field to understand what needs auditing
3. Assign audit_session tasks to each auditor by publishing to audit:tasks (use stream_publish tool)
   - Each task should target a specific auditor and include the session_id, project, and instructions
   - Assign tasks to ALL 6 auditors: trace, safety, policy, hallucination, drift, cost
4. Check for escalation resolutions from the user via read_escalation_resolutions for each project: {', '.join(p for p in project_names)}
5. Read compliance responses from each project: {', '.join('compliance:' + p for p in project_names)}

After assigning all tasks and checking for resolutions, you are DONE. Exit immediately.

For QDrant queries, pass filters as a JSON string, e.g.: '{{"project": "my-project"}}'
For stream publishing, pass the payload as a JSON string.

Begin now."""


def build_synthesize_prompt(projects: list[str]) -> str:
    """Build the Director's synthesis prompt (Phase 2)."""
    project_names = projects

    return f"""You are now online as the Audit Director — SYNTHESIS PHASE.

All auditors have completed their work. Your job is to read their findings, cross-check, issue directives, and write your report.

Active projects: {', '.join(project_names)}

1. Read ALL findings from audit:findings (use stream_read tool with count=100). Read until no more messages are returned — auditors may have published multiple findings each.
2. Cross-check findings across auditors. Where the same session has findings from 2+ auditors, produce a cross-audit synthesis finding (finding_type: "info", auditor_type: "director") and publish it to audit:findings.
3. Issue directives to project queues as needed: {', '.join('directives:' + p for p in project_names)}
   Every directive MUST include: a descriptive title in the content field, confidence score (0.0-1.0), the finding_id that triggered it, a risk/impact assessment in supporting_metrics, and the specific required_action for compliance.
4. Produce your session report and publish to audit:reports. This is the LAST thing you do.
   The report is your primary deliverable — write it as a polished document.

For QDrant queries, pass filters as a JSON string, e.g.: '{{"project": "my-project"}}'
For stream publishing, pass the payload as a JSON string.

Begin now."""


async def run_director(projects: list[str], mode: str, max_turns: int = 200) -> None:
    """Run the Director agent in the specified mode."""
    system_prompt = PROMPT_FILE.read_text(encoding="utf-8")

    if mode == "assign":
        task_prompt = build_assign_prompt(projects)
        turns = min(max_turns, 100)  # Assignment shouldn't need many turns
    else:
        task_prompt = build_synthesize_prompt(projects)
        turns = max_turns

    logger.info("Starting Audit Director (%s mode)", mode)
    logger.info("Projects: %s", ", ".join(projects))
    logger.info("Model: %s", DEFAULT_MODEL)
    logger.info("Max turns: %d", turns)

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        mcp_servers={"director-tools": director_server},
        permission_mode="bypassPermissions",
        max_turns=turns,
        model=DEFAULT_MODEL,
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query(task_prompt)
        async for message in client.receive_response():
            if isinstance(message, ResultMessage):
                logger.info(
                    "Director (%s) completed. Stop reason: %s",
                    mode, message.stop_reason,
                )
                if message.result:
                    print(message.result)
            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        logger.info("Director: %s", block.text[:200])


def main():
    parser = argparse.ArgumentParser(description="Run the Audit Director")
    parser.add_argument(
        "--projects", required=True,
        help="Comma-separated list of active project names",
    )
    parser.add_argument(
        "--mode", choices=["assign", "synthesize"], default="assign",
        help="Director mode: 'assign' (Phase 1) or 'synthesize' (Phase 2)",
    )
    parser.add_argument(
        "--max-turns", type=int, default=200,
        help="Maximum conversation turns (default: 200)",
    )
    args = parser.parse_args()

    projects = [p.strip() for p in args.projects.split(",") if p.strip()]
    if not projects:
        logger.critical("No projects specified.")
        sys.exit(1)

    anyio.run(run_director, projects, args.mode, args.max_turns)


if __name__ == "__main__":
    main()
