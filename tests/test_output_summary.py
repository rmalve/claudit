"""Unit tests for build_output_summary.

Previously the function only matched {output, filePath, content, error, result,
matches} keys and returned None for Read/Bash/Glob/AskUserQuestion tool
responses — causing 38-100% null rates on those tools in live data. The
rewrite adds more keys plus a JSON-dump fallback so no non-null response
is silently dropped.
"""
import sys
import types
from unittest.mock import MagicMock

# Stub transitive imports before importing the hook module (observability
# package pulls in qdrant/fastembed/redis otherwise).
from tests.test_filter_parsing import _install_stubs
_install_stubs()

_redis_mod = types.ModuleType("redis")
_redis_mod.Redis = MagicMock
sys.modules.setdefault("redis", _redis_mod)

from observability.hooks.post_tool_use import build_output_summary  # noqa: E402


def _payload(tool_response) -> dict:
    return {"tool_response": tool_response}


class TestBuildOutputSummary:
    """Each real-world tool_response shape should produce a non-null summary
    unless the response itself is empty/None."""

    # --- null / empty ---

    def test_returns_none_when_tool_response_missing(self):
        assert build_output_summary({}) is None

    def test_returns_none_when_tool_response_none(self):
        assert build_output_summary(_payload(None)) is None

    def test_returns_none_for_empty_string(self):
        assert build_output_summary(_payload("")) is None

    def test_returns_none_for_empty_list(self):
        assert build_output_summary(_payload([])) is None

    # --- each tool family ---

    def test_read_string_response(self):
        """Read tool sometimes sends the file contents as a plain string."""
        result = build_output_summary(_payload("def foo():\n    return 42\n"))
        assert result is not None
        assert "def foo" in result

    def test_read_dict_with_content(self):
        """Read may also wrap content in a dict."""
        result = build_output_summary(_payload({"content": "hello world"}))
        assert result is not None
        assert "hello world" in result

    def test_bash_stdout_stderr(self):
        """Bash responses carry stdout/stderr keys (previously unmatched)."""
        resp = {"stdout": "hello", "stderr": "warning: x", "exitCode": 0}
        result = build_output_summary(_payload(resp))
        assert result is not None
        assert "stdout" in result and "stderr" in result
        assert "hello" in result

    def test_glob_list_of_files(self):
        """Glob returns a list of paths (previously hit the dict branch, got None)."""
        files = ["src/a.py", "src/b.py", "src/c.py"]
        result = build_output_summary(_payload(files))
        assert result is not None
        assert "a.py" in result

    def test_askuserquestion_answer_key(self):
        """AskUserQuestion carries user input in 'answer' (previously unmatched)."""
        resp = {"answer": "Option B", "reasoning": "because of X"}
        result = build_output_summary(_payload(resp))
        assert result is not None
        assert "Option B" in result

    def test_write_edit_filePath(self):
        """Regression guard: the originally-matched keys still work."""
        result = build_output_summary(_payload({"filePath": "/tmp/x.py"}))
        assert result is not None
        assert "/tmp/x.py" in result

    # --- fallback for unknown shapes ---

    def test_unknown_dict_shape_uses_json_fallback(self):
        """Novel response shapes no longer get silently dropped — JSON-dumped instead."""
        resp = {"custom_field": "some value", "other": [1, 2, 3]}
        result = build_output_summary(_payload(resp))
        assert result is not None
        assert "custom_field" in result

    def test_truncation_at_500_chars(self):
        """All branches cap at ~500 chars for QDrant semantic-search sanity."""
        big = "x" * 10000
        result = build_output_summary(_payload(big))
        assert result is not None
        assert len(result) <= 500
