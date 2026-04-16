"""
Qdrant vector store backend for semantic telemetry.

Stores tool calls, hallucinations, agent spawns, evals, and session summaries
as embedded vectors for similarity search and failure clustering.

Uses fastembed for local embedding (BAAI/bge-small-en-v1.5, 384 dimensions).
Defaults to local Docker server with embedded fallback.
"""

import logging
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from fastembed import TextEmbedding
from qdrant_client import QdrantClient, models

logger = logging.getLogger(__name__)

# Collection names
TOOL_CALLS = "tool_calls"
HALLUCINATIONS = "hallucinations"
AGENT_SPAWNS = "agent_spawns"
EVALS = "evals"
SESSIONS = "sessions"
PROMPTS = "prompts"
CODE_CHANGES = "code_changes"
BUGS = "bugs"
FINDINGS = "findings"
DATA_QUALITY = "data_quality"
SESSION_TIMELINES = "session_timelines"
CONVERSATION_TURNS = "conversation_turns"

ALL_COLLECTIONS = [
    TOOL_CALLS, HALLUCINATIONS, AGENT_SPAWNS, EVALS, SESSIONS,
    PROMPTS, CODE_CHANGES, BUGS, FINDINGS, DATA_QUALITY,
    SESSION_TIMELINES, CONVERSATION_TURNS,
]

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384

# Recognized range operator suffixes
_RANGE_OPS = {"__gte", "__lte", "__gt", "__lt"}

# Words too common to be useful in cluster labels
_STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "must", "ought",
    "in", "on", "at", "to", "for", "of", "with", "by", "from", "as",
    "into", "through", "during", "before", "after", "above", "below",
    "and", "but", "or", "nor", "not", "no", "so", "if", "then", "than",
    "that", "this", "these", "those", "it", "its", "all", "each", "every",
    "both", "few", "more", "most", "other", "some", "such", "only",
    "same", "also", "very", "just", "about", "between", "over", "under",
    "finding", "findings", "found", "detected", "observed", "noted",
    "session", "agent", "across", "which", "when", "where", "how",
}

_AUDITOR_DISPLAY = {
    "trace": "Trace",
    "safety": "Safety",
    "policy": "Policy",
    "hallucination": "Hallucination",
    "drift": "Drift",
    "cost": "Cost",
}


_CLUSTER_CATEGORY = {
    ("trace", "violation"): "Sequence Violations",
    ("trace", "anomaly"): "Trace Anomalies",
    ("trace", "trend"): "Behavioral Trends",
    ("trace", "info"): "Session Reconstruction",
    ("safety", "violation"): "Security Violations",
    ("safety", "anomaly"): "Safety Concerns",
    ("safety", "trend"): "Safety Trends",
    ("safety", "info"): "Safety Review",
    ("policy", "violation"): "Policy Violations",
    ("policy", "anomaly"): "Governance Anomalies",
    ("policy", "trend"): "Compliance Trends",
    ("policy", "info"): "Policy Compliance",
    ("hallucination", "violation"): "Factual Errors",
    ("hallucination", "anomaly"): "Hallucination Risks",
    ("hallucination", "trend"): "Accuracy Trends",
    ("hallucination", "info"): "Factual Verification",
    ("drift", "violation"): "Drift Violations",
    ("drift", "anomaly"): "Behavioral Drift",
    ("drift", "trend"): "Drift Patterns",
    ("drift", "info"): "Baseline Analysis",
    ("cost", "violation"): "Efficiency Violations",
    ("cost", "anomaly"): "Cost Anomalies",
    ("cost", "trend"): "Efficiency Trends",
    ("cost", "info"): "Cost Analysis",
}


def _build_cluster_label(claims: list[str], auditor: str, finding_type: str) -> str:
    """Build a concise chart label from auditor type and finding type."""
    key = (auditor.lower(), finding_type.lower())
    if key in _CLUSTER_CATEGORY:
        return _CLUSTER_CATEGORY[key]

    prefix = _AUDITOR_DISPLAY.get(auditor, auditor.title())
    suffix = finding_type.title() if finding_type else "General"
    return f"{prefix}: {suffix}"


