#!/usr/bin/env python3
"""
Test runner hook — runs pytest, produces EvalResults and BugEvents.

Runs after every Write/Edit to a .py file. Captures:
- EvalResult on EVERY run (pass or fail) — feeds Drift Detector baselines
- BugEvent on failure — links to the specific code change that caused it

Stdin JSON format (from Claude Code PostToolUse):
{
    "tool_name": "Edit",
    "tool_input": {"file_path": "api/main.py", ...},
    "session_id": "abc123"
}
"""

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from observability.client import ObservabilityClient
from observability.schemas import (
    BugEvent, BugStage, BugDiscoveredBy,
    EvalResult,
)
from observability.version_resolver import resolve_agent_name, resolve_version_for_agent

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

CHANGE_TRACKER_DIR = Path(tempfile.gettempdir()) / "llm-obs"
PROJECT_ROOT = os.environ.get("PROJECT_ROOT", str(Path(__file__).resolve().parent.parent.parent))


def _get_change_tracker_path(session_id: str) -> Path:
    CHANGE_TRACKER_DIR.mkdir(parents=True, exist_ok=True)
    return CHANGE_TRACKER_DIR / f"last_change_{session_id}.json"


def save_last_change(session_id: str, change_id: str, file_path: str) -> None:
    tracker = _get_change_tracker_path(session_id)
    data = {
        "change_id": change_id,
        "file_path": file_path,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    tracker.write_text(json.dumps(data), encoding="utf-8")


def load_last_change(session_id: str) -> dict | None:
    tracker = _get_change_tracker_path(session_id)
    if tracker.exists():
        try:
            return json.loads(tracker.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def parse_stdin() -> dict | None:
    try:
        if not sys.stdin.isatty():
            return json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def run_lint(file_path: str) -> tuple[bool, str, int]:
    """Run linter on a single file. Returns (passed, output, issues_found).

    Tries ruff first, then flake8. Returns (True, "no_linter", 0) if neither available.
    """
    # Try ruff
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", file_path, "--output-format=concise"],
            capture_output=True, text=True, timeout=30,
            cwd=PROJECT_ROOT,
        )
        output = result.stdout + result.stderr
        issues = len([l for l in output.splitlines() if l.strip() and ":" in l and not l.startswith("All checks")])
        return result.returncode == 0, output, issues
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Try flake8
    try:
        result = subprocess.run(
            [sys.executable, "-m", "flake8", file_path, "--max-line-length=120"],
            capture_output=True, text=True, timeout=30,
            cwd=PROJECT_ROOT,
        )
        output = result.stdout + result.stderr
        issues = len([l for l in output.splitlines() if l.strip()])
        return result.returncode == 0, output, issues
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # No linter available
    return True, "no_linter_available", 0


def run_pytest() -> tuple[bool, str, int, int, int]:
    """Run pytest and return (passed, output, tests_run, tests_passed, tests_failed)."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "--tb=short", "-q"],
        capture_output=True, text=True, timeout=120,
        cwd=PROJECT_ROOT,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "OTEL_EXPORT_MODE": "none", "QDRANT_URL": ""},
    )

    output = result.stdout + result.stderr
    passed = result.returncode == 0

    tests_passed = 0
    tests_failed = 0
    for line in output.splitlines():
        if "passed" in line or "failed" in line:
            passed_match = re.search(r"(\d+) passed", line)
            failed_match = re.search(r"(\d+) failed", line)
            if passed_match:
                tests_passed = int(passed_match.group(1))
            if failed_match:
                tests_failed = int(failed_match.group(1))

    tests_run = tests_passed + tests_failed
    return passed, output, tests_run, tests_passed, tests_failed


def main() -> None:
    data = parse_stdin()
    if not data:
        sys.exit(0)

    file_path = (
        data.get("tool_input", {}).get("file_path")
        or data.get("tool_response", {}).get("filePath", "")
    )

    if not file_path.endswith(".py"):
        sys.exit(0)

    session_id = data.get("session_id", "unknown")
    hook_agent_type = data.get("agent_type")  # Claude Code provides for sub-agents
    project = os.environ.get("OBSERVABILITY_PROJECT", "")
    agent_name = resolve_agent_name(hook_agent_type)
    agent_version = resolve_version_for_agent(agent_name)

    change_id = str(uuid.uuid5(
        uuid.NAMESPACE_DNS,
        f"{session_id}:{file_path}:{datetime.now(timezone.utc).isoformat()}"
    ))
    save_last_change(session_id, change_id, file_path)

    # Run tests
    all_passed, output, tests_run, tests_passed, tests_failed = run_pytest()

    try:
        client = ObservabilityClient(project=project)

        # Always produce an EvalResult — pass or fail
        pass_rate = tests_passed / tests_run if tests_run > 0 else 1.0

        eval_test = EvalResult(
            session_id=session_id,
            eval_name="test_pass_rate",
            agent_version=agent_version,
            project=project,
            passed=all_passed,
            score=pass_rate,
            details=f"{tests_passed}/{tests_run} tests passed after editing {file_path}",
            metadata={
                "tests_run": tests_run,
                "tests_passed": tests_passed,
                "tests_failed": tests_failed,
                "trigger_file": file_path,
                "change_id": change_id,
            },
        )
        client.record_eval(eval_test)

        # Run lint check on the changed file
        lint_passed, lint_output, lint_issues = run_lint(file_path)

        if lint_output != "no_linter_available":
            eval_lint = EvalResult(
                session_id=session_id,
                eval_name="lint_check",
                agent_version=agent_version,
                project=project,
                passed=lint_passed,
                score=1.0 if lint_passed else max(0.0, 1.0 - (lint_issues * 0.1)),
                details=f"{'Clean' if lint_passed else f'{lint_issues} issue(s)'} in {file_path}",
                metadata={
                    "issues_found": lint_issues,
                    "trigger_file": file_path,
                    "change_id": change_id,
                    "linter_output": lint_output[:500],
                },
            )
            client.record_eval(eval_lint)
        else:
            # Record that no linter is available — the audit team should know
            eval_lint = EvalResult(
                session_id=session_id,
                eval_name="lint_check",
                agent_version=agent_version,
                project=project,
                passed=True,
                score=None,
                details=f"No linter available (ruff/flake8 not installed) — {file_path} not checked",
                metadata={
                    "trigger_file": file_path,
                    "linter_available": False,
                },
            )
            client.record_eval(eval_lint)

        # On failure, also produce a BugEvent
        if not all_passed and tests_failed > 0:
            last_change = load_last_change(session_id)
            introduced_by = last_change["change_id"] if last_change else None

            failure_lines = []
            capture = False
            for line in output.splitlines():
                if "FAILED" in line or "ERROR" in line:
                    capture = True
                if capture:
                    failure_lines.append(line)
                if "short test summary" in line.lower():
                    capture = True

            failure_detail = "\n".join(failure_lines[-20:]) if failure_lines else output[-500:]

            bug = BugEvent(
                session_id=session_id,
                introduced_in_session_id=session_id,
                stage=BugStage.DEV,
                discovered_by=BugDiscoveredBy.TEST_FAILURE,
                severity="high" if tests_failed > 3 else "medium",
                agent_version=agent_version,
                file_paths=[file_path],
                description=f"Test failure after editing {file_path}: {tests_failed} test(s) failed",
                error_message=failure_detail[:1000],
                introduced_by_change_id=introduced_by,
                project=project,
            )
            client.record_bug(bug)
            logger.warning("BugEvent created: %s (%d tests failed)", bug.bug_id, tests_failed)

        client.close()

    except Exception as e:
        logger.error("Test runner hook error: %s", e)

    # Print output for Claude to see
    if all_passed:
        print(output.strip().split("\n")[-1] if output.strip() else "Tests passed")
    else:
        print(output)

    sys.exit(0)


if __name__ == "__main__":
    main()
