#!/usr/bin/env python3
"""
Agent Definition Version Archiver

Snapshots agent definition files (.claude/agents/*.md) when their content
changes. Creates versioned copies and maintains an INDEX.json per agent.

Designed to run:
  - As a pre-session hook (archives any changes before telemetry starts)
  - Manually via CLI (for initial setup or catch-up)

Usage:
    # Archive all changed agent definitions in a project:
    python scripts/version_archive.py --root /path/to/project

    # Dry run — show what would be archived:
    python scripts/version_archive.py --root /path/to/project --dry-run

    # Archive a single agent:
    python scripts/version_archive.py --root /path/to/project --agent architect
"""

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


def content_hash(path: Path) -> str:
    """SHA-256 of file contents."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_index(index_path: Path) -> dict:
    """Load or initialize an INDEX.json."""
    if index_path.exists():
        try:
            return json.loads(index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"versions": []}


def next_version(index: dict) -> int:
    """Compute the next version number."""
    if not index["versions"]:
        return 1
    return max(v["version"] for v in index["versions"]) + 1


def needs_archive(agent_path: Path, index: dict) -> bool:
    """Check if the agent file has changed since the last archived version."""
    if not index["versions"]:
        return True
    current_hash = content_hash(agent_path)
    latest = max(index["versions"], key=lambda v: v["version"])
    return latest.get("sha256") != current_hash


def archive_agent(agent_path: Path, dry_run: bool = False) -> dict | None:
    """Archive an agent definition if it has changed.

    Returns the new version entry dict, or None if no change detected.
    """
    agent_name = agent_path.stem
    versions_dir = agent_path.parent / f"{agent_name}.versions"
    index_path = versions_dir / "INDEX.json"

    index = load_index(index_path)

    if not needs_archive(agent_path, index):
        return None

    version_num = next_version(index)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"{agent_name}.v{version_num}.{timestamp}.md"
    sha = content_hash(agent_path)

    entry = {
        "version": version_num,
        "filename": filename,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sha256": sha,
    }

    if dry_run:
        return entry

    versions_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(agent_path, versions_dir / filename)

    index["versions"].append(entry)
    index_path.write_text(
        json.dumps(index, indent=2), encoding="utf-8"
    )

    return entry


def archive_all(project_root: Path, dry_run: bool = False, agent_filter: str | None = None) -> list[dict]:
    """Archive all changed agent definitions in a project.

    Returns list of new version entries created.
    """
    agents_dir = project_root / ".claude" / "agents"
    if not agents_dir.exists():
        return []

    results = []
    for agent_file in sorted(agents_dir.glob("*.md")):
        if agent_filter and agent_file.stem != agent_filter:
            continue
        entry = archive_agent(agent_file, dry_run=dry_run)
        if entry:
            results.append({"agent": agent_file.stem, **entry})

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Archive agent definition changes for audit version tracking"
    )
    parser.add_argument(
        "--root", required=True,
        help="Path to the external project root (must contain .claude/agents/)"
    )
    parser.add_argument(
        "--agent",
        help="Archive only this agent (by name, without .md extension)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be archived without writing files"
    )
    args = parser.parse_args()

    project_root = Path(args.root).resolve()
    agents_dir = project_root / ".claude" / "agents"

    if not agents_dir.exists():
        print(f"No agents directory found at {agents_dir}")
        sys.exit(1)

    results = archive_all(project_root, dry_run=args.dry_run, agent_filter=args.agent)

    if not results:
        print("No agent definitions have changed since last archive.")
        return

    label = "Would archive" if args.dry_run else "Archived"
    for r in results:
        print(f"  {label}: {r['agent']} -> v{r['version']} ({r['filename']})")

    if args.dry_run:
        print(f"\nDry run complete. {len(results)} agent(s) would be archived.")
    else:
        print(f"\n{len(results)} agent(s) archived.")


if __name__ == "__main__":
    main()
