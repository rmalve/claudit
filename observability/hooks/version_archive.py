#!/usr/bin/env python3
"""
Version Archive Hook — snapshots agent definitions at session start.

Runs as part of the PreToolUse hook chain. Archives any agent definition
files (.claude/agents/*.md) that have changed since the last snapshot.

This ensures version_resolver always has current data when telemetry
hooks tag events with agent_version and agent_version_path.
"""

import sys
from pathlib import Path

# Add parent directories so we can import version_archive
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from observability.version_resolver import _get_project_root

# Inline the archiving logic to avoid import dependency on scripts/
import hashlib
import json
import shutil
from datetime import datetime, timezone


def _content_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _archive_changed_agents(project_root: Path) -> list[str]:
    """Archive any changed agent definitions. Returns list of archived agent names."""
    agents_dir = project_root / ".claude" / "agents"
    if not agents_dir.exists():
        return []

    archived = []
    for agent_file in agents_dir.glob("*.md"):
        agent_name = agent_file.stem
        versions_dir = agents_dir / f"{agent_name}.versions"
        index_path = versions_dir / "INDEX.json"

        # Load existing index
        index = {"versions": []}
        if index_path.exists():
            try:
                index = json.loads(index_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass

        # Check if changed
        current_hash = _content_hash(agent_file)
        if index["versions"]:
            latest = max(index["versions"], key=lambda v: v["version"])
            if latest.get("sha256") == current_hash:
                continue

        # Archive
        version_num = max((v["version"] for v in index["versions"]), default=0) + 1
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        filename = f"{agent_name}.v{version_num}.{timestamp}.md"

        versions_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(agent_file, versions_dir / filename)

        index["versions"].append({
            "version": version_num,
            "filename": filename,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sha256": current_hash,
        })
        index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")
        archived.append(agent_name)

    return archived


def main():
    try:
        root = _get_project_root()
        archived = _archive_changed_agents(root)
        if archived:
            # Print to stderr so it doesn't interfere with hook JSON output
            print(
                f"[version-archive] Archived {len(archived)} agent(s): {', '.join(archived)}",
                file=sys.stderr,
            )
    except Exception as e:
        # Never block the session — version archiving is best-effort
        print(f"[version-archive] Warning: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
