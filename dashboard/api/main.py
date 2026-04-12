"""
Audit Dashboard API

FastAPI backend that serves QDrant telemetry and Redis stream data
to the React dashboard. Thin REST layer over existing clients.
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

# Add project root to path
_project_root = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _project_root)

from dotenv import load_dotenv
load_dotenv(os.path.join(_project_root, ".env"))

from observability.qdrant_backend import QdrantBackend, ALL_COLLECTIONS
from observability.stream_client import StreamClient
from observability.messages import (
    MessageEnvelope,
    EscalationResolutionPayload,
    project_escalation_resolution_stream,
)
from observability.audit_store import AuditStore

app = FastAPI(title="LLM Observability Audit Dashboard", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Lazy-initialized backends
_qb: QdrantBackend | None = None
_sc: StreamClient | None = None
_store: AuditStore | None = None


def get_qdrant() -> QdrantBackend:
    global _qb
    if _qb is None:
        _qb = QdrantBackend()
    return _qb


def get_stream_client() -> StreamClient:
    global _sc
    if _sc is None:
        _sc = StreamClient.for_director()
    return _sc


def get_store() -> AuditStore:
    global _store
    if _store is None:
        _store = AuditStore()
    return _store


def _read_live_findings(sc, limit=500):
    """Read findings currently in Redis (not yet archived)."""
    results = sc._redis.xrange("audit:findings", count=limit)
    findings = []
    for stream_id, data in results:
        try:
            env = MessageEnvelope.from_stream_dict(data)
            findings.append({
                "stream_id": stream_id,
                "timestamp": env.timestamp.isoformat(),
                "source": env.source,
                "live": True,
                **env.payload,
            })
        except Exception:
            continue
    return findings


# ── Health ──

@app.get("/api/health")
def health():
    qb = get_qdrant()
    sc = get_stream_client()
    try:
        pending_audit = qb.count_pending_audit()
    except Exception:
        pending_audit = 0
    return {
        "status": "ok",
        "qdrant": True,
        "redis": sc.ping(),
        "pending_audit": pending_audit,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Collections Overview ──

@app.get("/api/collections")
def list_collections():
    qb = get_qdrant()
    counts = {}
    for coll in ALL_COLLECTIONS:
        try:
            counts[coll] = qb.get_collection_count(coll)
        except Exception:
            counts[coll] = 0
    return {"collections": counts}


# ── Streams Overview ──

@app.get("/api/streams")
def list_streams():
    sc = get_stream_client()
    streams = {}
    stream_names = [
        "audit:findings", "audit:tasks", "audit:status",
        "audit:directives", "audit:escalations",
    ]

    # Add per-project streams
    projects = _load_projects()
    for p in projects:
        stream_names.append(f"directives:{p}")
        stream_names.append(f"compliance:{p}")
        stream_names.append(f"promotions:{p}")
        stream_names.append(f"promotion_ack:{p}")
        stream_names.append(f"escalation_resolutions:{p}")

    for name in stream_names:
        try:
            streams[name] = sc.stream_length(name)
        except Exception:
            streams[name] = 0

    return {"streams": streams}


def _load_projects() -> list[str]:
    config_path = Path(__file__).resolve().parent.parent.parent / "config" / "projects.json"
    if not config_path.exists():
        return []
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        return [p["name"] for p in data.get("projects", []) if p.get("active", True)]
    except Exception:
        return []


def _parse_timestamp(ts_str):
    """Parse an ISO timestamp string, tolerant of various formats."""
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return None


def _in_date_range(timestamp_str, start, end):
    """Check if a timestamp falls within the given date range."""
    if not start and not end:
        return True
    ts = _parse_timestamp(timestamp_str)
    if not ts:
        return True  # can't filter, include it
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    if start and ts < start:
        return False
    if end and ts > end:
        return False
    return True


def _parse_date_param(date_str):
    """Parse a date query param (ISO format) into a datetime."""
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


# ── Findings ──

@app.get("/api/findings")
def get_findings(
    project: str | None = Query(None),
    auditor_type: str | None = Query(None),
    severity: str | None = Query(None),
    finding_type: str | None = Query(None),
    audit_cycle_id: str | None = Query(None),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    limit: int = Query(200, ge=1, le=500),
):
    store = get_store()
    sc = get_stream_client()
    start = _parse_date_param(start_date)
    end = _parse_date_param(end_date)

    # Archived findings from SQLite
    archived = store.query_findings(
        project=project,
        auditor_type=auditor_type,
        severity=severity,
        finding_type=finding_type,
        audit_cycle_id=audit_cycle_id,
        start_date=start.isoformat() if start else None,
        end_date=end.isoformat() if end else None,
        limit=limit,
    )
    archived_ids = {f.get("finding_id") for f in archived if f.get("finding_id")}

    # Live findings from Redis (not yet archived)
    live = []
    for f in _read_live_findings(sc, limit=500):
        # Skip if already archived (only match on non-empty IDs)
        fid = f.get("finding_id")
        if fid and fid in archived_ids:
            continue
        # Apply filters
        if project and f.get("project") != project:
            continue
        if auditor_type and f.get("auditor_type", f.get("auditor")) != auditor_type:
            continue
        if severity and f.get("severity", "").lower() != severity.lower():
            continue
        if finding_type and f.get("finding_type", "").lower() != finding_type.lower():
            continue
        if audit_cycle_id and f.get("audit_cycle_id") != audit_cycle_id:
            continue
        if not _in_date_range(f.get("timestamp", ""), start, end):
            continue
        live.append(f)

    # Merge: live first (most recent), then archived
    combined = live + archived
    combined.sort(key=lambda f: f.get("timestamp", ""), reverse=True)
    combined = combined[:limit]

    return {"count": len(combined), "findings": combined}


@app.get("/api/findings/by-cycle")
def get_findings_by_cycle(
    project: str | None = Query(None),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Group findings by audit_cycle_id — used for the line graph on Overview."""
    store = get_store()
    sc = get_stream_client()
    start = _parse_date_param(start_date)
    end = _parse_date_param(end_date)

    # Archived cycles from SQLite
    archived_cycles = store.get_findings_by_cycle(
        project=project,
        start_date=start.isoformat() if start else None,
        end_date=end.isoformat() if end else None,
    )

    # Live cycles from Redis
    cycles = {c["cycle_id"]: c for c in archived_cycles}
    for f in _read_live_findings(sc, limit=500):
        if project and f.get("project") != project:
            continue
        if not _in_date_range(f.get("timestamp", ""), start, end):
            continue

        cycle_id = f.get("audit_cycle_id", "unknown")
        if cycle_id not in cycles:
            cycles[cycle_id] = {"cycle_id": cycle_id, "timestamp": f.get("timestamp", ""), "total": 0}

        auditor = f.get("auditor_type", f.get("auditor", "director"))
        cycles[cycle_id][auditor] = cycles[cycle_id].get(auditor, 0) + 1
        cycles[cycle_id]["total"] += 1

    sorted_cycles = sorted(cycles.values(), key=lambda c: c.get("timestamp", ""))
    return {"count": len(sorted_cycles), "cycles": sorted_cycles}


