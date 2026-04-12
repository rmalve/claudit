"""Unit tests for QdrantBackend.timeline() using a mocked QdrantClient."""
import sys
import types
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

def _ensure_stubs():
    if "qdrant_client" not in sys.modules:
        from tests.test_filter_parsing import _install_stubs
        _install_stubs()
    # Ensure OrderBy and Direction are available (may already be added by
    # test_compare_windows if both test files run in the same process)
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

def _make_search_hit(score: float, payload: dict) -> MagicMock:
    """Simulate a result item returned by search_similar()."""
    return {
        "score": score,
        "payload": payload,
        "text": payload.get("_text", ""),
    }


def _make_scroll_point(payload: dict) -> MagicMock:
    p = MagicMock()
    p.id = payload.get("session_id", "unknown")
    p.payload = payload
    return p


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
    instance._embed = MagicMock(return_value=[0.1] * 384)
    return instance


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTimelineBasic:
    def test_basic_timeline_three_collections(self, backend, mock_client):
        """timeline() merges events from 3 collections sorted by timestamp."""
        anchor_epoch = 1_000_000.0
        anchor_payload = {"timestamp_epoch": anchor_epoch, "_text": "auth error"}

        # search_similar (query_points) returns anchor from first collection
        anchor_point = MagicMock()
        anchor_point.score = 0.95
        anchor_point.payload = anchor_payload
        qp_result = MagicMock()
        qp_result.points = [anchor_point]
        mock_client.query_points.return_value = qp_result

        # scroll returns events for each collection (3 collections)
        def _scroll_side_effect(**kwargs):
            col = kwargs["collection_name"]
            if col == "tool_calls":
                pts = [
                    _make_scroll_point({"timestamp_epoch": anchor_epoch - 60, "action": "call"}),
                    _make_scroll_point({"timestamp_epoch": anchor_epoch + 60, "action": "response"}),
                ]
            elif col == "hallucinations":
                pts = [
                    _make_scroll_point({"timestamp_epoch": anchor_epoch + 30, "msg": "hallucination"}),
                ]
            else:  # evals
                pts = [
                    _make_scroll_point({"timestamp_epoch": anchor_epoch - 30, "result": "pass"}),
                ]
            return (pts, None)

        mock_client.scroll.side_effect = _scroll_side_effect

        result = backend.timeline(
            query_text="auth error",
            collections=["tool_calls", "hallucinations", "evals"],
        )

        assert "error" not in result
        assert result["query"] == "auth error"
        assert result["anchor"]["collection"] == "tool_calls"
        assert result["anchor"]["score"] == pytest.approx(0.95)

        tl = result["timeline"]
        assert len(tl) == 4  # 2 from tool_calls + 1 hallucination + 1 eval

        # Sorted by timestamp_epoch ascending
        epochs = [e["timestamp_epoch"] for e in tl]
        assert epochs == sorted(epochs)

        # Each event tagged with its collection
        collections_in_timeline = {e["collection"] for e in tl}
        assert collections_in_timeline == {"tool_calls", "hallucinations", "evals"}

    def test_anchor_marked_is_anchor_true(self, backend, mock_client):
        """The anchor event in timeline has is_anchor=True."""
        anchor_epoch = 5_000.0
        anchor_payload = {"timestamp_epoch": anchor_epoch, "_text": "target event"}

        anchor_point = MagicMock()
        anchor_point.score = 0.9
        anchor_point.payload = anchor_payload
        qp_result = MagicMock()
        qp_result.points = [anchor_point]
        mock_client.query_points.return_value = qp_result

        mock_client.scroll.return_value = (
            [_make_scroll_point({"timestamp_epoch": anchor_epoch})],
            None,
        )

        result = backend.timeline(
            query_text="target event",
            collections=["tool_calls"],
        )

        anchor_events = [e for e in result["timeline"] if e.get("is_anchor")]
        assert len(anchor_events) == 1
        assert anchor_events[0]["collection"] == "tool_calls"

    def test_counts_by_collection_populated(self, backend, mock_client):
        """counts_by_collection has one entry per collection."""
        anchor_epoch = 1_000.0
        anchor_point = MagicMock()
        anchor_point.score = 0.8
        anchor_point.payload = {"timestamp_epoch": anchor_epoch}
        qp_result = MagicMock()
        qp_result.points = [anchor_point]
        mock_client.query_points.return_value = qp_result

        def _scroll(**kwargs):
            col = kwargs["collection_name"]
            pts = [
                _make_scroll_point({"timestamp_epoch": anchor_epoch + i * 10})
                for i in range(3 if col == "tool_calls" else 1)
            ]
            return (pts, None)

        mock_client.scroll.side_effect = _scroll

        result = backend.timeline(
            query_text="test",
            collections=["tool_calls", "evals"],
        )

        cbc = result["counts_by_collection"]
        assert "tool_calls" in cbc
        assert "evals" in cbc
        assert cbc["tool_calls"] == 3
        assert cbc["evals"] == 1


