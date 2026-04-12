"""Unit tests for QdrantBackend.compare_windows() using a mocked QdrantClient."""
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Stubs — reuse the install helper from test_filter_parsing, then extend
# ---------------------------------------------------------------------------

def _ensure_stubs():
    if "qdrant_client" not in sys.modules:
        from tests.test_filter_parsing import _install_stubs
        _install_stubs()
    # Ensure OrderBy and Direction are in the models stub
    models_mod = sys.modules["qdrant_client.models"]
    if not hasattr(models_mod, "OrderBy"):
        class _OrderBy:
            def __init__(self, *, key, direction=None):
                self.key = key
                self.direction = direction

        class _Direction:
            DESC = "desc"
            ASC = "asc"

        models_mod.OrderBy = _OrderBy
        models_mod.Direction = _Direction


_ensure_stubs()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_point(id_val, timestamp_epoch=None, **extra_payload):
    p = MagicMock()
    p.id = id_val
    p.payload = {"timestamp_epoch": timestamp_epoch, **extra_payload} if timestamp_epoch is not None else extra_payload
    return p


def _make_count_response(n: int):
    r = MagicMock()
    r.count = n
    return r


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_client():
    client = MagicMock()
    client.get_collections.return_value.collections = []
    return client


@pytest.fixture()
def backend(mock_client):
    from observability import qdrant_backend as mod

    instance = mod.QdrantBackend.__new__(mod.QdrantBackend)
    instance._client = mock_client
    instance._embedder = MagicMock()
    # Make _embed return a fixed vector
    instance._embed = MagicMock(return_value=[0.1] * 384)
    return instance


# ---------------------------------------------------------------------------
# Tests — calendar-based ("days")
# ---------------------------------------------------------------------------

class TestCompareWindowsDays:
    def test_returns_correct_structure(self, backend, mock_client):
        """Result contains all required top-level keys."""
        # search_similar -> query_points
        qp_result = MagicMock()
        qp_result.points = []
        mock_client.query_points.return_value = qp_result

        mock_client.count.side_effect = [
            _make_count_response(10),  # recent count
            _make_count_response(4),   # prior count
        ]

        result = backend.compare_windows(
            collection="tool_calls",
            query_text="error in auth",
            window_type="days",
            window_size=7,
        )

        assert result["collection"] == "tool_calls"
        assert result["query"] == "error in auth"
        assert result["window_type"] == "days"
        assert result["window_size"] == 7
        assert "recent" in result
        assert "prior" in result
        assert "delta" in result

    def test_delta_count_change_and_ratio(self, backend, mock_client):
        """Delta is computed correctly from recent and prior counts."""
        qp_result = MagicMock()
        qp_result.points = []
        mock_client.query_points.return_value = qp_result

        mock_client.count.side_effect = [
            _make_count_response(8),   # recent
            _make_count_response(4),   # prior
        ]

        result = backend.compare_windows(
            collection="tool_calls",
            query_text="timeout failures",
            window_type="days",
            window_size=7,
        )

        assert result["delta"]["count_change"] == 4
        assert result["delta"]["count_ratio"] == pytest.approx(2.0)

    def test_prior_zero_yields_null_ratio(self, backend, mock_client):
        """count_ratio is None when prior count is 0."""
        qp_result = MagicMock()
        qp_result.points = []
        mock_client.query_points.return_value = qp_result

        mock_client.count.side_effect = [
            _make_count_response(5),   # recent
            _make_count_response(0),   # prior
        ]

        result = backend.compare_windows(
            collection="hallucinations",
            query_text="wrong answer",
            window_type="days",
            window_size=3,
        )

        assert result["delta"]["count_ratio"] is None
        assert result["delta"]["count_change"] == 5

    def test_range_fields_are_iso_strings(self, backend, mock_client):
        """recent.range and prior.range are lists of two ISO date strings."""
        qp_result = MagicMock()
        qp_result.points = []
        mock_client.query_points.return_value = qp_result
        mock_client.count.side_effect = [_make_count_response(1), _make_count_response(1)]

        result = backend.compare_windows(
            collection="evals",
            query_text="test query",
            window_type="days",
            window_size=7,
        )

        assert len(result["recent"]["range"]) == 2
        assert len(result["prior"]["range"]) == 2
        # Should be parseable ISO strings
        from datetime import datetime
        for ts in result["recent"]["range"] + result["prior"]["range"]:
            datetime.fromisoformat(ts)  # raises if not valid

    def test_search_similar_called_with_window_filters(self, backend, mock_client):
        """query_points is called with timestamp range filter for each window."""
        qp_result = MagicMock()
        qp_result.points = []
        mock_client.query_points.return_value = qp_result
        mock_client.count.side_effect = [_make_count_response(0), _make_count_response(0)]

        backend.compare_windows(
            collection="tool_calls",
            query_text="query",
            window_type="days",
            window_size=7,
        )

        # Two calls to query_points (recent + prior)
        assert mock_client.query_points.call_count == 2


