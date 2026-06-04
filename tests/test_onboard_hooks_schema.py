"""Schema-conformance tests for scripts/onboard_project.py hook generation.

Regression guard for the 2026-06-04 bug: build_hook_config() emitted a flat
`{"command": ..., "description": ...}` shape directly inside each hook-event
array. Claude Code's settings schema requires each entry to be a *matcher group*
object containing a nested `hooks` array of `{"type": "command", "command": ...}`
objects. The flat shape made Claude Code reject the file with
"expected array, but received undefined" (the missing nested `hooks` array).

These tests validate the generator output against the ACTUAL schema, not against
the config/hook-templates/*.json files — those templates encoded the same wrong
format, so they cannot be the source of truth.
"""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import onboard_project  # noqa: E402

HOOK_EVENTS = ("PreToolUse", "PostToolUse", "Stop")


def _assert_valid_hook_block(hooks: dict) -> None:
    """Assert a `hooks` object conforms to the Claude Code settings schema."""
    assert isinstance(hooks, dict) and hooks, "hooks block must be a non-empty object"
    for event, groups in hooks.items():
        assert isinstance(groups, list), f"{event} must be an array"
        assert groups, f"{event} must have at least one matcher group"
        for group in groups:
            assert isinstance(group, dict), f"{event} entries must be objects"
            # The fatal field: each group MUST carry a nested `hooks` array.
            assert "hooks" in group, (
                f"{event} group missing required 'hooks' array "
                f"(this is the bug that caused 'expected array, but received undefined')"
            )
            assert isinstance(group["hooks"], list) and group["hooks"], (
                f"{event} group 'hooks' must be a non-empty array"
            )
            # A flat group must NOT put the command at the top level.
            assert "command" not in group, (
                f"{event} group must not specify 'command' directly — it belongs "
                f"inside the nested 'hooks' array"
            )
            if event in ("PreToolUse", "PostToolUse"):
                assert "matcher" in group, f"{event} group should declare a matcher"
            for hook in group["hooks"]:
                assert hook.get("type") == "command", "hook.type must be 'command'"
                assert isinstance(hook.get("command"), str) and hook["command"], (
                    "hook.command must be a non-empty string"
                )
                # Exec form keeps the script path in args (a list of strings).
                args = hook.get("args")
                if args is not None:
                    assert isinstance(args, list) and all(isinstance(a, str) for a in args), (
                        "hook.args must be a list of strings (exec form)"
                    )
                # Regression guard (2026-06-04): a hook's script path must be
                # cwd-independent — ${CLAUDE_PROJECT_DIR}-anchored, {OBSERVABILITY_PATH}
                # template placeholder, or absolute — so it resolves when a build
                # step runs from a subdirectory (e.g. `cd webapp`). A bare relative
                # path like 'observability/hooks/x.py' fails to resolve there, exits
                # non-zero, and PreToolUse then denies every tool.
                invocation = " ".join([hook["command"], *(args or [])])
                if "observability/hooks/" in invocation or "observability\\hooks\\" in invocation:
                    anchored = (
                        "${CLAUDE_PROJECT_DIR}" in invocation
                        or "{OBSERVABILITY_PATH}" in invocation
                        or ":" in invocation                       # Windows absolute (C:\...)
                        or invocation.lstrip().startswith("/")     # POSIX absolute
                    )
                    assert anchored, (
                        f"hook script path must be cwd-independent, not bare-relative: {invocation}"
                    )


class TestBuildHookConfigSchema:
    def test_full_config_is_valid_json(self):
        raw = onboard_project.build_hook_config("/fake/obs/path")
        parsed = json.loads(raw)  # must be parseable
        assert "hooks" in parsed

    def test_full_config_matches_claude_code_schema(self):
        parsed = json.loads(onboard_project.build_hook_config("/fake/obs/path"))
        _assert_valid_hook_block(parsed["hooks"])

    def test_all_expected_events_present(self):
        hooks = json.loads(onboard_project.build_hook_config("/fake/obs/path"))["hooks"]
        for event in HOOK_EVENTS:
            assert event in hooks, f"expected {event} in generated hooks"

    def test_post_tool_use_records_telemetry(self):
        hooks = json.loads(onboard_project.build_hook_config("/fake/obs/path"))["hooks"]
        texts = [
            " ".join([h["command"], *h.get("args", [])])
            for group in hooks["PostToolUse"]
            for h in group["hooks"]
        ]
        assert any("post_tool_use" in t for t in texts)

    def test_hook_paths_are_cwd_independent(self):
        """Regression (2026-06-04): generated hook paths must be anchored to
        ${CLAUDE_PROJECT_DIR}, never bare-relative — otherwise a build step run
        from a subdirectory (cd webapp) blocks every tool via PreToolUse denial."""
        hooks = json.loads(onboard_project.build_hook_config("/fake/obs/path"))["hooks"]
        for event, groups in hooks.items():
            for group in groups:
                for h in group["hooks"]:
                    text = " ".join([h["command"], *h.get("args", [])])
                    assert "${CLAUDE_PROJECT_DIR}" in text, (
                        f"{event} hook not anchored to CLAUDE_PROJECT_DIR: {text}"
                    )


class TestShippedTemplatesMatchSchema:
    """The config/hook-templates/*.json files must also be schema-valid —
    they are copied/referenced during onboarding."""

    @pytest.mark.parametrize(
        "name",
        ["settings-full.json", "settings-telemetry-only.json"],
    )
    def test_template_conforms(self, name):
        path = REPO_ROOT / "config" / "hook-templates" / name
        data = json.loads(path.read_text(encoding="utf-8"))
        _assert_valid_hook_block(data["hooks"])
