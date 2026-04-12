"""Unit tests for build_query_filter() in observability/qdrant_backend.py.

These tests import and call build_query_filter() directly — no QdrantClient
or fastembed is required, so they run without any running services.
"""
import sys
import types
import importlib
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Lightweight stubs so qdrant_backend.py can be imported without real deps
# ---------------------------------------------------------------------------

def _make_qdrant_models_stub():
    """Return a stub module that mimics the qdrant_client.models API."""
    mod = types.ModuleType("qdrant_client.models")

    class _MatchValue:
        def __init__(self, *, value):
            self.value = value
        def __repr__(self):
            return f"MatchValue(value={self.value!r})"

    class _MatchAny:
        def __init__(self, *, any):
            self.any = any
        def __repr__(self):
            return f"MatchAny(any={self.any!r})"

    class _Range:
        def __init__(self, *, gte=None, lte=None, gt=None, lt=None):
            self.gte = gte
            self.lte = lte
            self.gt = gt
            self.lt = lt
        def __repr__(self):
            return (
                f"Range(gte={self.gte!r}, lte={self.lte!r},"
                f" gt={self.gt!r}, lt={self.lt!r})"
            )

    class _FieldCondition:
        def __init__(self, *, key, match=None, range=None):
            self.key = key
            self.match = match
            self.range = range
        def __repr__(self):
            return (
                f"FieldCondition(key={self.key!r},"
                f" match={self.match!r}, range={self.range!r})"
            )

    class _Filter:
        def __init__(self, *, must=None, must_not=None):
            self.must = must
            self.must_not = must_not
        def __repr__(self):
            return f"Filter(must={self.must!r}, must_not={self.must_not!r})"

    class _PayloadSchemaType:
        FLOAT = "float"
        KEYWORD = "keyword"
        BOOL = "bool"
        INTEGER = "integer"

    class _Distance:
        COSINE = "Cosine"

    class _VectorParams:
        def __init__(self, *, size, distance):
            self.size = size
            self.distance = distance

    mod.MatchValue = _MatchValue
    mod.MatchAny = _MatchAny
    mod.Range = _Range
    mod.FieldCondition = _FieldCondition
    mod.Filter = _Filter
    mod.PayloadSchemaType = _PayloadSchemaType
    mod.Distance = _Distance
    mod.VectorParams = _VectorParams
    mod.PointStruct = MagicMock
    return mod


def _install_stubs():
    """Install lightweight stubs for qdrant_client and fastembed."""
    # qdrant_client package
    qc_pkg = types.ModuleType("qdrant_client")
    models_mod = _make_qdrant_models_stub()
    qc_pkg.models = models_mod
    qc_pkg.QdrantClient = MagicMock
    sys.modules.setdefault("qdrant_client", qc_pkg)
    sys.modules.setdefault("qdrant_client.models", models_mod)

    # fastembed
    fe_pkg = types.ModuleType("fastembed")
    fe_pkg.TextEmbedding = MagicMock
    sys.modules.setdefault("fastembed", fe_pkg)


_install_stubs()

# Now safe to import the module under test
from observability.qdrant_backend import build_query_filter  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _must_conditions(f):
    return f.must or []


def _must_not_conditions(f):
    return f.must_not or []


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBuildQueryFilterReturnNone:
    def test_none_input(self):
        assert build_query_filter(None) is None

    def test_empty_dict(self):
        assert build_query_filter({}) is None


class TestEqualityFilter:
    def test_scalar_value_goes_to_must(self):
        f = build_query_filter({"status": "failure"})
        assert f is not None
        conds = _must_conditions(f)
        assert len(conds) == 1
        c = conds[0]
        assert c.key == "status"
        assert c.match.value == "failure"
        assert c.range is None

    def test_list_value_uses_match_any(self):
        f = build_query_filter({"agent": ["a", "b", "c"]})
        conds = _must_conditions(f)
        assert len(conds) == 1
        c = conds[0]
        assert c.key == "agent"
        assert c.match.any == ["a", "b", "c"]
        assert c.range is None


class TestRangeOperators:
    def test_gte(self):
        f = build_query_filter({"timestamp_epoch__gte": 100.0})
        conds = _must_conditions(f)
        assert len(conds) == 1
        c = conds[0]
        assert c.key == "timestamp_epoch"
        assert c.range.gte == 100.0
        assert c.range.lte is None
        assert c.range.gt is None
        assert c.range.lt is None

    def test_lte(self):
        f = build_query_filter({"timestamp_epoch__lte": 200.0})
        conds = _must_conditions(f)
        c = conds[0]
        assert c.key == "timestamp_epoch"
        assert c.range.lte == 200.0

    def test_gt(self):
        f = build_query_filter({"score__gt": 0.5})
        conds = _must_conditions(f)
        c = conds[0]
        assert c.key == "score"
        assert c.range.gt == 0.5

    def test_lt(self):
        f = build_query_filter({"score__lt": 1.0})
        conds = _must_conditions(f)
        c = conds[0]
        assert c.key == "score"
        assert c.range.lt == 1.0

    def test_multiple_range_ops_same_field_merged(self):
        f = build_query_filter({
            "timestamp_epoch__gte": 100.0,
            "timestamp_epoch__lte": 200.0,
        })
        conds = _must_conditions(f)
        # Both operators on same field must be merged into ONE Range condition
        range_conds = [c for c in conds if c.key == "timestamp_epoch"]
        assert len(range_conds) == 1
        r = range_conds[0].range
        assert r.gte == 100.0
        assert r.lte == 200.0


class TestNegationFilter:
    def test_ne_goes_to_must_not(self):
        f = build_query_filter({"status__ne": "archived"})
        assert _must_conditions(f) == [] or f.must is None
        mn = _must_not_conditions(f)
        assert len(mn) == 1
        c = mn[0]
        assert c.key == "status"
        assert c.match.value == "archived"


class TestCombinedFilters:
    def test_equality_plus_range_plus_negation(self):
        f = build_query_filter({
            "project": "proj-alpha",
            "timestamp_epoch__gte": 1000,
            "timestamp_epoch__lte": 9000,
            "status__ne": "deleted",
        })
        must = _must_conditions(f)
        must_not = _must_not_conditions(f)

        # Equality condition
        eq_conds = [c for c in must if c.key == "project"]
        assert len(eq_conds) == 1
        assert eq_conds[0].match.value == "proj-alpha"

        # Range condition (merged)
        range_conds = [c for c in must if c.key == "timestamp_epoch"]
        assert len(range_conds) == 1
        assert range_conds[0].range.gte == 1000
        assert range_conds[0].range.lte == 9000

        # Negation
        assert len(must_not) == 1
        assert must_not[0].key == "status"
        assert must_not[0].match.value == "deleted"
