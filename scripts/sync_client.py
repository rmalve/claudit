#!/usr/bin/env python3
"""
Client Observability Package Sync

Distributes the canonical client/observability/ package to registered
external projects. Each project holds its own copy for portability
(see the hooks pattern decision in client/README.md).

Usage:
    # Show what would change for the 'rpi' project (no writes):
    python scripts/sync_client.py --project rpi

    # Apply the sync:
    python scripts/sync_client.py --project rpi --apply

    # Sync every registered project:
    python scripts/sync_client.py --all --apply

    # Diff only — exits 1 if any project is out of sync:
    python scripts/sync_client.py --all --verify
"""

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
SOURCE_DIR = REPO_ROOT / "client" / "observability"
PROJECTS_FILE = REPO_ROOT / "config" / "projects.json"
VERSION_FILE_NAME = ".observability-version"
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


def _iter_source_files(source: Path):
    """Yield (relative_path, absolute_path) for every file under source,
    skipping pycache and compiled artifacts."""
    for path in sorted(source.rglob("*")):
        if path.is_dir():
            continue
        if any(part in EXCLUDE_DIRS for part in path.parts):
            continue
        if path.suffix in EXCLUDE_SUFFIXES:
            continue
        yield path.relative_to(source), path


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_version() -> str:
    """Extract __version__ from client/observability/__init__.py."""
    init = SOURCE_DIR / "__init__.py"
    text = init.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("__version__"):
            _, _, rhs = line.partition("=")
            return rhs.strip().strip("\"'")
    return "unknown"


def _compute_diff(source: Path, target_obs: Path):
    """Return (added, changed, unchanged, extras) relative-path lists.

    extras are files present in target but not tracked by source. The script
    NEVER deletes them — projects legitimately carry their own files here
    (standing_directives.md, agent version archives, custom hooks). Extras are
    surfaced for user awareness only.
    """
    added, changed, unchanged = [], [], []
    source_files = dict(_iter_source_files(source))

    for rel, src_path in source_files.items():
        dst_path = target_obs / rel
        if not dst_path.exists():
            added.append(rel)
        elif _file_hash(src_path) != _file_hash(dst_path):
            changed.append(rel)
        else:
            unchanged.append(rel)

    extras = []
    if target_obs.exists():
        for path in target_obs.rglob("*"):
            if path.is_dir():
                continue
            if any(part in EXCLUDE_DIRS for part in path.parts):
                continue
            if path.suffix in EXCLUDE_SUFFIXES:
                continue
            rel = path.relative_to(target_obs)
            if rel not in source_files:
                extras.append(rel)

    return added, changed, unchanged, extras


def _print_diff(project_name: str, target_obs: Path,
                added, changed, unchanged, extras) -> bool:
    """Print a diff summary. Returns True if sync would modify anything."""
    diff_count = len(added) + len(changed)
    if diff_count == 0:
        extras_note = f" ({len(extras)} untracked file(s) left alone)" if extras else ""
        print(f"  [{project_name}] in sync ({len(unchanged)} files){extras_note}")
        return False

    print(f"  [{project_name}] out of sync — target: {target_obs}")
    for rel in added:
        print(f"    + {rel}  (new)")
    for rel in changed:
        print(f"    ~ {rel}  (changed)")
    if extras:
        print(f"    (ignoring {len(extras)} untracked file(s) in target — "
              f"project-specific content is preserved)")
    print(f"    {len(unchanged)} unchanged, {len(added)} added, "
          f"{len(changed)} changed")
    return True


def _apply_sync(source: Path, target_obs: Path, version: str,
                added, changed) -> None:
    target_obs.mkdir(parents=True, exist_ok=True)
    for rel in added + changed:
        src = source / rel
        dst = target_obs / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    version_path = target_obs.parent / VERSION_FILE_NAME
    version_path.write_text(version + "\n", encoding="utf-8")


def _load_projects(only: str | None) -> list[dict]:
    """Load projects.json and filter by name."""
    if not PROJECTS_FILE.exists():
        print(f"ERROR: {PROJECTS_FILE} not found.", file=sys.stderr)
        sys.exit(2)

    data = json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
    projects = data.get("projects", [])

    if only is not None:
        projects = [p for p in projects if p["name"] == only]
        if not projects:
            print(f"ERROR: project '{only}' not found in {PROJECTS_FILE}.",
                  file=sys.stderr)
            sys.exit(2)

    active = [p for p in projects if p.get("active", True)]
    return active


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync client/observability/ into registered external projects."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--project", help="Sync one project by name")
    group.add_argument("--all", action="store_true",
                       help="Sync every active project in config/projects.json")

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true",
                      help="Write files (default is dry-run)")
    mode.add_argument("--verify", action="store_true",
                      help="Exit 1 if any project is out of sync; do not write")
    args = parser.parse_args()

    if not SOURCE_DIR.exists():
        print(f"ERROR: source dir missing: {SOURCE_DIR}", file=sys.stderr)
        return 2

    projects = _load_projects(only=args.project)
    version = _load_version()
    mode_label = "verify" if args.verify else ("apply" if args.apply else "dry-run")

    print(f"Source: {SOURCE_DIR}")
    print(f"Version: {version}")
    print(f"Mode: {mode_label}")
    print(f"Projects: {', '.join(p['name'] for p in projects)}\n")

    any_diff = False
    for project in projects:
        target_root = Path(project["root"])
        target_obs = target_root / "observability"

        if not target_root.exists():
            print(f"  [{project['name']}] SKIP — root does not exist: {target_root}")
            continue

        marker_path = target_root / VERSION_FILE_NAME
        target_version = None
        if marker_path.is_file():
            target_version = marker_path.read_text(encoding="utf-8").strip() or None
        if target_version and target_version != version:
            print(f"  [{project['name']}] version drift: target {target_version} "
                  f"→ source {version}")
        elif not target_version:
            print(f"  [{project['name']}] no version marker at target "
                  f"(first sync?)")

        added, changed, unchanged, extras = _compute_diff(SOURCE_DIR, target_obs)
        out_of_sync = _print_diff(project["name"], target_obs,
                                  added, changed, unchanged, extras)
        any_diff = any_diff or out_of_sync

        if args.apply and out_of_sync:
            _apply_sync(SOURCE_DIR, target_obs, version, added, changed)
            print(f"    ✓ synced (version {version} stamped at "
                  f"{target_obs.parent / VERSION_FILE_NAME})")

    if args.verify and any_diff:
        print("\nverify: at least one project is out of sync.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
