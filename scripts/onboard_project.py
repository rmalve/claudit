#!/usr/bin/env python3
"""
Project Onboarding Script

Generates everything needed to integrate a new external project with
the LLM Observability audit platform:

1. Redis ACL entry for the project
2. Adapter scaffold
3. Environment variable reference
4. Claude Code hook configuration (.claude/settings.json snippet)
5. CLAUDE.md audit authority verbiage
6. Per-agent skill audit interface verbiage

Usage:
    python scripts/onboard_project.py --project my-project --root /path/to/project

    # Dry run (print everything, write nothing):
    python scripts/onboard_project.py --project my-project --root /path/to/project --dry-run

    # Write directly to the external project:
    python scripts/onboard_project.py --project my-project --root /path/to/project --apply
"""

import argparse
import json
import os
import secrets
import string
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
CONFIG_DIR = REPO_ROOT / "config"
VERBIAGE_DIR = CONFIG_DIR / "external-verbiage"
ADAPTERS_DIR = REPO_ROOT / "adapters"
ACL_FILE = CONFIG_DIR / "redis-acl.conf"
PROJECTS_FILE = CONFIG_DIR / "projects.json"


def generate_password(length: int = 32) -> str:
    """Generate a secure random password for Redis ACL."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def build_acl_entry(project: str, password: str) -> str:
    """Build the Redis ACL line for this project.

    Redis ACL files cannot contain comments — documentation is in redis-acl.README.md.
    """
    return (
        f"user project-{project} on >{password} "
        f"~directives:{project} ~compliance:{project} "
        f"~promotions:{project} ~promotion_ack:{project} "
        f"+XREADGROUP +XACK +XADD +XINFO +XLEN +XGROUP +PING +AUTH\n"
    )


def build_adapter(project: str, project_root: str) -> str:
    """Build a project-specific adapter file."""
    class_name = "".join(
        word.capitalize() for word in project.replace("-", "_").split("_")
    )
    env_var = f"{project.upper().replace('-', '_')}_PROJECT_ROOT"
    return f'''"""
{project} adapter for the LLM observability framework.
"""

import os
from pathlib import Path

from adapters.base import ProjectAdapter


class {class_name}Adapter(ProjectAdapter):
    """Adapter for the {project} project."""

    @property
    def project_name(self) -> str:
        return "{project}"

    @property
    def project_root(self) -> Path:
        root = os.environ.get("{env_var}")
        if not root:
            raise RuntimeError(
                "{env_var} environment variable is not set. "
                "Set it to the absolute path of the {project} project directory."
            )
        return Path(root)

    @property
    def schema_dir(self) -> Path | None:
        schemas = self.project_root / "api" / "schemas"
        return schemas if schemas.exists() else None
'''


def build_env_reference(project: str, password: str, project_root: str) -> str:
    """Build the environment variable reference."""
    env_var = f"{project.upper().replace('-', '_')}_PROJECT_ROOT"
    return f"""# ── LLM Observability Audit Platform ──
# Add these to your project's environment (shell profile, .env, etc.)

OBSERVABILITY_PROJECT="{project}"
PROJECT_ROOT="{project_root}"
{env_var}="{project_root}"
QDRANT_URL="http://localhost:6333"
REDIS_URL="redis://localhost:6379"
REDIS_USERNAME="project-{project}"
REDIS_PASSWORD="{password}"

# Set AGENT_NAME when launching agent sessions manually (e.g. AGENT_NAME=lead-engineer).
# Not needed for Agent tool sub-agents — auto-detected from Claude Code hook data.
AGENT_NAME="main"
"""


def build_hook_config(observability_path: str) -> str:
    """Build the Claude Code hooks configuration."""
    hooks_path = Path(observability_path) / "observability" / "hooks"

    config = {
        "hooks": {
            "PostToolUse": [
                {
                    "command": f"python \"{hooks_path / 'post_tool_use.py'}\"",
                    "description": "Audit telemetry: captures tool calls, agent spawns, code changes to QDrant"
                },
                {
                    "command": f"python \"{hooks_path / 'test_runner.py'}\"",
                    "description": "Eval telemetry: runs tests and lint after .py file changes"
                }
            ],
            "Stop": [
                {
                    "command": f"python \"{hooks_path / 'session_end.py'}\"",
                    "description": "Audit telemetry: aggregates session summary and flushes metrics"
                }
            ],
            "PreToolUse": [
                {
                    "command": f"python \"{hooks_path / 'version_archive.py'}\"",
                    "description": "Agent versioning: snapshots changed agent definitions before telemetry starts"
                },
                {
                    "command": f"python \"{hooks_path / 'directive_intake.py'}\"",
                    "description": "Audit directives: reads pending directives from the Audit Director"
                }
            ]
        }
    }

    return json.dumps(config, indent=2)


def build_hook_config_merge_snippet(observability_path: str) -> str:
    """Build a snippet showing how to merge hooks into existing settings."""
    hooks_path = Path(observability_path) / "observability" / "hooks"

    return f"""