def build_query_filter(filters: dict | None) -> "models.Filter | None":
    """Build a Qdrant Filter from a Django-style filter dict.

    Supported key suffixes:
      (none)   -> MatchValue (scalar) or MatchAny (list)  in must
      __gte    -> Range(gte=N)   in must
      __lte    -> Range(lte=N)   in must
      __gt     -> Range(gt=N)    in must
      __lt     -> Range(lt=N)    in must
      __ne     -> MatchValue     in must_not

    Multiple range operators for the same field are merged into one Range condition.
    Returns None for None or empty-dict input.
    """
    if not filters:
        return None

    must: list = []
    must_not: list = []

    # Collect range kwargs keyed by field name so we can merge them
    range_accum: dict[str, dict] = {}

    for key, value in filters.items():
        if key.endswith("__ne"):
            field = key[:-4]
            must_not.append(
                models.FieldCondition(key=field, match=models.MatchValue(value=value))
            )
        elif any(key.endswith(op) for op in _RANGE_OPS):
            for op in _RANGE_OPS:
                if key.endswith(op):
                    field = key[: -len(op)]
                    kwarg = op.lstrip("_")  # "gte", "lte", "gt", "lt"
                    range_accum.setdefault(field, {})[kwarg] = value
                    break
        elif isinstance(value, list):
            must.append(
                models.FieldCondition(key=key, match=models.MatchAny(any=value))
            )
        else:
            must.append(
                models.FieldCondition(key=key, match=models.MatchValue(value=value))
            )

    # Flush accumulated range conditions
    for field, kwargs in range_accum.items():
        must.append(models.FieldCondition(key=field, range=models.Range(**kwargs)))

    if not must and not must_not:
        return None

    return models.Filter(
        must=must or None,
        must_not=must_not or None,
    )


