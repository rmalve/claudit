"""
Version Resolver — reads current agent versions from the versioning system.

Looks up the latest version number from each agent's INDEX.json
(created by scripts/version_archive.py) and returns it for telemetry tagging.
"""

import json
import logging
import os
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_project_root() -> Path:
    """Find the project root (directory containing CLAUDE.md).

    Priority: PROJECT_ROOT env var > walk up from file > cwd.
    Hooks should set PROJECT_ROOT from stdin cwd before calling this.
    """
    root = os.environ.get("PROJECT_ROOT")
    if root:
        return Path(root)

    # Walk up from this file
    current = Path(__file__).resolve().parent
    for _ in range(5):
        if (current / "CLAUDE.md").exists():
            return current
        current = current.parent

    return Path.cwd()


def get_agent_version(agent_name: str) -> str | None:
    """Get the current version of an agent definition.

    Reads from the agent's .versions/INDEX.json created by the versioning system.

    Args:
        agent_name: Agent name without .md extension (e.g., "architect", "api-engineer")

    Returns:
        Version string like "v3" or None if no versions found.
    """
    root = _get_project_root()
    index_path = root / ".claude" / "agents" / f"{agent_name}.versions" / "INDEX.json"

    if not index_path.exists():
        return None

    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
        versions = data.get("versions", [])
        if versions:
            latest = max(versions, key=lambda v: v["version"])
            return f"v{latest['version']}"
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.debug("Could not read agent version for %s: %s", agent_name, e)

    return None


def get_agent_version_path(agent_name: str) -> str | None:
    """Get the absolute path to the current versioned agent definition file.

    Args:
        agent_name: Agent name without .md extension (e.g., "architect")

    Returns:
        Absolute path like ".claude/agents/architect.versions/architect.v3.20260405-144427.md"
        or None if no versions found.
    """
    root = _get_project_root()
    index_path = root / ".claude" / "agents" / f"{agent_name}.versions" / "INDEX.json"

    if not index_path.exists():
        return None

    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
        versions = data.get("versions", [])
        if versions:
            latest = max(versions, key=lambda v: v["version"])
            version_file = index_path.parent / latest["filename"]
            return str(version_file.resolve())
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.debug("Could not resolve agent version path for %s: %s", agent_name, e)

    return None


def get_all_agent_versions() -> dict[str, str]:
    """Get current versions for all agents.

    Returns:
        Dict mapping agent name to version string, e.g.:
        {"architect": "v1", "api-engineer": "v3", "security": "v2"}
    """
    root = _get_project_root()
    agents_dir = root / ".claude" / "agents"

    if not agents_dir.exists():
        return {}

    versions = {}
    for agent_file in agents_dir.glob("*.md"):
        name = agent_file.stem
        version = get_agent_version(name)
        if version:
            versions[name] = version

    return versions


@lru_cache(maxsize=1)
def get_cached_agent_versions() -> dict[str, str]:
    """Cached version lookup — call once per session."""
    versions = get_all_agent_versions()
    if versions:
        logger.info("Agent versions loaded: %s", versions)
    return versions


def resolve_agent_name(hook_agent_type: str | None = None) -> str:
    """Determine the current agent name.

    Priority: hook stdin agent_type > AGENT_NAME env var > 'main'.
    """
    if hook_agent_type:
        return hook_agent_type
    return os.environ.get("AGENT_NAME", "main")


def resolve_version_for_agent(agent_name: str) -> str | None:
    """Resolve the current version for a specific agent, using cache."""
    versions = get_cached_agent_versions()
    return versions.get(agent_name) or versions.get(agent_name.replace("_", "-"))


def resolve_version_path_for_agent(agent_name: str) -> str | None:
    """Resolve the absolute path to the current versioned agent file."""
    name = agent_name.replace("_", "-")
    return get_agent_version_path(name) or get_agent_version_path(agent_name)


def resolve_all_versions_json() -> str | None:
    """Return a JSON-encoded map of all agent versions as a fallback.

    Used when the specific agent name is unknown — stores the full version
    snapshot so auditors can cross-reference with agent attribution later.
    """
    import json
    versions = get_cached_agent_versions()
    return json.dumps(versions) if versions else None


def get_all_agent_version_paths() -> dict[str, str]:
    """Return {agent_name: absolute_path} for every agent with an INDEX.json.

    Parallel to get_all_agent_versions() — same iteration, but yields paths
    instead of version labels. Empty dict if no archives found.
    """
    root = _get_project_root()
    agents_dir = root / ".claude" / "agents"

    if not agents_dir.exists():
        return {}

    paths: dict[str, str] = {}
    for agent_file in agents_dir.glob("*.md"):
        name = agent_file.stem
        path = get_agent_version_path(name)
        if path:
            paths[name] = path
    return paths


def resolve_all_paths_json() -> str | None:
    """Return a JSON-encoded map of all agent version paths.

    Used as a fallback for main-session tool calls, where `main` has no
    single versioned-agent file. The Drift Auditor correlates behavior
    against definitions live at cycle time — the map gives it every
    subagent path available when the tool call fired.
    """
    import json
    paths = get_all_agent_version_paths()
    return json.dumps(paths) if paths else None