class TestTimelineNoAnchor:
    def test_no_anchor_found_returns_error(self, backend, mock_client):
        """When search returns no results, an error dict is returned."""
        qp_result = MagicMock()
        qp_result.points = []
        mock_client.query_points.return_value = qp_result

        result = backend.timeline(
            query_text="something rare",
            collections=["tool_calls", "evals"],
        )

        assert "error" in result
        assert "tool_calls" in result["error"]

    def test_anchor_missing_timestamp_epoch_returns_error(self, backend, mock_client):
        """When anchor payload lacks timestamp_epoch, backfill error is returned."""
        anchor_point = MagicMock()
        anchor_point.score = 0.7
        anchor_point.payload = {"_text": "some event"}  # no timestamp_epoch
        qp_result = MagicMock()
        qp_result.points = [anchor_point]
        mock_client.query_points.return_value = qp_result

        result = backend.timeline(
            query_text="some event",
            collections=["tool_calls"],
        )

        assert "error" in result
        assert "timestamp_epoch" in result["error"]
        assert "backfill" in result["error"]


class TestTimelineAnchorCollection:
    def test_defaults_anchor_to_first_collection(self, backend, mock_client):
        """anchor_collection defaults to the first item in collections list."""
        anchor_point = MagicMock()
        anchor_point.score = 0.85
        anchor_point.payload = {"timestamp_epoch": 2_000.0}
        qp_result = MagicMock()
        qp_result.points = [anchor_point]
        mock_client.query_points.return_value = qp_result
        mock_client.scroll.return_value = ([], None)

        result = backend.timeline(
            query_text="test",
            collections=["hallucinations", "evals", "tool_calls"],
            # anchor_collection not specified
        )

        assert result["anchor"]["collection"] == "hallucinations"

    def test_explicit_anchor_collection_used(self, backend, mock_client):
        """When anchor_collection is provided, it overrides the default."""
        anchor_point = MagicMock()
        anchor_point.score = 0.75
        anchor_point.payload = {"timestamp_epoch": 3_000.0}
        qp_result = MagicMock()
        qp_result.points = [anchor_point]
        mock_client.query_points.return_value = qp_result
        mock_client.scroll.return_value = ([], None)

        result = backend.timeline(
            query_text="test",
            collections=["tool_calls", "evals", "sessions"],
            anchor_collection="evals",
        )

        assert result["anchor"]["collection"] == "evals"


class TestTimelineLimitPerCollection:
    def test_limit_per_collection_caps_results(self, backend, mock_client):
        """limit_per_collection caps the number of events kept per collection."""
        anchor_epoch = 10_000.0
        anchor_point = MagicMock()
        anchor_point.score = 0.9
        anchor_point.payload = {"timestamp_epoch": anchor_epoch}
        qp_result = MagicMock()
        qp_result.points = [anchor_point]
        mock_client.query_points.return_value = qp_result

        # scroll returns 20 points for the collection
        def _scroll(**kwargs):
            pts = [
                _make_scroll_point({"timestamp_epoch": anchor_epoch + i})
                for i in range(20)
            ]
            return (pts, None)

        mock_client.scroll.side_effect = _scroll

        result = backend.timeline(
            query_text="test",
            collections=["tool_calls"],
            limit_per_collection=3,
        )

        # Only 3 results should survive
        assert result["counts_by_collection"]["tool_calls"] == 3
        assert len(result["timeline"]) == 3

    def test_keeps_events_closest_to_anchor(self, backend, mock_client):
        """Of all scrolled events, the closest to anchor epoch are kept."""
        anchor_epoch = 1_000.0
        anchor_point = MagicMock()
        anchor_point.score = 0.9
        anchor_point.payload = {"timestamp_epoch": anchor_epoch}
        qp_result = MagicMock()
        qp_result.points = [anchor_point]
        mock_client.query_points.return_value = qp_result

        # 5 events at varying distances; we want only the 2 closest
        events = [
            {"timestamp_epoch": anchor_epoch + 5},    # dist=5  <- keep
            {"timestamp_epoch": anchor_epoch + 100},  # dist=100
            {"timestamp_epoch": anchor_epoch - 3},    # dist=3  <- keep
            {"timestamp_epoch": anchor_epoch + 200},  # dist=200
            {"timestamp_epoch": anchor_epoch + 50},   # dist=50
        ]
        mock_client.scroll.return_value = (
            [_make_scroll_point(e) for e in events],
            None,
        )

        result = backend.timeline(
            query_text="test",
            collections=["tool_calls"],
            limit_per_collection=2,
        )

        kept_epochs = {e["timestamp_epoch"] for e in result["timeline"]}
        assert anchor_epoch + 5 in kept_epochs
        assert anchor_epoch - 3 in kept_epochs


class TestTimelineWindowRange:
    def test_window_start_end_in_result(self, backend, mock_client):
        """Result includes window.start and window.end as ISO strings."""
        anchor_epoch = 10_000.0
        anchor_point = MagicMock()
        anchor_point.score = 0.9
        anchor_point.payload = {"timestamp_epoch": anchor_epoch}
        qp_result = MagicMock()
        qp_result.points = [anchor_point]
        mock_client.query_points.return_value = qp_result
        mock_client.scroll.return_value = ([], None)

        result = backend.timeline(
            query_text="test",
            collections=["tool_calls"],
            time_window_minutes=60,
        )

        from datetime import datetime
        assert "window" in result
        datetime.fromisoformat(result["window"]["start"])
        datetime.fromisoformat(result["window"]["end"])

        # window spans ±30 minutes around anchor
        from datetime import timezone
        start_epoch = datetime.fromisoformat(result["window"]["start"]).timestamp()
        end_epoch = datetime.fromisoformat(result["window"]["end"]).timestamp()
        assert abs(start_epoch - (anchor_epoch - 1800)) < 1
        assert abs(end_epoch - (anchor_epoch + 1800)) < 1