class QdrantBackend:
    """Qdrant-backed semantic telemetry store.

    Supports Docker server mode (default) with embedded fallback.
    Uses fastembed for local text embedding — no external API calls.
    """

    def __init__(self, url: str | None = None, path: str | None = None):
        """Initialize Qdrant client.

        Priority: explicit url > env QDRANT_URL > explicit path > embedded fallback.
        Default: connects to local Docker server at localhost:6333.
        """
        server_url = url or os.environ.get("QDRANT_URL", "http://localhost:6333")

        if server_url:
            try:
                self._client = QdrantClient(url=server_url, timeout=5)
                self._client.get_collections()
                logger.info("Qdrant connected to server: %s", server_url)
            except Exception:
                storage_path = path or os.path.expanduser(
                    os.environ.get("QDRANT_PATH", "~/.claude/observability/qdrant")
                )
                self._client = QdrantClient(path=storage_path)
                logger.warning(
                    "Qdrant server at %s unreachable, falling back to embedded at %s",
                    server_url, storage_path,
                )
        else:
            storage_path = path or os.path.expanduser(
                os.environ.get("QDRANT_PATH", "~/.claude/observability/qdrant")
            )
            self._client = QdrantClient(path=storage_path)
            logger.info("Qdrant running embedded at: %s", storage_path)

        self._fastembed_cache_dir = os.path.expanduser(
            os.environ.get("FASTEMBED_CACHE_PATH", "~/.claude/observability/fastembed_cache")
        )
        # Lazy: only loaded when an embedding is actually needed. Many callers
        # (scroll_all, count, query-only flows) never touch the embedder, so
        # skipping the 2-5s ONNX model load at construction time is a meaningful
        # win — especially for orchestrator subprocesses and the dashboard API.
        self._embedder: TextEmbedding | None = None
        self._ensure_collections()

    def _get_embedder(self) -> TextEmbedding:
        if self._embedder is None:
            self._embedder = TextEmbedding(
                EMBEDDING_MODEL, cache_dir=self._fastembed_cache_dir
            )
        return self._embedder

    # Payload fields and their index types
    _PAYLOAD_INDEXES: list[tuple[str, Any]] = [
        ("timestamp_epoch", models.PayloadSchemaType.FLOAT),
        ("agent",           models.PayloadSchemaType.KEYWORD),
        ("project",         models.PayloadSchemaType.KEYWORD),
        ("session_id",      models.PayloadSchemaType.KEYWORD),
        ("status",          models.PayloadSchemaType.KEYWORD),
        ("severity",        models.PayloadSchemaType.KEYWORD),
        ("audited",         models.PayloadSchemaType.BOOL),
    ]

    def _ensure_collections(self) -> None:
        """Create collections and payload indexes if they don't exist."""
        existing = {c.name for c in self._client.get_collections().collections}
        for name in ALL_COLLECTIONS:
            if name not in existing:
                self._client.create_collection(
                    collection_name=name,
                    vectors_config=models.VectorParams(
                        size=EMBEDDING_DIM,
                        distance=models.Distance.COSINE,
                    ),
                )
                logger.info("Created Qdrant collection: %s", name)

            # Idempotent: create payload indexes (no-op if already present)
            for field, schema_type in self._PAYLOAD_INDEXES:
                try:
                    self._client.create_payload_index(
                        collection_name=name,
                        field_name=field,
                        field_schema=schema_type,
                    )
                except Exception:
                    pass  # index already exists or collection doesn't support it

    def _embed(self, text: str) -> list[float]:
        """Embed a single text string using fastembed."""
        vectors = list(self._get_embedder().embed([text]))
        return vectors[0].tolist()

    # ── Write methods ──
    #
    # All add_* methods use deterministic point IDs derived from the event's
    # natural key fields. This makes writes idempotent: if a hook fires twice
    # for the same event, the second write overwrites the first rather than
    # creating a duplicate.

    @staticmethod
    def _deterministic_id(namespace: str, *key_parts: str) -> str:
        """Generate a deterministic UUID5 from namespace + key fields."""
        key = "|".join(str(p) for p in key_parts if p)
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{namespace}:{key}"))

    def add_tool_call(self, text: str, payload: dict) -> None:
        point_id = self._deterministic_id(
            "tool_call",
            payload.get("session_id", ""),
            payload.get("tool_name", ""),
            payload.get("timestamp", ""),
            payload.get("file_path", ""),
        )
        self._upsert(TOOL_CALLS, text, payload, point_id=point_id)

    def add_hallucination(self, text: str, payload: dict) -> None:
        point_id = self._deterministic_id(
            "hallucination",
            payload.get("session_id", ""),
            payload.get("timestamp", ""),
            payload.get("claim", ""),
        )
        self._upsert(HALLUCINATIONS, text, payload, point_id=point_id)

    def add_agent_spawn(self, text: str, payload: dict) -> None:
        point_id = self._deterministic_id(
            "agent_spawn",
            payload.get("session_id", ""),
            payload.get("child_agent", ""),
            payload.get("timestamp", ""),
            payload.get("description", ""),
        )
        self._upsert(AGENT_SPAWNS, text, payload, point_id=point_id)

    def add_eval(self, text: str, payload: dict) -> None:
        point_id = self._deterministic_id(
            "eval",
            payload.get("session_id", ""),
            payload.get("eval_name", ""),
            payload.get("timestamp", ""),
        )
        self._upsert(EVALS, text, payload, point_id=point_id)

    def add_session(self, text: str, payload: dict) -> None:
        point_id = self._deterministic_id(
            "session", payload.get("session_id", ""),
        )
        self._upsert(SESSIONS, text, payload, point_id=point_id)

    def add_prompt(self, text: str, payload: dict) -> None:
        point_id = self._deterministic_id(
            "prompt",
            payload.get("session_id", ""),
            payload.get("agent", ""),
            payload.get("timestamp", ""),
        )
        self._upsert(PROMPTS, text, payload, point_id=point_id)

    def add_code_change(self, text: str, payload: dict) -> None:
        point_id = self._deterministic_id(
            "code_change",
            payload.get("session_id", ""),
            payload.get("change_id", ""),
            payload.get("file_path", ""),
            payload.get("timestamp", ""),
        )
        self._upsert(CODE_CHANGES, text, payload, point_id=point_id)

    def add_bug(self, text: str, payload: dict) -> None:
        point_id = self._deterministic_id(
            "bug",
            payload.get("bug_id", ""),
            payload.get("session_id", ""),
        )
        self._upsert(BUGS, text, payload, point_id=point_id)

    def add_finding(self, text: str, payload: dict) -> None:
        point_id = self._deterministic_id(
            "finding",
            payload.get("finding_id", ""),
        )
        self._upsert(FINDINGS, text, payload, point_id=point_id)

    def add_data_quality_event(self, text: str, payload: dict) -> None:
        point_id = self._deterministic_id(
            "data_quality",
            payload.get("event_id", ""),
            payload.get("session_id", ""),
            payload.get("source_event_type", ""),
        )
        self._upsert(DATA_QUALITY, text, payload, point_id=point_id)

    def add_session_timeline(self, text: str, payload: dict) -> None:
        # Deterministic ID from session_id — re-publishing overwrites
        point_id = self._deterministic_id(
            "timeline", payload.get("session_id", ""),
        )
        self._upsert(SESSION_TIMELINES, text, payload, point_id=point_id)

    def add_conversation_turn(self, text: str, payload: dict) -> None:
        # Deterministic ID from session_id + prompt_id — idempotent
        point_id = self._deterministic_id(
            "conv_turn",
            payload.get("session_id", ""),
            payload.get("prompt_id", ""),
        )
        self._upsert(CONVERSATION_TURNS, text, payload, point_id=point_id)

    def get_conversation_turns(self, session_id: str) -> list[dict]:
        """Get all conversation turns for a session, sorted by turn_index."""
        results = self.scroll_all(
            CONVERSATION_TURNS,
            filters={"session_id": session_id},
            limit=500,
        )
        results.sort(key=lambda r: r.get("payload", {}).get("turn_index", 0))
        return results

    # ── Audit tracking ──

    AUDITABLE_COLLECTIONS = [TOOL_CALLS, AGENT_SPAWNS, CODE_CHANGES]

    def mark_session_audited(self, session_id: str) -> int:
        """Set audited=true on all events for a session across auditable collections.

        Returns total points updated.
        """
        total = 0
        for collection in self.AUDITABLE_COLLECTIONS:
            points = self.scroll_all(collection, filters={"session_id": session_id}, limit=10000)
            if not points:
                continue
            point_ids = [p["id"] for p in points]
            self._client.set_payload(
                collection_name=collection,
                payload={"audited": True},
                points=point_ids,
            )
            total += len(point_ids)
        return total

    def count_pending_audit(self) -> int:
        """Count events across auditable collections where audited is not true."""
        total = 0
        for collection in self.AUDITABLE_COLLECTIONS:
            # Count where audited field is missing or false
            not_audited = self.count(collection, {"audited__ne": True})
            total += not_audited
        return total

    def _upsert(
        self, collection: str, text: str, payload: dict,
        point_id: str | None = None,
    ) -> None:
        """Embed text and upsert as a point into the collection.

        Args:
            point_id: Deterministic ID for idempotent upserts. If provided,
                      repeated calls with the same ID overwrite the previous point.
                      If None, a random UUID is generated (insert-only).
        """
        vector = self._embed(text)
        payload["_text"] = text  # store original text for retrieval
        self._client.upsert(
            collection_name=collection,
            points=[
                models.PointStruct(
                    id=point_id or str(uuid.uuid4()),
                    vector=vector,
                    payload=payload,
                )
            ],
        )

    # ── Search methods ──

    def search_similar(
        self,
        collection: str,
        query_text: str,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[dict]:
        """Find semantically similar events."""
        query_vector = self._embed(query_text)

        query_filter = build_query_filter(filters)

        results = self._client.query_points(
            collection_name=collection,
            query=query_vector,
            query_filter=query_filter,
            limit=limit,
        )

        return [
            {
                "score": r.score,
                "payload": r.payload,
                "text": r.payload.get("_text", ""),
            }
            for r in results.points
        ]

    def search_similar_hallucinations(
        self, query: str, limit: int = 5, agent: str | None = None,
    ) -> list[dict]:
        filters = {"status": "failure"} if not agent else {"agent": agent}
        return self.search_similar(HALLUCINATIONS, query, limit, filters if agent else None)

    def search_similar_failures(
        self, error_description: str, limit: int = 5, project: str | None = None,
    ) -> list[dict]:
        filters = {"status": "failure"}
        if project:
            filters["project"] = project
        return self.search_similar(TOOL_CALLS, error_description, limit, filters)

    def search_similar_prompts(
        self, query: str, limit: int = 5, agent: str | None = None,
        project: str | None = None,
    ) -> list[dict]:
        """Find prompts semantically similar to a query."""
        filters = {}
        if agent:
            filters["agent"] = agent
        if project:
            filters["project"] = project
        return self.search_similar(PROMPTS, query, limit, filters or None)

    def search_similar_code_changes(
        self, query: str, limit: int = 5, file_path: str | None = None,
        agent: str | None = None, project: str | None = None,
    ) -> list[dict]:
        """Find code changes semantically similar to a query."""
        filters = {}
        if file_path:
            filters["file_path"] = file_path
        if agent:
            filters["agent"] = agent
        if project:
            filters["project"] = project
        return self.search_similar(CODE_CHANGES, query, limit, filters or None)

    def search_similar_bugs(
        self, query: str, limit: int = 5, stage: str | None = None,
        agent: str | None = None, project: str | None = None,
    ) -> list[dict]:
        """Find bugs semantically similar to a query."""
        filters = {}
        if stage:
            filters["stage"] = stage
        if agent:
            filters["agent"] = agent
        if project:
            filters["project"] = project
        return self.search_similar(BUGS, query, limit, filters or None)

    def search_data_quality_events(
        self, query: str, limit: int = 10, agent: str | None = None,
        event_type: str | None = None, project: str | None = None,
    ) -> list[dict]:
        """Find data quality events similar to a query."""
        filters = {}
        if agent:
            filters["agent"] = agent
        if event_type:
            filters["source_event_type"] = event_type
        if project:
            filters["project"] = project
        return self.search_similar(DATA_QUALITY, query, limit, filters or None)

    def count(self, collection: str, filters: dict | None = None) -> int:
        """Return an exact count of points matching the optional filter."""
        result = self._client.count(
            collection_name=collection,
            count_filter=build_query_filter(filters),
            exact=True,
        )
        return result.count

    def scroll_all(
        self,
        collection: str,
        filters: dict[str, Any] | None = None,
        limit: int = 2000,
        with_vectors: bool = False,
    ) -> list[dict]:
        """Return all points matching a filter using scroll (no embedding).

        Unlike search_similar, this is deterministic and returns every
        matching point up to the limit, not just semantic top-N.
        """
        query_filter = build_query_filter(filters)
        all_points = []
        offset = None

        while True:
            results, next_offset = self._client.scroll(
                collection_name=collection,
                scroll_filter=query_filter,
                limit=min(100, limit - len(all_points)),
                offset=offset,
                with_payload=True,
                with_vectors=with_vectors,
            )

            for r in results:
                point = {
                    "id": str(r.id),
                    "payload": r.payload,
                    "text": r.payload.get("_text", ""),
                }
                if with_vectors and r.vector is not None:
                    point["vector"] = r.vector
                all_points.append(point)

            if next_offset is None or len(all_points) >= limit:
                break
            offset = next_offset

        return all_points

    def get_collection_count(self, collection: str) -> int:
        info = self._client.get_collection(collection)
        return info.points_count

    # ── Finding clusters ──

    def cluster_findings(
        self,
        filters: dict[str, Any] | None = None,
        distance_threshold: float = 0.20,
        top_k: int = 5,
        max_findings: int = 2000,
    ) -> list[dict]:
        """Cluster findings by vector similarity using greedy centroid selection.

        Returns top_k clusters sorted by finding count descending.
        Each cluster has: label, short_label, finding_count, session_count,
        finding_ids, dominant_severity, dominant_auditor.
        """
        import numpy as np
        from collections import Counter

        points = self.scroll_all(FINDINGS, filters, limit=max_findings, with_vectors=True)
        if not points:
            return []

        # Filter to points that actually have vectors
        points_with_vectors = [p for p in points if p.get("vector")]
        if not points_with_vectors:
            return []

        n = len(points_with_vectors)
        dim = len(points_with_vectors[0]["vector"])
        vectors = np.array([p["vector"] for p in points_with_vectors], dtype=np.float32)

        # Normalize for cosine similarity (QDrant cosine collections
        # store normalized vectors, but be safe)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        vectors = vectors / norms

        # Cosine similarity matrix
        sim = vectors @ vectors.T
        sim_threshold = 1.0 - distance_threshold

        # Density: count of neighbors within threshold for each point
        density = (sim >= sim_threshold).sum(axis=1)

        # Greedy centroid clustering
        assigned = np.zeros(n, dtype=bool)
        clusters = []

        # Process by density descending
        order = np.argsort(-density)

        for idx in order:
            if assigned[idx]:
                continue

            # Find all unassigned points within threshold of this centroid
            neighbors = np.where((sim[idx] >= sim_threshold) & ~assigned)[0]
            if len(neighbors) == 0:
                continue

            assigned[neighbors] = True
            member_points = [points_with_vectors[i] for i in neighbors]

            # Compute cluster metadata
            payloads = [p["payload"] for p in member_points]
            finding_ids = [p["finding_id"] for p in payloads if p.get("finding_id")]
            sessions = set(p.get("target_session", "") for p in payloads if p.get("target_session"))
            severities = [p.get("severity", "info") for p in payloads]
            auditors = [p.get("auditor_type", "unknown") for p in payloads]

            # Build cluster label from auditor type + finding type
            centroid_payload = points_with_vectors[idx]["payload"]
            claim = centroid_payload.get("claim", "Uncategorized findings")
            finding_types = [p.get("finding_type", "info") for p in payloads]
            short = _build_cluster_label(
                [p.get("claim", "") for p in payloads],
                Counter(auditors).most_common(1)[0][0] if auditors else "unknown",
                Counter(finding_types).most_common(1)[0][0] if finding_types else "info",
            )

            clusters.append({
                "label": claim,
                "short_label": short,
                "finding_count": len(neighbors),
                "session_count": len(sessions),
                "finding_ids": finding_ids,
                "dominant_severity": Counter(severities).most_common(1)[0][0] if severities else "info",
                "dominant_auditor": Counter(auditors).most_common(1)[0][0] if auditors else "unknown",
                "centroid_finding_id": centroid_payload.get("finding_id", ""),
            })

        # Sort by finding count descending, return top_k
        clusters.sort(key=lambda c: c["finding_count"], reverse=True)
        return clusters[:top_k]

    # ── Session hierarchy ──

    def get_session_events(self, session_id: str) -> list[dict]:
        """Fetch all events for a session across multiple collections.

        Returns a flat list sorted by timestamp_epoch, each with a 'type'
        discriminator ('tool_call', 'agent_spawn', 'code_change').
        """
        session_filter = {"session_id": session_id}
        events = []

        for collection, event_type in [
            (TOOL_CALLS, "tool_call"),
            (AGENT_SPAWNS, "agent_spawn"),
            (CODE_CHANGES, "code_change"),
        ]:
            try:
                results = self.scroll_all(collection, filters=session_filter, limit=5000)
                for r in results:
                    payload = r.get("payload", {})
                    payload["_event_type"] = event_type
                    payload["_point_id"] = r.get("id")
                    events.append(payload)
            except Exception:
                continue

        events.sort(key=lambda e: e.get("timestamp_epoch", 0))
        return events

    # ── Window comparison ──

    def compare_windows(
        self,
        collection: str,
        query_text: str,
        window_type: str,
        window_size: int,
        filters: dict | None = None,
        limit: int = 5,
    ) -> dict:
        """Compare a semantic query across two consecutive time windows.

        window_type: "days" — calendar-based windows relative to now.
                     "sessions" — last 2*window_size sessions split in half.
        """
        if window_type == "days":
            return self._compare_windows_days(
                collection, query_text, window_size, filters, limit
            )
        elif window_type == "sessions":
            return self._compare_windows_sessions(
                collection, query_text, window_size, filters, limit
            )
        else:
            raise ValueError(f"Unknown window_type: {window_type!r}. Use 'days' or 'sessions'.")

    def _compare_windows_days(
        self,
        collection: str,
        query_text: str,
        window_size: int,
        filters: dict | None,
        limit: int,
    ) -> dict:
        """Calendar-based window comparison."""
        now = datetime.now(tz=timezone.utc)
        t_now = now.timestamp()
        t_mid = (now - timedelta(days=window_size)).timestamp()
        t_start = (now - timedelta(days=2 * window_size)).timestamp()

        def _window_filters(gte: float, lte: float) -> dict:
            f = dict(filters) if filters else {}
            f["timestamp_epoch__gte"] = gte
            f["timestamp_epoch__lte"] = lte
            return f

        recent_filters = _window_filters(t_mid, t_now)
        prior_filters = _window_filters(t_start, t_mid)

        recent_results = self.search_similar(collection, query_text, limit, recent_filters)
        recent_count = self.count(collection, recent_filters)

        prior_results = self.search_similar(collection, query_text, limit, prior_filters)
        prior_count = self.count(collection, prior_filters)

        count_ratio = (recent_count / prior_count) if prior_count else None

        return {
            "collection": collection,
            "query": query_text,
            "window_type": "days",
            "window_size": window_size,
            "recent": {
                "range": [
                    datetime.fromtimestamp(t_mid, tz=timezone.utc).isoformat(),
                    datetime.fromtimestamp(t_now, tz=timezone.utc).isoformat(),
                ],
                "count": recent_count,
                "results": recent_results,
            },
            "prior": {
                "range": [
                    datetime.fromtimestamp(t_start, tz=timezone.utc).isoformat(),
                    datetime.fromtimestamp(t_mid, tz=timezone.utc).isoformat(),
                ],
                "count": prior_count,
                "results": prior_results,
            },
            "delta": {
                "count_change": recent_count - prior_count,
                "count_ratio": count_ratio,
            },
        }

    def _compare_windows_sessions(
        self,
        collection: str,
        query_text: str,
        window_size: int,
        filters: dict | None,
        limit: int = 5,
    ) -> dict:
        """Session-based window comparison."""
        # Build a filter for the sessions collection (project only, if provided)
        session_filter_dict: dict = {}
        if filters and "project" in filters:
            session_filter_dict["project"] = filters["project"]

        session_scroll_filter = build_query_filter(session_filter_dict or None)

        # Scroll the sessions collection ordered by timestamp_epoch DESC
        scroll_result = self._client.scroll(
            collection_name=SESSIONS,
            scroll_filter=session_scroll_filter,
            limit=2 * window_size,
            order_by=models.OrderBy(
                key="timestamp_epoch",
                direction=models.Direction.DESC,
            ),
        )

        points = scroll_result[0] if scroll_result else []
        session_ids = [p.id for p in points]

        # Split: first half = recent, second half = prior
        recent_ids = session_ids[:window_size]
        prior_ids = session_ids[window_size:]

        def _session_filters(ids: list) -> dict:
            f = dict(filters) if filters else {}
            f["session_id"] = ids
            return f

        recent_filters = _session_filters(recent_ids)
        prior_filters = _session_filters(prior_ids)

        recent_results = self.search_similar(collection, query_text, limit, recent_filters) if recent_ids else []
        recent_count = self.count(collection, recent_filters) if recent_ids else 0

        prior_results = self.search_similar(collection, query_text, limit, prior_filters) if prior_ids else []
        prior_count = self.count(collection, prior_filters) if prior_ids else 0

        count_ratio = (recent_count / prior_count) if prior_count else None

        return {
            "collection": collection,
            "query": query_text,
            "window_type": "sessions",
            "window_size": window_size,
            "recent": {
                "range": recent_ids,
                "count": recent_count,
                "results": recent_results,
            },
            "prior": {
                "range": prior_ids,
                "count": prior_count,
                "results": prior_results,
            },
            "delta": {
                "count_change": recent_count - prior_count,
                "count_ratio": count_ratio,
            },
        }

    # ── Timeline ──

    def timeline(
        self,
        query_text: str,
        collections: list[str],
        anchor_collection: str | None = None,
        time_window_minutes: int = 30,
        filters: dict | None = None,
        limit_per_collection: int = 10,
    ) -> dict:
        """Reconstruct a unified time-sorted view across multiple collections.

        Phase 1: Find anchor event via semantic search.
        Phase 2: Gather events from all collections within a time window.
        Phase 3: Merge, sort by timestamp, tag with collection.
        """
        anchor_col = anchor_collection or collections[0]

        # Phase 1: Find anchor
        anchor_results = self.search_similar(anchor_col, query_text, limit=1, filters=filters)
        if not anchor_results:
            return {"error": f"No matching anchor event found in {anchor_col}"}

        anchor_hit = anchor_results[0]
        anchor_payload = anchor_hit.get("payload", {})
        anchor_epoch = anchor_payload.get("timestamp_epoch")

        if anchor_epoch is None:
            return {"error": "Anchor event missing timestamp_epoch — run backfill migration"}

        # Compute time window
        half_window = time_window_minutes * 60 / 2
        window_start_epoch = anchor_epoch - half_window
        window_end_epoch = anchor_epoch + half_window
        window_start_iso = datetime.fromtimestamp(window_start_epoch, tz=timezone.utc).isoformat()
        window_end_iso = datetime.fromtimestamp(window_end_epoch, tz=timezone.utc).isoformat()

        # Phase 2: Gather events from each collection using temporal scroll
        all_events: list[dict] = []
        counts_by_collection: dict[str, int] = {}

        time_filters: dict = {}
        if filters:
            time_filters.update(filters)
        time_filters["timestamp_epoch__gte"] = window_start_epoch
        time_filters["timestamp_epoch__lte"] = window_end_epoch

        scroll_filter = build_query_filter(time_filters)

        for col in collections:
            scroll_result = self._client.scroll(
                collection_name=col,
                scroll_filter=scroll_filter,
                limit=limit_per_collection * 10,  # over-fetch, then trim by proximity
            )
            points = scroll_result[0] if scroll_result else []

            # Sort by closeness to anchor epoch, keep top limit_per_collection
            def _dist(p):
                return abs((p.payload or {}).get("timestamp_epoch", 0) - anchor_epoch)

            points_sorted = sorted(points, key=_dist)[:limit_per_collection]

            counts_by_collection[col] = len(points_sorted)

            for point in points_sorted:
                payload = point.payload or {}
                epoch = payload.get("timestamp_epoch", 0)
                ts_iso = (
                    datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
                    if epoch
                    else None
                )
                event: dict = {
                    "collection": col,
                    "timestamp": ts_iso,
                    "timestamp_epoch": epoch,
                    "payload": payload,
                    "is_anchor": False,
                }
                all_events.append(event)

        # Phase 3: Sort by timestamp_epoch ascending and mark anchor
        all_events.sort(key=lambda e: e.get("timestamp_epoch") or 0)

        # Mark the anchor event (match by collection + timestamp_epoch)
        anchor_ts = anchor_payload.get("timestamp_epoch")
        for event in all_events:
            if event["collection"] == anchor_col and event["timestamp_epoch"] == anchor_ts:
                event["is_anchor"] = True
                break

        anchor_ts_iso = (
            datetime.fromtimestamp(anchor_ts, tz=timezone.utc).isoformat()
            if anchor_ts
            else None
        )

        return {
            "query": query_text,
            "anchor": {
                "collection": anchor_col,
                "timestamp": anchor_ts_iso,
                "score": anchor_hit.get("score"),
                "payload": anchor_payload,
            },
            "window": {
                "start": window_start_iso,
                "end": window_end_iso,
            },
            "timeline": all_events,
            "counts_by_collection": counts_by_collection,
        }

    def close(self) -> None:
        self._client.close()
