"""Unit tests for the rate-chart inclusion predicate used by
/api/findings/by-day. Covers cross-session exclusion and info-noise
exclusion on both severity and finding_type axes.
"""
import sys
import types
from unittest.mock import MagicMock

import pytest

# Install the same qdrant/fastembed + redis stubs used elsewhere before
# importing anything from dashboard.api (which imports observability).
from tests.test_filter_parsing import _install_stubs
_install_stubs()

_redis_mod = types.ModuleType("redis")
_redis_mod.Redis = MagicMock
sys.modules.setdefault("redis", _redis_mod)

from dashboard.api.main import _rate_chart_includes  # noqa: E402


VALID_SESSION = "abcdef12-3456-7890-abcd-ef1234567890"


def _finding(**overrides):
    base = {
        "target_session": VALID_SESSION,
        "severity": "high",
        "finding_type": "violation",
        "auditor_type": "safety",
    }
    base.update(overrides)
    return base


class TestRateChartIncludes:
    """Predicate gates the numerator of /api/findings/by-day."""

    def test_includes_normal_finding(self):
        assert _rate_chart_includes(_finding()) is True

    def test_excludes_info_severity(self):
        assert _rate_chart_includes(_finding(severity="info")) is False

    def test_includes_info_finding_type_with_nonzero_severity(self):
        """Director synthesis findings use finding_type=info but may have
        medium/high severity. The severity-only filter keeps them."""
        assert _rate_chart_includes(
            _finding(finding_type="info", severity="medium"),
        ) is True
        assert _rate_chart_includes(
            _finding(finding_type="info", severity="high"),
        ) is True

    def test_excludes_when_both_axes_are_info(self):
        assert _rate_chart_includes(
            _finding(severity="info", finding_type="info"),
        ) is False

    def test_excludes_cross_session_null_target(self):
        assert _rate_chart_includes(_finding(target_session=None)) is False

    def test_excludes_cross_session_empty_target(self):
        assert _rate_chart_includes(_finding(target_session="")) is False

    def test_excludes_cross_session_string_placeholder(self):
        assert _rate_chart_includes(_finding(target_session="cross-session")) is False

    def test_includes_low_severity(self):
        """Low is rate-relevant, only info is noise."""
        assert _rate_chart_includes(_finding(severity="low")) is True

    def test_includes_trend_finding_type(self):
        assert _rate_chart_includes(_finding(finding_type="trend")) is True
