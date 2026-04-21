"""Unit tests for escalation #1 fixes: agent_version_path JSON-map fallback,
active_duration_seconds, and task_type classifier.
"""
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Stubs needed for session_end.py's transitive imports (observability.client
# → qdrant_backend → fastembed). Same pattern as other test modules.
from tests.test_filter_parsing import _install_stubs
_install_stubs()

_redis_mod = types.ModuleType("redis")
_redis_mod.Redis = MagicMock
sys.modules.setdefault("redis", _redis_mod)

from observability import version_resolver  # noqa: E402
from observability.hooks import session_end  # noqa: E402


# ---------------------------------------------------------------------------
# Item 1: agent_version_path JSON-map fallback
# ---------------------------------------------------------------------------


def _write_agent_archive(project_root: Path, agent_name: str, version_num: int = 1) -> None:
    """Create a minimal .claude/agents/<name>.versions/INDEX.json so the
    resolver can find a path. Also writes an empty version file."""
    versions_dir = project_root / ".claude" / "agents" / f"{agent_name}.versions"
    versions_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{agent_name}.v{version_num}.20260420-000000.md"
    (versions_dir / filename).write_text("")
    index = {
        "agent": agent_name,
        "versions": [{"version": version_num, "filename": filename}],
    }
    (versions_dir / "INDEX.json").write_text(json.dumps(index))


class TestResolveAllPathsJson:
    """HARDEN escalation #1, Item 1: Drift Auditor gets a map of every subagent
    path available at the time of a main-session tool call."""

    def test_returns_map_of_agents(self, tmp_path, monkeypatch):
        _write_agent_archive(tmp_path, "architect", 1)
        _write_agent_archive(tmp_path, "api-engineer", 3)
        # Add agent .md files so get_all_agent_version_paths finds them.
        agents_dir = tmp_path / ".claude" / "agents"
        (agents_dir / "architect.md").write_text("")
        (agents_dir / "api-engineer.md").write_text("")

        monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))

        raw = version_resolver.resolve_all_paths_json()
        assert raw is not None
        paths = json.loads(raw)
        assert set(paths.keys()) == {"architect", "api-engineer"}
        assert paths["architect"].endswith("architect.v1.20260420-000000.md")
        assert paths["api-engineer"].endswith("api-engineer.v3.20260420-000000.md")

    def test_returns_none_when_no_archives(self, tmp_path, monkeypatch):
        # Empty project — no .claude/agents/
        monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
        assert version_resolver.resolve_all_paths_json() is None


# ---------------------------------------------------------------------------
# Item 2: active_duration_seconds
# ---------------------------------------------------------------------------


def _iso(offset_seconds: int, base: datetime | None = None) -> str:
    base = base or datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc)
    return (base + timedelta(seconds=offset_seconds)).isoformat()


class TestActiveDuration:
    """Idle gaps between consecutive tool calls above the threshold are excluded."""

    def test_excludes_idle_gaps(self, monkeypatch):
        # Use the module's configured threshold (default 300). Timestamps at
        # 0, 10, 700, 720 → gaps of 10, 690 (idle), 20. Active = 30.
        monkeypatch.setattr(session_end, "IDLE_THRESHOLD_SECONDS", 300)
        ts = [_iso(0), _iso(10), _iso(700), _iso(720)]
        assert session_end._compute_active_duration(ts) == 30

    def test_matches_duration_when_all_contiguous(self, monkeypatch):
        monkeypatch.setattr(session_end, "IDLE_THRESHOLD_SECONDS", 300)
        ts = [_iso(i) for i in range(5)]  # 0, 1, 2, 3, 4 — all 1s gaps
        assert session_end._compute_active_duration(ts) == 4

    def test_zero_when_fewer_than_two_timestamps(self):
        assert session_end._compute_active_duration([]) == 0.0
        assert session_end._compute_active_duration([_iso(0)]) == 0.0

    def test_robust_to_malformed_timestamps(self, monkeypatch):
        monkeypatch.setattr(session_end, "IDLE_THRESHOLD_SECONDS", 300)
        # Malformed ones are dropped; remaining two have a 5s gap.
        ts = ["not a date", _iso(0), None, _iso(5), ""]
        assert session_end._compute_active_duration(ts) == 5


# ---------------------------------------------------------------------------
# Item 3: task_type classifier
# ---------------------------------------------------------------------------


class TestClassifySession:
    """Heuristic classifier — ABORTED > IMPLEMENTATION > KM > PLANNING > UNCLASSIFIED."""

    def test_aborted_on_zero_calls(self):
        assert session_end._classify_session(0, []) == "ABORTED"

    def test_implementation_wins_over_planning(self, monkeypatch):
        monkeypatch.setenv("PROJECT_ROOT", "/proj")
        monkeypatch.delenv("OBSERVABILITY_VAULT_ROOTS", raising=False)
        paths = ["/proj/.claude/plans/foo.md", "/proj/src/bar.py"]
        assert session_end._classify_session(2, paths) == "IMPLEMENTATION"

    def test_knowledge_management_for_vault_only(self, monkeypatch):
        monkeypatch.setenv("PROJECT_ROOT", "/proj")
        monkeypatch.setenv("OBSERVABILITY_VAULT_ROOTS", "/vault/notes")
        paths = ["/vault/notes/daily/2026-04-21.md"]
        assert session_end._classify_session(1, paths) == "KNOWLEDGE_MANAGEMENT"

    def test_planning_only_when_sole_target(self, monkeypatch):
        monkeypatch.setenv("PROJECT_ROOT", "/proj")
        monkeypatch.delenv("OBSERVABILITY_VAULT_ROOTS", raising=False)
        paths = ["/home/user/.claude/plans/feature.md"]
        assert session_end._classify_session(1, paths) == "PLANNING"

    def test_unclassified_on_read_only_session(self, monkeypatch):
        monkeypatch.setenv("PROJECT_ROOT", "/proj")
        monkeypatch.delenv("OBSERVABILITY_VAULT_ROOTS", raising=False)
        # Non-zero calls but no write_paths — Read-heavy research session.
        assert session_end._classify_session(10, []) == "UNCLASSIFIED"

    def test_windows_path_separators_normalized(self, monkeypatch):
        monkeypatch.setenv("PROJECT_ROOT", "C:\\proj")
        monkeypatch.delenv("OBSERVABILITY_VAULT_ROOTS", raising=False)
        paths = ["C:\\proj\\src\\main.py"]
        assert session_end._classify_session(1, paths) == "IMPLEMENTATION"