@app.get("/api/findings/by-day")
def get_findings_by_day(
    project: str | None = Query(None),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Findings rate per 100 tool calls, aggregated by calendar day and auditor.

    Normalizes finding counts against session activity so days with
    different workloads are comparable.
    """
    store = get_store()
    sc = get_stream_client()
    qb = get_qdrant()
    start = _parse_date_param(start_date)
    end = _parse_date_param(end_date)

    # 1. Collect all findings (archived + live) grouped by day + auditor.
    # Use the audit_cycle_id date (local time) to group, not the UTC timestamp.
    days: dict[str, dict] = {}  # date_str -> {auditor: count, ...}

    def _finding_date(finding: dict) -> str:
        """Extract the local date from audit_cycle_id or fall back to timestamp."""
        cycle_id = finding.get("audit_cycle_id", "")
        # Format: cycle-YYYYMMDD-HHMMSS-hash
        if cycle_id.startswith("cycle-") and len(cycle_id) >= 15:
            raw = cycle_id.split("-")[1]  # YYYYMMDD
            if len(raw) == 8:
                return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
        # Fall back to timestamp, converting UTC to local
        ts = finding.get("timestamp", "")
        if ts:
            try:
                from datetime import datetime as dt
                utc = dt.fromisoformat(ts)
                local = utc.astimezone()
                return local.strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                return ts[:10]
        return ""

    def _add_finding(finding: dict):
        date_str = _finding_date(finding)
        if not date_str:
            return
        if not _in_date_range(date_str, start, end):
            return
        if date_str not in days:
            days[date_str] = {"_counts": {}, "_total": 0}
        auditor = finding.get("auditor_type", finding.get("auditor", "director"))
        days[date_str]["_counts"][auditor] = days[date_str]["_counts"].get(auditor, 0) + 1
        days[date_str]["_total"] += 1

    # Archived from SQLite
    archived = store.query_findings(project=project, limit=5000)
    for f in archived:
        _add_finding(f)

    # Live from Redis
    for f in _read_live_findings(sc, limit=500):
        if project and f.get("project") != project:
            continue
        _add_finding(f)

    # 2. Get total tool call count from sessions collection (fast, no full scan)
    session_results = qb.scroll_all("sessions", filters={"project": project} if project else None, limit=500)
    total_tool_calls = sum(
        s.get("payload", {}).get("total_tool_calls", 0) for s in session_results
    )

    # 3. Compute rates per day using total tool calls as denominator.
    # Findings are dated by publication time (audit cycle day) which may differ
    # from the session date. Using total tool calls across the range gives a
    # meaningful rate regardless of date alignment.
    all_dates = sorted(days.keys())
    result = []
    for date_str in all_dates:
        day_data = days[date_str]
        tc = total_tool_calls  # same denominator for all days

        entry = {
            "date": date_str,
            "tool_calls": tc,
            "total_findings": day_data["_total"],
            "total_rate": round(day_data["_total"] / tc * 100, 2) if tc > 0 else None,
        }

        # Per-auditor rates
        for auditor, count in day_data["_counts"].items():
            entry[auditor] = round(count / tc * 100, 2) if tc > 0 else None

        result.append(entry)

    return {"count": len(result), "days": result}


@app.get("/api/findings/by-type")
def get_findings_by_type(
    project: str | None = Query(None),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Group findings by finding_type — used for the findings status bar chart."""
    sc = get_stream_client()
    results = sc._redis.xrange("audit:findings", count=500)
    start = _parse_date_param(start_date)
    end = _parse_date_param(end_date)

    by_type = {}
    for stream_id, data in results:
        try:
            env = MessageEnvelope.from_stream_dict(data)
            p = env.payload
            if project and p.get("project") != project:
                continue
            if not _in_date_range(env.timestamp.isoformat(), start, end):
                continue
            ft = p.get("finding_type", "unknown").lower()
            by_type[ft] = by_type.get(ft, 0) + 1
        except Exception:
            continue

    return {"by_type": by_type}


@app.get("/api/findings/by-confidence")
def get_findings_by_confidence(
    project: str | None = Query(None),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Bucket findings by confidence score — used for the confidence pie chart."""
    sc = get_stream_client()
    results = sc._redis.xrange("audit:findings", count=500)
    start = _parse_date_param(start_date)
    end = _parse_date_param(end_date)

    buckets = {"0.9-1.0": 0, "0.7-0.9": 0, "0.5-0.7": 0, "0.0-0.5": 0, "unknown": 0}
    for stream_id, data in results:
        try:
            env = MessageEnvelope.from_stream_dict(data)
            p = env.payload
            if project and p.get("project") != project:
                continue
            if not _in_date_range(env.timestamp.isoformat(), start, end):
                continue
            conf = p.get("confidence")
            if conf is None:
                buckets["unknown"] += 1
            elif conf >= 0.9:
                buckets["0.9-1.0"] += 1
            elif conf >= 0.7:
                buckets["0.7-0.9"] += 1
            elif conf >= 0.5:
                buckets["0.5-0.7"] += 1
            else:
                buckets["0.0-0.5"] += 1
        except Exception:
            continue

    return {"by_confidence": buckets}


@app.get("/api/findings/clusters")
def get_finding_clusters(
    project: str | None = Query(None),
    threshold: float = Query(0.20, ge=0.05, le=0.9),
    top_k: int = Query(5, ge=1, le=20),
):
    """Cluster findings by semantic similarity using QDrant vector embeddings."""
    qb = get_qdrant()
    filters = {}
    if project:
        filters["project"] = project

    clusters = qb.cluster_findings(
        filters=filters if filters else None,
        distance_threshold=threshold,
        top_k=top_k,
    )

    total = qb.get_collection_count("findings")
    clustered = sum(c["finding_count"] for c in clusters)

    return {
        "clusters": [
            {
                "cluster_id": i,
                "label": c["label"],
                "short_label": c["short_label"],
                "finding_count": c["finding_count"],
                "session_count": c["session_count"],
                "finding_ids": c["finding_ids"],
                "dominant_severity": c["dominant_severity"],
                "dominant_auditor": c["dominant_auditor"],
            }
            for i, c in enumerate(clusters)
        ],
        "total_findings": total,
        "clustered_findings": clustered,
    }


@app.get("/api/directives/by-status")
def get_directives_by_status(
    project: str | None = Query(None),
):
    """Group directives by type and status — used for the directives bar chart.

    Reuses the list endpoint logic so counts and statuses match.
    """
    result = get_directives(
        project=project, status=None, directive_type=None, limit=500,
    )
    directives = result.get("directives", [])

    type_status = {}  # {type: {status: count}}
    for d in directives:
        dtype = (d.get("directive_type") or "unknown").upper()
        status = (d.get("lifecycle_status") or "PENDING").upper()

        if dtype not in type_status:
            type_status[dtype] = {}
        type_status[dtype][status] = type_status[dtype].get(status, 0) + 1

    return {"by_type_status": type_status}


@app.get("/api/findings/{finding_id}")
def get_finding_detail(finding_id: str):
    sc = get_stream_client()
    results = sc._redis.xrange("audit:findings", count=200)

    for stream_id, data in results:
        try:
            env = MessageEnvelope.from_stream_dict(data)
            if env.payload.get("finding_id") == finding_id:
                return {
                    "stream_id": stream_id,
                    "timestamp": env.timestamp.isoformat(),
                    "source": env.source,
                    **env.payload,
                }
        except Exception:
            continue

    raise HTTPException(status_code=404, detail=f"Finding {finding_id} not found")


# ── Compliance helper ──

def _read_all_compliance(sc, store, projects):
    """Read compliance events from both Redis (live) and SQLite (archived)."""
    events = []

    # Archived from SQLite
    archived = store.query_compliance(limit=500)
    archived_ids = {c.get("compliance_id") for c in archived if c.get("compliance_id")}
    events.extend(archived)

    # Live from Redis per-project compliance streams
    for proj in projects:
        try:
            results = sc._redis.xrange(f"compliance:{proj}", count=200)
            for stream_id, data in results:
                env = MessageEnvelope.from_stream_dict(data)
                p = env.payload
                cid = p.get("compliance_id")
                if cid and cid in archived_ids:
                    continue
                events.append({
                    "stream_id": stream_id,
                    "timestamp": env.timestamp.isoformat(),
                    "live": True,
                    "project": proj,
                    **p,
                })
        except Exception:
            continue

    return events


def _compute_directive_status(directive, compliance_events):
    """Compute the effective lifecycle status of a directive from its compliance events."""
    # Respect explicit terminal statuses — don't override with computed lifecycle
    explicit = (directive.get("status") or "").upper()
    if explicit in ("DISMISSED", "SUPERSEDED"):
        return explicit

    did = directive.get("directive_id", "")
    if not did:
        return "PENDING"

    related = [c for c in compliance_events if c.get("directive_id") == did]
    if not related:
        return "PENDING"

    # Check for verification results first (they're the final word)
    verifications = [c for c in related if c.get("is_verification") or c.get("is_verification") == 1]
    if verifications:
        latest_verification = sorted(verifications, key=lambda c: c.get("timestamp", ""))[-1]
        passed = latest_verification.get("verification_passed")
        if passed or passed == 1:
            return "VERIFIED_COMPLIANT"
        elif passed == 0 or passed is False:
            return "VERIFIED_NON_COMPLIANT"
        return "VERIFICATION_PENDING"

    # Check for acknowledgments
    acks = [c for c in related if not c.get("is_verification") and c.get("is_verification") != 1]
    if acks:
        # Check if any have a conflict
        conflicts = [c for c in acks if c.get("conflict_reason")]
        if conflicts:
            return "ESCALATED"
        return "ACKNOWLEDGED"

    return "PENDING"


# ── Directives ──

@app.get("/api/directives")
def get_directives(
    project: str | None = Query(None),
    status: str | None = Query(None),
    directive_type: str | None = Query(None),
    limit: int = Query(50),
):
    store = get_store()
    sc = get_stream_client()
    projects = [project] if project else _load_projects()

    # Get all compliance events for status computation
    all_compliance = _read_all_compliance(sc, store, projects)

    # Archived directives from SQLite — flatten nested payload to top level
    archived_raw = store.query_directives(project=project, directive_type=directive_type, limit=limit)
    archived = []
    for d in archived_raw:
        # The payload column contains the original DirectivePayload as a dict.
        # Merge its fields to top level so archived directives match live structure.
        nested = d.pop("payload", None)
        if isinstance(nested, str):
            try:
                nested = json.loads(nested)
            except (json.JSONDecodeError, TypeError):
                nested = {}
        if isinstance(nested, dict):
            for k, v in nested.items():
                if k not in d or d[k] in (None, ""):
                    d[k] = v
        d["live"] = False
        archived.append(d)
    archived_ids = {d.get("directive_id") for d in archived if d.get("directive_id")}

    # Live from Redis — per-project delivery queues only.
    # audit:directives is an internal log; directives:{project} is the
    # authoritative delivery stream with richer content. Reading both causes dupes.
    live = []
    seen_ids = set(archived_ids)
    streams = [f"directives:{proj}" for proj in projects]

    for stream in streams:
        try:
            results = sc._redis.xrange(stream, count=limit)
            for stream_id, data in results:
                env = MessageEnvelope.from_stream_dict(data)
                p = env.payload
                src = "internal" if stream == "audit:directives" else f"delivered:{stream.split(':')[1]}"
                ts = env.timestamp.isoformat()

                # Handle batch-published directives (Director may send all directives
                # in a single message with a directives_issued array)
                batch = p.get("directives_issued")
                if isinstance(batch, list):
                    for item in batch:
                        did = item.get("id") or item.get("directive_id")
                        if did and did in seen_ids:
                            continue
                        if did:
                            seen_ids.add(did)
                        live.append({
                            "stream_id": stream_id,
                            "timestamp": ts,
                            "source": src,
                            "live": True,
                            "directive_id": did,
                            "directive_type": item.get("type", item.get("directive_type", "")),
                            "target_agent": item.get("target", item.get("target_agent", "")),
                            "content": item.get("content", ""),
                            "confidence": item.get("confidence"),
                            "severity": item.get("severity"),
                            "triggered_by_finding": item.get("triggered_by", item.get("triggered_by_finding")),
                            "status": item.get("status", "PENDING"),
                            "required_action": item.get("required_action", ""),
                            "supporting_metrics": item.get("supporting_metrics", {}),
                            **{k: v for k, v in item.items() if k not in (
                                "id", "type", "target", "content", "confidence",
                                "severity", "triggered_by", "status",
                            )},
                        })
                    continue

                # Standard single-directive message
                did = p.get("directive_id")
                if did and did in seen_ids:
                    continue
                if directive_type and (p.get("type", p.get("directive_type", ""))).upper() != directive_type.upper():
                    continue
                if did:
                    seen_ids.add(did)
                # Normalize: Director may use 'title' instead of 'content'
                if not p.get("content") and p.get("title"):
                    p["content"] = p["title"]
                if not p.get("triggered_by_finding") and p.get("triggered_by"):
                    p["triggered_by_finding"] = p["triggered_by"]
                live.append({
                    "stream_id": stream_id,
                    "timestamp": ts,
                    "source": src,
                    "live": True,
                    **p,
                })
        except Exception:
            continue

    combined = live + archived

    # Load all promotion decisions for enrichment
    all_promotions = store.query_promotion_decisions(project=project, limit=500)
    promotions_by_directive = {}
    for p in all_promotions:
        did = p.get("directive_id")
        if did:
            promotions_by_directive.setdefault(did, []).append(p)

    # Enrich each directive with computed lifecycle status, compliance events, and promotions
    for d in combined:
        did = d.get("directive_id", "")
        computed_status = _compute_directive_status(d, all_compliance)
        d["lifecycle_status"] = computed_status

        # Attach related compliance events
        related = sorted(
            [c for c in all_compliance if c.get("directive_id") == did],
            key=lambda c: c.get("timestamp", ""),
        )
        d["compliance_events"] = related

        # Attach promotion decisions
        d["promotion_decisions"] = promotions_by_directive.get(did, [])

    # Filter by status if requested (use computed lifecycle_status)
    if status:
        combined = [d for d in combined if d.get("lifecycle_status", "").upper() == status.upper()]

    combined.sort(key=lambda d: d.get("timestamp", ""), reverse=True)
    combined = combined[:limit]

    return {"count": len(combined), "directives": combined}


class DismissDirectiveInput(BaseModel):
    reason: str = ""


def _remove_directive_from_stream(sc, stream: str, directive_id: str) -> bool:
    """Remove a directive from a Redis stream, handling both single and batch formats.

    Returns True if the directive was found and removed.
    """
    try:
        results = sc._redis.xrange(stream, count=1000)
    except Exception:
        return False

    for stream_id, data in results:
        try:
            env = MessageEnvelope.from_stream_dict(data)
            p = env.payload

            # Single-directive message: directive_id at top level
            if p.get("directive_id") == directive_id:
                sc._redis.xdel(stream, stream_id)
                return True

            # Batch message: directives_issued array
            batch = p.get("directives_issued")
            if isinstance(batch, list):
                match = any(
                    (item.get("id") or item.get("directive_id")) == directive_id
                    for item in batch
                )
                if match:
                    remaining = [
                        item for item in batch
                        if (item.get("id") or item.get("directive_id")) != directive_id
                    ]
                    if not remaining:
                        # All directives dismissed — remove entire message
                        sc._redis.xdel(stream, stream_id)
                    else:
                        # Rebuild message without the dismissed directive
                        p["directives_issued"] = remaining
                        env.payload = p
                        sc._redis.xdel(stream, stream_id)
                        sc._redis.xadd(stream, env.to_stream_dict())
                    return True
        except Exception:
            continue

    return False


@app.post("/api/directives/{directive_id}/dismiss")
def dismiss_directive(directive_id: str, body: DismissDirectiveInput):
    """User dismisses a directive.

    1. Archives the directive to SQLite with status=DISMISSED (preserves history)
    2. Removes from outbound Redis streams so the external project doesn't act on it
    """
    store = get_store()
    sc = get_stream_client()

    # 1. Archive to SQLite before removing from Redis.
    # First try to find the directive payload in Redis so we have full data.
    projects = _load_projects()
    directive_payload = None
    for proj in projects:
        stream = f"directives:{proj}"
        try:
            results = sc._redis.xrange(stream, count=1000)
            for stream_id, data in results:
                env = MessageEnvelope.from_stream_dict(data)
                p = env.payload
                if p.get("directive_id") == directive_id:
                    directive_payload = p
                    directive_payload["_stream_id"] = stream_id
                    directive_payload["_timestamp"] = env.timestamp.isoformat()
                    break
                # Check batch format
                batch = p.get("directives_issued", [])
                for item in batch:
                    if (item.get("id") or item.get("directive_id")) == directive_id:
                        directive_payload = item
                        directive_payload["_timestamp"] = env.timestamp.isoformat()
                        break
            if directive_payload:
                break
        except Exception:
            continue

    # Archive the directive payload to SQLite if we found it in Redis, so
    # the dismiss_directive() call below has a row to update. Do NOT override
    # status here — dismiss_directive() reads the current status for the
    # lifecycle view's `previous_status` snapshot.
    if directive_payload:
        clean_payload = {k: v for k, v in directive_payload.items() if not k.startswith("_")}
        store.archive_directive(
            stream_id=directive_payload.get("_stream_id", ""),
            timestamp=directive_payload.get("_timestamp", datetime.now(timezone.utc).isoformat()),
            payload=clean_payload,
        )
        store.commit()

    # Atomic dismissal: reads current status, stamps metadata.dismissal with
    # previous_status snapshot (load-bearing for the directive_lifecycle view),
    # and flips directives.status = 'DISMISSED'. See Gap 1 Issue #5.
    result = store.dismiss_directive(
        directive_id=directive_id,
        reason=body.reason or "",
        cycle_id=None,
    )
    if result is None:
        # Directive not in SQLite — nothing to dismiss
        raise HTTPException(status_code=404, detail=f"Directive {directive_id} not found")

    # 2. Remove from all relevant streams
    removed_from = []

    streams = ["audit:directives"]
    for proj in projects:
        streams.append(f"directives:{proj}")

    for stream in streams:
        if _remove_directive_from_stream(sc, stream, directive_id):
            removed_from.append(stream)

    return {
        "dismissed": True,
        "directive_id": directive_id,
        "reason": body.reason,
        "removed_from_streams": removed_from,
    }


# ── Directive lifecycle + metrics (Gap 1) ──

@app.get("/api/directives/{directive_id}/lifecycle")
def get_directive_lifecycle(directive_id: str):
    """Return the transition timeline for a single directive (Chart A)."""
    store = get_store()
    transitions = store.query_directive_lifecycle(directive_id)
    return {
        "directive_id": directive_id,
        "transitions": transitions,
    }


@app.get("/api/metrics/cycles-to-verification")
def get_cycles_to_verification(
    project: str = Query(..., description="Project name to scope metrics"),
    last_n_cycles: int = Query(20, description="Rough window limit"),
):
    """Return directives that reached VERIFIED_COMPLIANT with their
    publication and verification cycle markers. Feeds Chart C — frontend
    computes cycles-elapsed (verified_cycle - published_cycle) and aggregates
    into median + IQR per cycle bucket.
    """
    store = get_store()
    rows = store.query_cycles_to_verification(
        project=project,
        last_n_cycles=last_n_cycles,
    )
    return {
        "project": project,
        "last_n_cycles": last_n_cycles,
        "directives": rows,
    }


@app.get("/api/metrics/non-verified-counts")
def get_non_verified_counts(
    project: str = Query(..., description="Project name to scope metrics"),
):
    """Return counts of directives in each terminal-non-verified state for
    the counter strip below Chart C.
    """
    store = get_store()
    counts = store.query_non_verified_counts(project=project)
    return {
        "project": project,
        "counts": counts,
    }


# ── Compliance ──

@app.get("/api/compliance")
def get_compliance(
    directive_id: str | None = Query(None),
    agent: str | None = Query(None),
    project: str | None = Query(None),
    limit: int = Query(50),
):
    """Get compliance events — acknowledgments and verification results."""
    store = get_store()
    sc = get_stream_client()
    projects = [project] if project else _load_projects()

    all_events = _read_all_compliance(sc, store, projects)

    # Apply filters
    if directive_id:
        all_events = [c for c in all_events if c.get("directive_id") == directive_id]
    if agent:
        all_events = [c for c in all_events if c.get("agent") == agent]
    if project:
        all_events = [c for c in all_events if c.get("project") == project]

    all_events.sort(key=lambda c: c.get("timestamp", ""), reverse=True)
    return {"count": len(all_events[:limit]), "compliance": all_events[:limit]}


# ── Escalations ──

@app.get("/api/escalations")
def get_escalations(
    severity: str | None = Query(None),
    project: str | None = Query(None),
    escalation_type: str | None = Query(None),
    resolution_status: str | None = Query(None),
    limit: int = Query(50),
):
    store = get_store()
    sc = get_stream_client()

    # Archived
    archived = store.query_escalation_history(
        project=project,
        escalation_type=escalation_type,
        resolution_status=resolution_status,
        limit=limit,
    )
    archived_ids = {e.get("escalation_id") for e in archived if e.get("escalation_id")}

    # Live from Redis
    live = []
    results = sc._redis.xrange("audit:escalations", count=limit)
    for stream_id, data in results:
        try:
            env = MessageEnvelope.from_stream_dict(data)
            p = env.payload
            eid = p.get("escalation_id")
            if eid and eid in archived_ids:
                continue
            if severity and p.get("severity", "").lower() != severity.lower():
                continue
            if project and p.get("project") != project:
                continue
            if escalation_type and p.get("escalation_type", "").upper() != escalation_type.upper():
                continue
            if resolution_status and p.get("resolution_status", "OPEN").upper() != resolution_status.upper():
                continue
            live.append({
                "stream_id": stream_id,
                "timestamp": env.timestamp.isoformat(),
                "live": True,
                **p,
            })
        except Exception:
            continue

    combined = live + archived
    combined.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    combined = combined[:limit]

    return {"count": len(combined), "escalations": combined}


# ── Promotions ──

@app.get("/api/promotions")
def get_promotions(
    project: str | None = Query(None),
    decision_type: str | None = Query(None),
    status: str | None = Query(None),
    directive_id: str | None = Query(None),
    limit: int = Query(50),
):
    """Get promotion decisions from SQLite."""
    store = get_store()
    decisions = store.query_promotion_decisions(
        project=project,
        decision_type=decision_type,
        status=status,
        directive_id=directive_id,
        limit=limit,
    )
    return {"count": len(decisions), "promotions": decisions}


@app.get("/api/standing-directives")
def get_standing_directives(
    project: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50),
):
    """Get standing directives from SQLite."""
    store = get_store()
    directives = store.query_standing_directives(
        project=project,
        status=status,
        limit=limit,
    )
    return {"count": len(directives), "standing_directives": directives}


# ── Escalation Messages & Resolution ──

@app.get("/api/escalations/{escalation_id}/messages")
def get_escalation_messages(escalation_id: str):
    """Get the conversation thread for an escalation."""
    store = get_store()
    messages = store.get_escalation_messages(escalation_id)
    return {"escalation_id": escalation_id, "count": len(messages), "messages": messages}


class EscalationMessageInput(BaseModel):
    content: str


@app.post("/api/escalations/{escalation_id}/messages")
def post_escalation_message(escalation_id: str, body: EscalationMessageInput):
    """User sends a message in the escalation conversation thread."""
    store = get_store()
    message_id = store.insert_escalation_message(
        escalation_id=escalation_id,
        author="user",
        content=body.content,
    )
    return {"message_id": message_id, "status": "sent"}


class DismissInput(BaseModel):
    guidance: str


@app.post("/api/escalations/{escalation_id}/dismiss")
def dismiss_escalation(escalation_id: str, body: DismissInput):
    """User dismisses an escalation with final guidance.

    1. Writes the final guidance as a message in the thread
    2. Updates escalation status to DISMISSED
    3. Publishes EscalationResolutionPayload to Redis for Director pickup
    """
    store = get_store()
    sc = get_stream_client()

    # 1. Write final message to thread
    store.insert_escalation_message(
        escalation_id=escalation_id,
        author="user",
        content=body.guidance,
    )

    # 2. Update status
    store.update_escalation_status(escalation_id, "DISMISSED")

    # 3. Get conversation history for the resolution payload
    messages = store.get_escalation_messages(escalation_id)

    # 4. Determine project from escalation record
    escalations = store.query_escalation_history(limit=500)
    esc = next((e for e in escalations if e.get("escalation_id") == escalation_id), None)
    project = esc.get("project", "") if esc else ""

    if not project:
        # Try to find project from active projects
        projects = _load_projects()
        project = projects[0] if projects else ""

    # 5. Publish to escalation_resolutions:{project} stream
    if project:
        payload = EscalationResolutionPayload(
            escalation_id=escalation_id,
            final_guidance=body.guidance,
            message_history=messages,
        )
        stream = project_escalation_resolution_stream(project)
        from observability.messages import MessageType, build_message
        envelope = build_message(
            stream=stream,
            source="dashboard",
            target="director",
            message_type=MessageType.ESCALATION_RESOLUTION,
            payload=payload,
        )
        sc._redis.xadd(stream, envelope.to_stream_dict())

    return {
        "escalation_id": escalation_id,
        "status": "DISMISSED",
        "message": "Guidance published to Director.",
    }


# ── Reports ──

@app.get("/api/reports")
def get_reports(
    project: str | None = Query(None),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    limit: int = Query(50),
):
    """Get audit session reports from SQLite (archived) and Redis (live)."""
    store = get_store()
    sc = get_stream_client()
    start = _parse_date_param(start_date)
    end = _parse_date_param(end_date)

    # Archived from SQLite
    archived = store.query_reports(
        project=project,
        start_date=start.isoformat() if start else None,
        end_date=end.isoformat() if end else None,
        limit=limit,
    )
    archived_ids = {r.get("report_id") for r in archived if r.get("report_id")}

    # Live from Redis audit:reports stream
    live = []
    try:
        results = sc._redis.xrange("audit:reports", count=limit)
        for stream_id, data in results:
            env = MessageEnvelope.from_stream_dict(data)
            p = env.payload
            rid = p.get("report_id")
            if rid and rid in archived_ids:
                continue
            if project and p.get("project") != project:
                continue
            if not _in_date_range(env.timestamp.isoformat(), start, end):
                continue
            live.append({
                "stream_id": stream_id,
                "timestamp": env.timestamp.isoformat(),
                "live": True,
                **p,
            })
    except Exception:
        pass

    # Also check audit:findings for report payloads (Director may publish there)
    try:
        results = sc._redis.xrange("audit:findings", count=500)
        for stream_id, data in results:
            env = MessageEnvelope.from_stream_dict(data)
            p = env.payload
            if not p.get("report_id"):
                continue
            rid = p.get("report_id")
            if rid in archived_ids:
                continue
            if any(l.get("report_id") == rid for l in live):
                continue
            if project and p.get("project") != project:
                continue
            if not _in_date_range(env.timestamp.isoformat(), start, end):
                continue
            live.append({
                "stream_id": stream_id,
                "timestamp": env.timestamp.isoformat(),
                "live": True,
                "source_stream": "audit:findings",
                **p,
            })
    except Exception:
        pass

    combined = live + archived
    combined.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return {"count": len(combined[:limit]), "reports": combined[:limit]}


# ── QDrant Semantic Search ──

@app.get("/api/search/{collection}")
def search_collection(
    collection: str,
    q: str = Query(..., description="Semantic search query"),
    project: str | None = Query(None),
    agent: str | None = Query(None),
    limit: int = Query(10, ge=1, le=50),
):
    if collection not in ALL_COLLECTIONS:
        raise HTTPException(status_code=400, detail=f"Unknown collection: {collection}")

    qb = get_qdrant()
    filters = {}
    if project:
        filters["project"] = project
    if agent:
        filters["agent"] = agent

    results = qb.search_similar(collection, q, limit, filters or None)
    return {
        "collection": collection,
        "query": q,
        "count": len(results),
        "results": results,
    }


# ── Evals ──

@app.get("/api/evals")
def get_evals(
    project: str | None = Query(None),
    eval_name: str | None = Query(None),
    agent: str | None = Query(None),
    passed: bool | None = Query(None),
    limit: int = Query(50),
):
    """Get eval results from QDrant."""
    qb = get_qdrant()
    filters = {}
    if project:
        filters["project"] = project
    if eval_name:
        filters["eval_name"] = eval_name
    if agent:
        filters["agent"] = agent
    if passed is not None:
        filters["passed"] = passed

    query = f"eval {eval_name or ''} {agent or ''}".strip() or "eval result"
    results = qb.search_similar("evals", query, limit, filters or None)

    # Sort by timestamp descending
    results.sort(key=lambda r: r.get("payload", {}).get("timestamp", ""), reverse=True)

    return {"count": len(results), "evals": results}


@app.get("/api/evals/summary")
def get_evals_summary(
    project: str | None = Query(None),
):
    """Aggregate eval scores by eval_name — used for dashboard overview."""
    qb = get_qdrant()
    filters = {"project": project} if project else None

    # Get a large sample of evals
    results = qb.search_similar("evals", "eval result test lint", 200, filters)

    summary = {}  # {eval_name: {total, passed, failed, avg_score, scores: []}}
    for r in results:
        p = r.get("payload", {})
        name = p.get("eval_name", "unknown")
        if name not in summary:
            summary[name] = {"eval_name": name, "total": 0, "passed": 0, "failed": 0, "scores": []}
        summary[name]["total"] += 1
        if p.get("passed"):
            summary[name]["passed"] += 1
        else:
            summary[name]["failed"] += 1
        score = p.get("score")
        if score is not None:
            summary[name]["scores"].append(score)

    # Compute averages
    for name, data in summary.items():
        scores = data.pop("scores")
        data["avg_score"] = sum(scores) / len(scores) if scores else None
        data["pass_rate"] = data["passed"] / data["total"] if data["total"] > 0 else None

    return {"summary": list(summary.values())}


# ── Data Quality ──

@app.get("/api/data-quality")
def get_data_quality(
    project: str | None = Query(None),
    limit: int = Query(20),
):
    qb = get_qdrant()
    results = qb.search_data_quality_events(
        "missing fields data quality", limit=limit, project=project
    )
    return {"count": len(results), "events": results}


# ── Sessions ──

@app.get("/api/sessions")
def get_sessions(
    project: str | None = Query(None),
    offset: int = Query(0),
    limit: int = Query(20),
):
    qb = get_qdrant()
    filters = {"project": project} if project else None

    # Primary source: sessions collection (written by session_end hook)
    session_results = qb.scroll_all("sessions", filters=filters, limit=500)

    sessions = []
    for s in session_results:
        p = s.get("payload", {})
        sid = p.get("session_id", "")
        if sid:
            sessions.append({"payload": p})

    # Sort by most recent first
    sessions.sort(
        key=lambda s: s.get("payload", {}).get("timestamp", ""),
        reverse=True,
    )

    # Apply offset/limit pagination
    page = sessions[offset:offset + limit]
    return {"count": len(sessions), "sessions": page}


@app.get("/api/sessions/{session_id}/hierarchy")
def get_session_hierarchy(
    session_id: str,
    gap_threshold: float = Query(5.0, description="Seconds of silence that indicate a new prompt turn"),
):
    """Build a hierarchical prompt-level view of a session.

    Prefers JSONL-parsed conversation turns (accurate promptId boundaries)
    when available. Falls back to timestamp gap heuristic for sessions
    that haven't been parsed yet.
    """
    qb = get_qdrant()

    # Try JSONL-parsed conversation turns first (ground truth)
    conv_turns = qb.get_conversation_turns(session_id)
    if conv_turns:
        # Separate root turns from subagent turns
        root_turns = []
        subagent_turns_by_type = {}  # agent_type -> [turn_payloads]
        for r in conv_turns:
            p = r.get("payload", {})
            if p.get("is_subagent"):
                agent_key = p.get("agent_type") or "subagent"
                subagent_turns_by_type.setdefault(agent_key, []).append(p)
            else:
                root_turns.append(p)

        # Sort subagent groups by turn_index
        for key in subagent_turns_by_type:
            subagent_turns_by_type[key].sort(key=lambda t: t.get("turn_index", 0))

        # Build a list of formatted subagent sections for matching
        def _format_subagent(agent_key, turns):
            return {
                "agent_type": agent_key,
                "turns": [{
                    "turn_index": t.get("turn_index", 0),
                    "user_prompt": t.get("user_prompt", ""),
                    "assistant_response": t.get("assistant_response", ""),
                    "tool_call_count": t.get("tool_call_count", 0),
                    "tool_call_names": t.get("tool_call_names", []),
                    "thinking_count": t.get("thinking_count", 0),
                    "events": t.get("events", []),
                    "event_count": t.get("entry_count", 0),
                } for t in turns],
            }

        # Track which subagent types have been claimed by a root turn
        claimed_subagents = set()

        result_turns = []
        for p in root_turns:
            spawns = p.get("subagent_spawns", [])

            # Collect subagent sections that belong to this turn
            inline_subagents = []
            for spawn_name in spawns:
                # Match by checking subagent agent_type against spawn descriptions
                for agent_key, sub_turns in subagent_turns_by_type.items():
                    if agent_key in claimed_subagents:
                        continue
                    # Match: agent_type contains the spawn name, or spawn is generic
                    if (spawn_name.lower() in agent_key.lower()
                            or agent_key.lower() in spawn_name.lower()
                            or spawn_name in ("general", "general-purpose", "Explore")):
                        inline_subagents.append(_format_subagent(agent_key, sub_turns))
                        claimed_subagents.add(agent_key)
                        break

            turn_data = {
                "turn_index": p.get("turn_index", 0),
                "prompt_id": p.get("prompt_id"),
                "start_time": p.get("timestamp"),
                "end_time": p.get("timestamp"),
                "event_count": p.get("entry_count", 0),
                "boundary_confidence": "high",
                "user_prompt": p.get("user_prompt", ""),
                "assistant_response": p.get("assistant_response", ""),
                "tool_call_count": p.get("tool_call_count", 0),
                "tool_call_names": p.get("tool_call_names", []),
                "thinking_count": p.get("thinking_count", 0),
                "subagent_spawns": spawns,
                "events": p.get("events", []),
                "subagents": inline_subagents,
            }
            result_turns.append(turn_data)

        # Any unclaimed subagents go on the last turn that had spawns, or the last turn
        unclaimed = [
            _format_subagent(k, v) for k, v in subagent_turns_by_type.items()
            if k not in claimed_subagents
        ]
        if unclaimed:
            # Find last turn with spawns, or just last turn
            target = next(
                (t for t in reversed(result_turns) if t.get("subagent_spawns")),
                result_turns[-1] if result_turns else None,
            )
            if target:
                target.setdefault("subagents", []).extend(unclaimed)

        project = root_turns[0].get("project", "") if root_turns else ""
        return {
            "session_id": session_id,
            "project": project,
            "source": "jsonl_parsed",
            "prompt_turns": result_turns,
        }

    # Fall back to gap-based heuristic
    # 1. Fetch all events for this session
    events = qb.get_session_events(session_id)
    if not events:
        return {"session_id": session_id, "project": "", "source": "empty", "prompt_turns": []}

    project = events[0].get("project", "")

    # 2. Group into prompt turns by timestamp gaps
    prompt_turns = []
    current_turn_events = []
    prev_epoch = None

    for event in events:
        epoch = event.get("timestamp_epoch", 0)

        if prev_epoch is not None and (epoch - prev_epoch) > gap_threshold:
            # Gap detected — close current turn, start new one
            if current_turn_events:
                prompt_turns.append(current_turn_events)
            current_turn_events = []

        current_turn_events.append(event)
        prev_epoch = epoch

    # Close final turn
    if current_turn_events:
        prompt_turns.append(current_turn_events)

    # 3. Build response with child session stubs
    result_turns = []
    for i, turn_events in enumerate(prompt_turns):
        # Compute boundary confidence based on gap size
        if i == 0:
            boundary_confidence = "high"
        else:
            prev_turn_end = prompt_turns[i - 1][-1].get("timestamp_epoch", 0)
            turn_start = turn_events[0].get("timestamp_epoch", 0)
            gap = turn_start - prev_turn_end
            if gap > 10:
                boundary_confidence = "high"
            elif gap > gap_threshold:
                boundary_confidence = "medium"
            else:
                boundary_confidence = "low"

        # Format events, resolve child session stubs for agent spawns
        formatted_events = []
        for event in turn_events:
            raw_type = event.get("_event_type", "tool_call")
            # Normalize to frontend-expected types
            event_type = "tool_use" if raw_type == "tool_call" else raw_type
            formatted = {
                "type": event_type,
                "timestamp": event.get("timestamp"),
                "timestamp_epoch": event.get("timestamp_epoch"),
            }

            if raw_type == "tool_call":
                formatted.update({
                    "tool_name": event.get("tool_name"),
                    "status": event.get("status"),
                    "file_path": event.get("file_path"),
                    "agent": event.get("agent"),
                    "text": event.get("_text", ""),
                    "input_summary": event.get("input_summary"),
                    "output_summary": event.get("output_summary"),
                })
            elif raw_type == "agent_spawn":
                child_stub = _find_child_session(qb, session_id, event)
                formatted.update({
                    "child_agent": event.get("child_agent"),
                    "description": event.get("description"),
                    "child_session": child_stub,
                })
            elif raw_type == "code_change":
                formatted.update({
                    "file_path": event.get("file_path"),
                    "operation": event.get("operation"),
                    "change_id": event.get("change_id"),
                    "diff_summary": event.get("diff_summary"),
                })

            formatted_events.append(formatted)

        result_turns.append({
            "turn_index": i,
            "start_time": turn_events[0].get("timestamp"),
            "end_time": turn_events[-1].get("timestamp"),
            "event_count": len(turn_events),
            "boundary_confidence": boundary_confidence,
            "events": formatted_events,
        })

    return {
        "session_id": session_id,
        "project": project,
        "source": "timestamp_inferred",
        "prompt_turns": result_turns,
    }


def _find_child_session(qb, parent_session_id: str, spawn_event: dict) -> dict | None:
    """Attempt to find a child session that started shortly after this spawn.

    Returns a stub dict or None if no match found.
    """
    spawn_epoch = spawn_event.get("timestamp_epoch", 0)
    if not spawn_epoch:
        return None

    # Search for tool calls that started within 30s after the spawn
    try:
        candidates = qb.scroll_all(
            "tool_calls",
            filters={
                "timestamp_epoch__gte": spawn_epoch,
                "timestamp_epoch__lte": spawn_epoch + 30,
            },
            limit=50,
        )
    except Exception:
        return None

    # Group by session_id, find sessions that aren't the parent
    child_sessions = {}
    for r in candidates:
        p = r.get("payload", {})
        sid = p.get("session_id", "")
        if sid and sid != parent_session_id:
            if sid not in child_sessions:
                child_sessions[sid] = {
                    "session_id": sid,
                    "first_epoch": p.get("timestamp_epoch", 0),
                    "event_count": 0,
                    "project": p.get("project", ""),
                }
            child_sessions[sid]["event_count"] += 1

    if not child_sessions:
        return None

    # Pick the session that started closest to the spawn time
    best = min(child_sessions.values(), key=lambda s: abs(s["first_epoch"] - spawn_epoch))

    # Count total events for the child session
    try:
        total = qb.count("tool_calls", {"session_id": best["session_id"]})
    except Exception:
        total = best["event_count"]

    confidence = "high" if len(child_sessions) == 1 else "medium" if len(child_sessions) <= 3 else "low"

    return {
        "session_id": best["session_id"],
        "child_agent": spawn_event.get("child_agent", ""),
        "description": spawn_event.get("description", ""),
        "event_count": total,
        "link_confidence": confidence,
    }


# ── Tool Calls ──

@app.get("/api/tool-calls")
def get_tool_calls(
    session_id: str | None = Query(None),
    project: str | None = Query(None),
    tool_name: str | None = Query(None),
    limit: int = Query(100),
):
    qb = get_qdrant()
    filters = {}
    if session_id:
        filters["session_id"] = session_id
    if project:
        filters["project"] = project
    if tool_name:
        filters["tool_name"] = tool_name

    # Use the session_id as the query text when filtering by session
    # This gives better semantic relevance than a generic query
    if session_id:
        query = f"session {session_id} tool call"
    elif tool_name:
        query = f"tool call {tool_name}"
    else:
        query = "recent tool call"

    results = qb.search_similar("tool_calls", query, limit, filters or None)

    # Sort by timestamp
    results.sort(key=lambda r: r.get("payload", {}).get("timestamp", ""))

    return {"count": len(results), "tool_calls": results}


# ── Task Pipeline ──

@app.get("/api/task-pipeline")
def get_task_pipeline():
    """Get task counts per auditor: assigned, completed, failed, pending.

    Reads audit:tasks for assignments and audit:status for completions.
    """
    sc = get_stream_client()

    # Count tasks assigned per auditor
    assigned = {}  # {auditor: count}
    task_ids_by_auditor = {}  # {auditor: set(task_id)}
    try:
        results = sc._redis.xrange("audit:tasks", count=1000)
        for stream_id, data in results:
            try:
                env = MessageEnvelope.from_stream_dict(data)
                p = env.payload
                auditor = (
                    p.get("target_auditor")
                    or env.target.removeprefix("auditor:").strip()
                    or "unknown"
                )
                assigned[auditor] = assigned.get(auditor, 0) + 1
                tid = p.get("task_id")
                if tid:
                    task_ids_by_auditor.setdefault(auditor, set()).add(tid)
            except Exception:
                continue
    except Exception:
        pass

    # Count completions and failures from status stream
    completed = {}  # {auditor: count}
    failed = {}  # {auditor: count}
    try:
        results = sc._redis.xrange("audit:status", count=2000)
        for stream_id, data in results:
            try:
                env = MessageEnvelope.from_stream_dict(data)
                p = env.payload
                status_type = p.get("status_type", "")
                auditor = p.get("auditor", "unknown")
                # Normalize auditor name (remove "auditor:" prefix if present)
                if auditor.startswith("auditor:"):
                    auditor = auditor[len("auditor:"):]

                if status_type == "task_complete":
                    completed[auditor] = completed.get(auditor, 0) + 1
                elif status_type == "task_failed":
                    failed[auditor] = failed.get(auditor, 0) + 1
            except Exception:
                continue
    except Exception:
        pass

    # Build per-auditor pipeline
    all_auditors = set(assigned.keys()) | set(completed.keys()) | set(failed.keys())
    pipeline = {}
    for auditor in sorted(all_auditors):
        a = assigned.get(auditor, 0)
        c = completed.get(auditor, 0)
        f = failed.get(auditor, 0)
        pipeline[auditor] = {
            "assigned": a,
            "completed": c,
            "failed": f,
            "pending": max(0, a - c - f),
        }

    return {"pipeline": pipeline}


# ── Summary Stats ──

@app.get("/api/stats")
def get_stats(
    project: str | None = Query(None),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    store = get_store()
    sc = get_stream_client()

    start = _parse_date_param(start_date)
    end = _parse_date_param(end_date)

    # Archived stats from SQLite
    db_stats = store.get_stats(
        project=project,
        start_date=start.isoformat() if start else None,
        end_date=end.isoformat() if end else None,
    )

    findings_by_severity = db_stats.get("findings_by_severity", {})
    findings_by_auditor = db_stats.get("findings_by_auditor", {})
    total_findings = db_stats.get("total_findings", 0)

    # Merge with live findings from Redis
    for f in _read_live_findings(sc, limit=500):
        if project and f.get("project") != project:
            continue
        if not _in_date_range(f.get("timestamp", ""), start, end):
            continue
        sev = f.get("severity", "").lower()
        findings_by_severity[sev] = findings_by_severity.get(sev, 0) + 1
        auditor = f.get("auditor_type", f.get("auditor", "director"))
        findings_by_auditor[auditor] = findings_by_auditor.get(auditor, 0) + 1
        total_findings += 1

    return {
        "findings_by_severity": findings_by_severity,
        "findings_by_auditor": findings_by_auditor,
        "total_findings": total_findings,
        "active_projects": _load_projects(),
        "stream_findings_total": sc.stream_length("audit:findings"),
        "stream_directives_total": sc.stream_length("audit:directives"),
        "stream_escalations_total": sc.stream_length("audit:escalations"),
    }