# ---------------------------------------------------------------------------
# Tests — session-based ("sessions")
# ---------------------------------------------------------------------------

class TestCompareWindowsSessions:
    def _make_scroll_result(self, session_ids):
        """Build a (points, next_offset) tuple like real scroll returns."""
        points = [_make_point(sid, timestamp_epoch=float(i * 100)) for i, sid in enumerate(session_ids)]
        return (points, None)

    def test_splits_session_ids_correctly(self, backend, mock_client):
        """Recent = first half of fetched sessions, prior = second half."""
        session_ids = ["s1", "s2", "s3", "s4"]
        mock_client.scroll.return_value = self._make_scroll_result(session_ids)

        qp_result = MagicMock()
        qp_result.points = []
        mock_client.query_points.return_value = qp_result
        mock_client.count.side_effect = [_make_count_response(3), _make_count_response(2)]

        result = backend.compare_windows(
            collection="tool_calls",
            query_text="test",
            window_type="sessions",
            window_size=2,
        )

        assert result["recent"]["range"] == ["s1", "s2"]
        assert result["prior"]["range"] == ["s3", "s4"]

    def test_session_scroll_uses_order_by_desc(self, backend, mock_client):
        """scroll() is called with OrderBy timestamp_epoch DESC."""
        mock_client.scroll.return_value = ([], None)
        mock_client.count.return_value = _make_count_response(0)
        qp_result = MagicMock()
        qp_result.points = []
        mock_client.query_points.return_value = qp_result

        backend.compare_windows(
            collection="tool_calls",
            query_text="test",
            window_type="sessions",
            window_size=3,
        )

        scroll_kwargs = mock_client.scroll.call_args.kwargs
        assert scroll_kwargs["collection_name"] == "sessions"
        assert scroll_kwargs["limit"] == 6  # 2 * window_size
        order = scroll_kwargs["order_by"]
        assert order.key == "timestamp_epoch"
        from observability.qdrant_backend import models as _models
        assert order.direction == _models.Direction.DESC

    def test_delta_computed_from_session_counts(self, backend, mock_client):
        """Delta fields are populated from session window counts."""
        session_ids = ["a", "b", "c", "d"]
        mock_client.scroll.return_value = self._make_scroll_result(session_ids)

        qp_result = MagicMock()
        qp_result.points = []
        mock_client.query_points.return_value = qp_result
        mock_client.count.side_effect = [_make_count_response(6), _make_count_response(2)]

        result = backend.compare_windows(
            collection="hallucinations",
            query_text="hallucination test",
            window_type="sessions",
            window_size=2,
        )

        assert result["delta"]["count_change"] == 4
        assert result["delta"]["count_ratio"] == pytest.approx(3.0)

    def test_prior_zero_sessions_yields_null_ratio(self, backend, mock_client):
        """count_ratio is None when prior session window has 0 events."""
        # Only 1 session -> recent gets it, prior is empty
        session_ids = ["only-one"]
        mock_client.scroll.return_value = self._make_scroll_result(session_ids)

        qp_result = MagicMock()
        qp_result.points = []
        mock_client.query_points.return_value = qp_result
        # count only called for recent (prior has no IDs, returns 0 directly)
        mock_client.count.return_value = _make_count_response(5)

        result = backend.compare_windows(
            collection="tool_calls",
            query_text="test",
            window_type="sessions",
            window_size=2,  # requests 4 sessions, only 1 available
        )

        assert result["delta"]["count_ratio"] is None

    def test_project_filter_passed_to_session_scroll(self, backend, mock_client):
        """When filters include 'project', it is forwarded to sessions scroll."""
        mock_client.scroll.return_value = ([], None)
        mock_client.count.return_value = _make_count_response(0)
        qp_result = MagicMock()
        qp_result.points = []
        mock_client.query_points.return_value = qp_result

        backend.compare_windows(
            collection="tool_calls",
            query_text="test",
            window_type="sessions",
            window_size=2,
            filters={"project": "proj-alpha"},
        )

        scroll_kwargs = mock_client.scroll.call_args.kwargs
        scroll_filter = scroll_kwargs["scroll_filter"]
        assert scroll_filter is not None
        must_conds = scroll_filter.must or []
        assert any(c.key == "project" and c.match.value == "proj-alpha" for c in must_conds)


class TestCompareWindowsInvalidType:
    def test_unknown_window_type_raises(self, backend):
        with pytest.raises(ValueError, match="Unknown window_type"):
            backend.compare_windows(
                collection="tool_calls",
                query_text="test",
                window_type="weeks",
                window_size=2,
            )
