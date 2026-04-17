"""Tests for Director prompt builders."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))

from run_director import build_per_session_assign_prompt, build_cross_session_assign_prompt


class TestPerSessionAssignPrompt:
    def test_includes_audited_filter(self):
        prompt = build_per_session_assign_prompt(["rpi"])
        assert "audited" in prompt.lower()

    def test_includes_only_per_session_auditors(self):
        prompt = build_per_session_assign_prompt(["rpi"])
        assert "trace" in prompt
        assert "safety" in prompt
        assert "policy" in prompt
        assert "hallucination" in prompt

    def test_excludes_drift_and_cost(self):
        prompt = build_per_session_assign_prompt(["rpi"])
        assert "do not assign tasks to drift or cost" in prompt.lower()


class TestCrossSessionAssignPrompt:
    def test_includes_only_cross_session_auditors(self):
        prompt = build_cross_session_assign_prompt(["rpi"])
        assert "drift" in prompt
        assert "cost" in prompt

    def test_mentions_raw_window(self):
        prompt = build_cross_session_assign_prompt(["rpi"])
        assert "raw_window" in prompt or "raw window" in prompt.lower()

    def test_mentions_summary_sessions(self):
        prompt = build_cross_session_assign_prompt(["rpi"])
        assert "summary_sessions" in prompt or "summary" in prompt.lower()

    def test_mentions_prior_findings_dedup(self):
        prompt = build_cross_session_assign_prompt(["rpi"])
        assert "already been identified" in prompt.lower() or "prior" in prompt.lower()

    def test_excludes_per_session_auditors(self):
        prompt = build_cross_session_assign_prompt(["rpi"])
        assert "do not assign tasks to trace" in prompt.lower() or "do not assign" in prompt.lower()
