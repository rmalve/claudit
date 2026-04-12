"""Unit tests for QdrantBackend.count() using a mocked QdrantClient."""
import sys
import types
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Lightweight stubs — shared with test_filter_parsing.py approach
# ---------------------------------------------------------------------------

def _ensure_stubs():
    """Install stubs if not already present (safe to call multiple times)."""
    if "qdrant_client" not in sys.modules:
        from tests.test_filter_parsing import _install_stubs
        _install_stubs()


_ensure_stubs()

# After stubs are in place, import what we need
from observability.qdrant_backend import build_query_filter  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_client():
    """A MagicMock that impersonates QdrantClient well enough for count()."""
    client = MagicMock()

    # get_collections() must return something iterable
    client.get_collections.return_value.collections = []

    # Default count response
    count_response = MagicMock()
    count_response.count = 42
    client.count.return_value = count_response

    return client


@pytest.fixture()
def backend(mock_client):
    """QdrantBackend with all heavy init bypassed."""
    from observability import qdrant_backend as mod

    with (
        patch.object(mod, "QdrantClient", return_value=mock_client),
        patch.object(mod, "TextEmbedding", return_value=MagicMock()),
    ):
        instance = mod.QdrantBackend.__new__(mod.QdrantBackend)
        instance._client = mock_client
        instance._embedder = MagicMock()
        yield instance


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCount:
    def test_count_no_filters(self, backend, mock_client):
        result = backend.count("tool_calls")
        assert result == 42
        mock_client.count.assert_called_once_with(
            collection_name="tool_calls",
            count_filter=None,
            exact=True,
        )

    def test_count_with_equality_filter(self, backend, mock_client):
        mock_client.count.return_value.count = 7

        result = backend.count("hallucinations", filters={"agent": "code-agent"})
        assert result == 7

        call_kwargs = mock_client.count.call_args.kwargs
        assert call_kwargs["collection_name"] == "hallucinations"
        assert call_kwargs["exact"] is True

        # The count_filter must be a real Filter, not None
        count_filter = call_kwargs["count_filter"]
        assert count_filter is not None
        # Verify the must condition references the correct field/value
        must_conds = count_filter.must or []
        assert any(c.key == "agent" and c.match.value == "code-agent" for c in must_conds)

    def test_count_with_range_filter(self, backend, mock_client):
        mock_client.count.return_value.count = 15

        result = backend.count(
            "evals",
            filters={"timestamp_epoch__gte": 1000.0, "timestamp_epoch__lte": 5000.0},
        )
        assert result == 15

        call_kwargs = mock_client.count.call_args.kwargs
        count_filter = call_kwargs["count_filter"]
        assert count_filter is not None

        must_conds = count_filter.must or []
        range_conds = [c for c in must_conds if c.key == "timestamp_epoch"]
        assert len(range_conds) == 1
        r = range_conds[0].range
        assert r.gte == 1000.0
        assert r.lte == 5000.0
