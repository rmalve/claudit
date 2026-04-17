"""Unit tests for Orchestrator --mode argument and conditional process registration.

Tests that per-session and cross-session modes register only the relevant
auditors while always including director:assign and director:synthesize.
"""
import sys
import types
from unittest.mock import patch, MagicMock

import pytest

# Install qdrant/fastembed stubs before importing orchestrator
from tests.test_filter_parsing import _install_stubs
_install_stubs()

# Stub redis so StreamClient import doesn't fail
_redis_mod = types.ModuleType("redis")
_redis_mod.Redis = MagicMock
sys.modules.setdefault("redis", _redis_mod)

from orchestrator import Orchestrator, ProjectConfig  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_projects():
    return [ProjectConfig(name="test-project", active=True, root="/tmp/test")]


def _mock_multi_projects():
    return [
        ProjectConfig(name="alpha", active=True, root="/tmp/alpha"),
        ProjectConfig(name="beta", active=True, root="/tmp/beta"),
        ProjectConfig(name="gamma", active=True, root="/tmp/gamma"),
    ]


def _make_orchestrator(mode=None, project_names=None):
    """Create an Orchestrator with mocked project loading."""
    kwargs = {}
    if mode is not None:
        kwargs["mode"] = mode
    if project_names is not None:
        kwargs["project_names"] = project_names
    projects = _mock_multi_projects() if project_names is not None else _mock_projects()
    with patch("orchestrator.load_projects", return_value=projects):
        return Orchestrator(**kwargs)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestModeProcessRegistration:
    """Orchestrator registers different auditors based on mode."""

    def test_per_session_mode_excludes_drift_and_cost(self):
        orch = _make_orchestrator(mode="per-session")
        keys = set(orch.processes.keys())

        # Per-session auditors present
        assert "auditor:trace" in keys
        assert "auditor:safety" in keys
        assert "auditor:policy" in keys
        assert "auditor:hallucination" in keys

        # Cross-session auditors absent
        assert "auditor:drift" not in keys
        assert "auditor:cost" not in keys

        # Directors always present
        assert "director:assign" in keys
        assert "director:synthesize" in keys

    def test_cross_session_mode_excludes_per_session_auditors(self):
        orch = _make_orchestrator(mode="cross-session")
        keys = set(orch.processes.keys())

        # Cross-session auditors present
        assert "auditor:drift" in keys
        assert "auditor:cost" in keys

        # Per-session auditors absent
        assert "auditor:trace" not in keys
        assert "auditor:safety" not in keys
        assert "auditor:policy" not in keys
        assert "auditor:hallucination" not in keys

        # Directors always present
        assert "director:assign" in keys
        assert "director:synthesize" in keys

    def test_default_mode_is_per_session(self):
        orch = _make_orchestrator()  # no mode argument
        keys = set(orch.processes.keys())

        # Should behave like per-session
        assert "auditor:trace" in keys
        assert "auditor:safety" in keys
        assert "auditor:policy" in keys
        assert "auditor:hallucination" in keys
        assert "auditor:drift" not in keys
        assert "auditor:cost" not in keys

        # Directors always present
        assert "director:assign" in keys
        assert "director:synthesize" in keys

    def test_director_assign_mode_matches_orchestrator_mode(self):
        """Director assign process gets mode-specific assign subcommand."""
        orch_ps = _make_orchestrator(mode="per-session")
        assign_cmd = orch_ps.processes["director:assign"].cmd
        assert "per-session-assign" in assign_cmd

        orch_cs = _make_orchestrator(mode="cross-session")
        assign_cmd = orch_cs.processes["director:assign"].cmd
        assert "cross-session-assign" in assign_cmd

    def test_project_names_filter(self):
        """project_names parameter filters loaded projects."""
        orch = _make_orchestrator(project_names=["alpha", "gamma"])
        names = [p.name for p in orch.projects]
        assert names == ["alpha", "gamma"]
        assert "beta" not in names