If your project already has a .claude/settings.json, merge these hooks
into the existing "hooks" object:

  "PostToolUse": [
    {{
      "command": "python \\"{hooks_path / 'post_tool_use.py'}\\"",
      "description": "Audit telemetry: captures tool calls, agent spawns, code changes"
    }},
    {{
      "command": "python \\"{hooks_path / 'test_runner.py'}\\"",
      "description": "Eval telemetry: runs tests and lint after .py file changes"
    }}
  ],
  "Stop": [
    {{
      "command": "python \\"{hooks_path / 'session_end.py'}\\"",
      "description": "Audit telemetry: aggregates session summary"
    }}
  ],
  "PreToolUse": [
    {{
      "command": "python \\"{hooks_path / 'version_archive.py'}\\"",
      "description": "Agent versioning: snapshots changed agent definitions"
    }},
    {{
      "command": "python \\"{hooks_path / 'directive_intake.py'}\\"",
      "description": "Audit directives: reads pending directives from the Audit Director"
    }}
  ]

Directive compliance is NOT a hook — agents invoke it explicitly:
  python -m observability.hooks.directive_compliance \\
      --directive-id "DIRECTIVE-ID" --agent "agent-name" --action "what was done"
"""


def read_verbiage(filename: str) -> str:
    """Read a verbiage template file."""
    path = VERBIAGE_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return f"[Template not found: {filename}]"


def print_section(title: str, content: str) -> None:
    """Print a formatted section."""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")
    print(content)


def main():
    parser = argparse.ArgumentParser(
        description="Onboard a new external project to the LLM Observability audit platform"
    )
    parser.add_argument(
        "--project", required=True,
        help="Project identifier (e.g., 'my-project'). Must be lowercase, alphanumeric with hyphens."
    )
    parser.add_argument(
        "--root", required=True,
        help="Absolute path to the external project's root directory"
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=True,
        help="Print everything but write nothing (default)"
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Write files to the audit platform and external project"
    )
    parser.add_argument(
        "--observability-path", default=str(REPO_ROOT),
        help="Path to the llm-observability repo (default: this repo)"
    )
    args = parser.parse_args()

    project = args.project.lower().strip()
    project_root = Path(args.root).resolve()
    observability_path = Path(args.observability_path).resolve()

    if args.apply:
        args.dry_run = False

    # Validate
    if not all(c.isalnum() or c == "-" for c in project):
        print("ERROR: Project name must be lowercase alphanumeric with hyphens only.")
        sys.exit(1)

    if not project_root.exists():
        print(f"WARNING: Project root does not exist yet: {project_root}")

    # Generate
    password = generate_password()
    acl_entry = build_acl_entry(project, password)
    adapter_code = build_adapter(project, str(project_root))
    env_reference = build_env_reference(project, password, str(project_root))
    hook_config = build_hook_config(str(observability_path))
    merge_snippet = build_hook_config_merge_snippet(str(observability_path))
    claude_md_verbiage = read_verbiage("claude-md-audit-authority.md")
    skill_verbiage = read_verbiage("skill-audit-interface.md")

    # Output
    print_section("1. REDIS ACL ENTRY", acl_entry)
    print(f"  Append this to: {ACL_FILE}")
    print(f"  Then restart Redis or run: redis-cli ACL LOAD")

    print_section("2. PROJECT ADAPTER", adapter_code)
    adapter_filename = f"{project.replace('-', '_')}_adapter.py"
    print(f"  Save to: {ADAPTERS_DIR / adapter_filename}")

    print_section("3. ENVIRONMENT VARIABLES", env_reference)
    print(f"  Set these in the external project's environment.")
    print(f"  ⚠  Store the password securely — it won't be shown again.")

    print_section("4. CLAUDE CODE HOOKS — Full settings.json", hook_config)
    print_section("4b. CLAUDE CODE HOOKS — Merge snippet", merge_snippet)

    print_section("5. CLAUDE.MD AUDIT AUTHORITY", claude_md_verbiage)
    print(f"  Paste near the top of: {project_root / 'CLAUDE.md'}")

    print_section("6. PER-AGENT SKILL AUDIT INTERFACE", skill_verbiage)
    print(f"  Paste into each agent's skill definition or system prompt.")

    print_section("7. AGENT VERSION ARCHIVING",
        "The version_archive hook (included in the PreToolUse hooks above)\n"
        "automatically snapshots agent definitions when they change.\n\n"
        "To initialize version tracking now:\n"
        f"  python scripts/version_archive.py --root \"{project_root}\"\n\n"
        "This creates .versions/ directories in .claude/agents/ with INDEX.json\n"
        "files that the telemetry hooks use to tag events with agent_version.\n"
        "Without this, the Drift Detector and Policy Auditor cannot function."
    )

    # Apply if requested
    if not args.dry_run:
        print_section("APPLYING CHANGES", "Writing files...")

        # 1. Append ACL entry
        with open(ACL_FILE, "a", encoding="utf-8") as f:
            f.write(acl_entry)
        print(f"  ✓ ACL entry appended to {ACL_FILE}")

        # 1b. Register project in projects.json
        projects_data = {"projects": []}
        if PROJECTS_FILE.exists():
            try:
                projects_data = json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass

        # Check for duplicate
        existing_names = [p["name"] for p in projects_data.get("projects", [])]
        if project not in existing_names:
            projects_data["projects"].append({
                "name": project,
                "active": True,
                "root": str(project_root),
                "description": "",
            })
            PROJECTS_FILE.write_text(
                json.dumps(projects_data, indent=2), encoding="utf-8"
            )
            print(f"  ✓ Project registered in {PROJECTS_FILE}")
        else:
            print(f"  ⚠  Project '{project}' already registered in {PROJECTS_FILE}")

        # 2. Write adapter
        adapter_path = ADAPTERS_DIR / adapter_filename
        adapter_path.write_text(adapter_code, encoding="utf-8")
        print(f"  ✓ Adapter written to {adapter_path}")

        # 3. Write hook config template to external project
        claude_dir = project_root / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)

        settings_path = claude_dir / "settings.json"
        if settings_path.exists():
            print(f"  ⚠  {settings_path} already exists — not overwriting.")
            print(f"      Merge the hooks manually using the snippet above.")
        else:
            settings_path.write_text(hook_config, encoding="utf-8")
            print(f"  ✓ Hook config written to {settings_path}")

        # 4. Write env reference
        env_path = project_root / ".env.audit"
        env_path.write_text(env_reference, encoding="utf-8")
        print(f"  ✓ Environment reference written to {env_path}")

        # 5. Create empty standing directives file
        standing_path = project_root / "observability" / "standing_directives.md"
        standing_path.parent.mkdir(parents=True, exist_ok=True)
        if not standing_path.exists():
            standing_path.write_text("", encoding="utf-8")
            print(f"  ✓ Standing directives file created at {standing_path}")
        else:
            print(f"  ⚠  {standing_path} already exists — not overwriting.")

        # 6. Initialize agent version archives
        agents_dir = project_root / ".claude" / "agents"
        if agents_dir.exists():
            agent_files = list(agents_dir.glob("*.md"))
            if agent_files:
                # Import and run the archiver
                sys.path.insert(0, str(REPO_ROOT))
                from scripts.version_archive import archive_all
                results = archive_all(project_root)
                if results:
                    for r in results:
                        print(f"  ✓ Archived {r['agent']} → v{r['version']}")
                else:
                    print(f"  ✓ Agent versions already up to date")
            else:
                print(f"  ⚠  No agent definitions found in {agents_dir} — version archiving skipped")
                print(f"      Run version_archive.py after adding agents")
        else:
            print(f"  ⚠  No .claude/agents/ directory found — version archiving skipped")
            print(f"      Run version_archive.py after setting up Claude Code agents")

        print(f"\n  Done. Remember to:")
        print(f"    1. Run 'redis-cli ACL LOAD' or restart Redis")
        print(f"    2. Set the environment variables in the external project")
        print(f"    3. Paste audit authority verbiage into CLAUDE.md")
        print(f"    4. Paste audit interface verbiage into each agent skill")
        print(f"    5. Start the orchestrator: python orchestrator.py --project {project}")
        print(f"    6. After adding/changing agents, run: python scripts/version_archive.py --root \"{project_root}\"")
    else:
        print_section("DRY RUN COMPLETE",
            "No files were written. Run with --apply to write files.\n"
            f"  python scripts/onboard_project.py --project {project} --root \"{project_root}\" --apply"
        )


if __name__ == "__main__":
    main()
