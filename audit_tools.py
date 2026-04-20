"""
MCP tool handlers for the audit platform.

Provides two MCP servers:
- director_server: full access (qdrant_query, stream_publish, stream_read, read_file)
- auditor_server: scoped access (qdrant_query, stream_publish, stream_read)

These are backed by the existing ObservabilityClient, QdrantBackend, and StreamClient.
"""

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from claude_agent_sdk import tool, create_sdk_mcp_server

sys.path.insert(0, os.path.dirname(__file__))

from observability.qdrant_backend import QdrantBackend, ALL_COLLECTIONS
from observability.stream_client import StreamClient
from observability.audit_store import AuditStore
from observability.messages import (
    MessageEnvelope, MessageType, build_message,
    FindingPayload, TaskPayload, StatusPayload,
    DirectivePayload, EscalationPayload, ReportPayload,
    PromotionPayload, EscalationResolutionPayload,
    STREAM_FINDINGS, STREAM_TASKS, STREAM_STATUS,
    STREAM_DIRECTIVES, STREAM_ESCALATIONS,
    project_directive_stream, project_compliance_stream,
    project_promotion_stream, project_promotion_ack_stream,
    project_escalation_resolution_stream,
)

logger = logging.getLogger(__name__)

# Lazy-initialized backends (created on first tool call, not at import time)
_qdrant: QdrantBackend | None = None
_stream_clients: dict[str, StreamClient] = {}
_audit_store: AuditStore | None = None


def _get_qdrant() -> QdrantBackend:
    global _qdrant
    if _qdrant is None:
        _qdrant = QdrantBackend()
    return _qdrant


def _get_audit_store() -> AuditStore:
    global _audit_store
    if _audit_store is None:
        _audit_store = AuditStore()
    return _audit_store


def _get_stream_client(role: str) -> StreamClient:
    if role not in _stream_clients:
        if role == "director":
            _stream_clients[role] = StreamClient.for_director()
        else:
            auditor_type = role.split(":")[1] if ":" in role else role
            _stream_clients[role] = StreamClient.for_auditor(auditor_type)
    return _stream_clients[role]


def _inject_audit_cycle_id(payload) -> None:
    """Stamp AUDIT_CYCLE_ID from env var on any dict payload that doesn't
    already have one. Called universally from stream_publish — applies to
    findings, directives, tasks, and every other message type so the
    directive_lifecycle view and time-to-verification chart have reliable
    per-cycle markers. See eager-giggling-rivest.md (Gap 1, Issue #2).
    """
    if not isinstance(payload, dict):
        return
    if payload.get("audit_cycle_id"):
        return
    cycle_id = os.environ.get("AUDIT_CYCLE_ID")
    if cycle_id:
        payload["audit_cycle_id"] = cycle_id


# ── Tool: qdrant_query ──

@tool(
    "qdrant_query",
    "Query a QDrant collection with semantic search and optional filters. "
    "Collections: tool_calls, hallucinations, agent_spawns, evals, sessions, "
    "prompts, code_changes, bugs, findings, directives, compliance, escalations, data_quality. "
    "Returns matching documents with similarity scores. "
    "Filter keys support operators: field (exact match), field__gte, field__lte, "
    "field__gt, field__lt, field__ne. "
    "Set count_only=true to skip vector search and return only a document count.",
    {
        "collection": str,
        "query": str,
        "limit": int,
        "filters": str,
        "count_only": bool,
    },
)
async def qdrant_query(args):
    collection = args["collection"]
    query_text = args.get("query", "")
    limit = args.get("limit", 10)
    filters_raw = args.get("filters")
    count_only = args.get("count_only", False)

    if collection not in ALL_COLLECTIONS:
        return {"content": [{"type": "text", "text": json.dumps({
            "error": f"Unknown collection: {collection}",
            "available": ALL_COLLECTIONS,
        })}]}

    filters = None
    if filters_raw:
        try:
            filters = json.loads(filters_raw) if isinstance(filters_raw, str) else filters_raw
        except json.JSONDecodeError:
            return {"content": [{"type": "text", "text": json.dumps({
                "error": f"Invalid filters JSON: {filters_raw}",
            })}]}

    try:
        qb = _get_qdrant()

        if count_only:
            count = qb.count(collection, filters)
            return {"content": [{"type": "text", "text": json.dumps({
                "collection": collection,
                "filters": filters,
                "count": count,
            }, default=str)}]}

        results = qb.search_similar(collection, query_text, limit, filters)
        return {"content": [{"type": "text", "text": json.dumps({
            "collection": collection,
            "query": query_text,
            "count": len(results),
            "results": results,
        }, default=str)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": json.dumps({
            "error": str(e),
        })}]}


# ── Tool: qdrant_compare_windows ──

@tool(
    "qdrant_compare_windows",
    "Compare two consecutive time windows in a QDrant collection using semantic search. "
    "Useful for trend detection: compare this week vs last week, or this session vs previous. "
    "window_type must be 'days' or 'sessions'. window_size is the number of units per window. "
    "Returns results for both windows so patterns can be compared.",
    {
        "collection": str,
        "query": str,
        "window_type": str,
        "window_size": int,
        "filters": str,
        "limit": int,
    },
)
async def qdrant_compare_windows(args):
    collection = args["collection"]
    query_text = args["query"]
    window_type = args["window_type"]
    window_size = args["window_size"]
    filters_raw = args.get("filters")
    limit = args.get("limit", 5)

    if collection not in ALL_COLLECTIONS:
        return {"content": [{"type": "text", "text": json.dumps({
            "error": f"Unknown collection: {collection}",
            "available": ALL_COLLECTIONS,
        })}]}

    if window_type not in ("days", "sessions"):
        return {"content": [{"type": "text", "text": json.dumps({
            "error": f"window_type must be 'days' or 'sessions', got: {window_type}",
        })}]}

    filters = None
    if filters_raw:
        try:
            filters = json.loads(filters_raw) if isinstance(filters_raw, str) else filters_raw
        except json.JSONDecodeError:
            return {"content": [{"type": "text", "text": json.dumps({
                "error": f"Invalid filters JSON: {filters_raw}",
            })}]}

    try:
        qb = _get_qdrant()
        result = qb.compare_windows(
            collection, query_text, window_type, window_size, filters, limit
        )
        return {"content": [{"type": "text", "text": json.dumps(result, default=str)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": json.dumps({
            "error": str(e),
        })}]}


# ── Tool: qdrant_timeline ──

@tool(
    "qdrant_timeline",
    "Query multiple QDrant collections and arrange results chronologically into a unified timeline. "
    "Useful for cross-collection investigations: trace an event across tool_calls, findings, "
    "escalations, and compliance records. "
    "collections must be a JSON array of collection names. "
    "anchor_collection optionally pins the timeline to a specific collection's timestamps. "
    "time_window_minutes controls how wide the time window is around matched events.",
    {
        "query": str,
        "collections": str,
        "time_window_minutes": int,
        "filters": str,
        "anchor_collection": str,
        "limit_per_collection": int,
    },
)
async def qdrant_timeline(args):
    query_text = args["query"]
    collections_raw = args["collections"]
    time_window_minutes = args.get("time_window_minutes", 30)
    filters_raw = args.get("filters")
    anchor_collection = args.get("anchor_collection")
    limit_per_collection = args.get("limit_per_collection", 10)

    try:
        collections = json.loads(collections_raw) if isinstance(collections_raw, str) else collections_raw
    except json.JSONDecodeError:
        return {"content": [{"type": "text", "text": json.dumps({
            "error": f"Invalid collections JSON: {collections_raw}",
        })}]}

    if not isinstance(collections, list):
        return {"content": [{"type": "text", "text": json.dumps({
            "error": "collections must be a JSON array of collection names",
        })}]}

    invalid = [c for c in collections if c not in ALL_COLLECTIONS]
    if invalid:
        return {"content": [{"type": "text", "text": json.dumps({
            "error": f"Unknown collections: {invalid}",
            "available": ALL_COLLECTIONS,
        })}]}

    filters = None
    if filters_raw:
        try:
            filters = json.loads(filters_raw) if isinstance(filters_raw, str) else filters_raw
        except json.JSONDecodeError:
            return {"content": [{"type": "text", "text": json.dumps({
                "error": f"Invalid filters JSON: {filters_raw}",
            })}]}

    try:
        qb = _get_qdrant()
        result = qb.timeline(
            query_text, collections,
            anchor_collection=anchor_collection,
            time_window_minutes=time_window_minutes,
            filters=filters,
            limit_per_collection=limit_per_collection,
        )
        return {"content": [{"type": "text", "text": json.dumps(result, default=str)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": json.dumps({
            "error": str(e),
        })}]}


# ── Tool: stream_publish ──

@tool(
    "stream_publish",
    "Publish a message to a Redis audit stream. "
    "Internal streams: audit:findings, audit:tasks, audit:status, audit:directives, audit:escalations. "
    "Per-project streams: directives:{project}, compliance:{project}. "
    "Payload must be a JSON string matching the stream's expected format.",
    {
        "stream": str,
        "message_type": str,
        "target": str,
        "payload": str,
        "correlation_id": str,
    },
)
async def stream_publish(args):
    stream = args["stream"]
    msg_type = args["message_type"]
    target = args.get("target", "")
    payload_raw = args.get("payload", "{}")
    correlation_id = args.get("correlation_id")

    try:
        payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
    except json.JSONDecodeError:
        return {"content": [{"type": "text", "text": json.dumps({
            "error": f"Invalid payload JSON: {payload_raw}",
        })}]}

    # Universal: stamp audit_cycle_id on every payload so findings, directives,
    # tasks, and all other message types get the cycle marker needed by the
    # directive_lifecycle view and time-to-verification chart. Gap 1 Issue #2.
    _inject_audit_cycle_id(payload)

    try:
        # Use the auditor's own identity when available
        auditor_type = os.environ.get("AUDITOR_TYPE")
        if auditor_type:
            role = f"auditor:{auditor_type}"
        else:
            role = "director"

        client = _get_stream_client(role)

        # Deterministic finding_id from content hash — makes findings idempotent.
        # If the same auditor publishes the same finding twice (task replay, restart),
        # the second publish overwrites rather than creating a duplicate.
        # Preserve the agent's original finding_id as agent_finding_ref.
        if msg_type == "finding" and isinstance(payload, dict):
            agent_ref = payload.get("finding_id", "")
            # Derive deterministic ID from auditor + session + claim content
            dedup_key = "|".join([
                payload.get("auditor_type", ""),
                payload.get("target_session", ""),
                payload.get("claim", "")[:200],
                payload.get("finding_type", ""),
            ])
            payload["finding_id"] = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"finding:{dedup_key}"))
            if agent_ref:
                payload["agent_finding_ref"] = agent_ref

        # Ensure every directive has a stable directive_id
        if msg_type == "directive" and isinstance(payload, dict):
            if not payload.get("directive_id"):
                dedup_key = "|".join([
                    payload.get("target_agent", ""),
                    payload.get("title", payload.get("content", ""))[:200],
                    payload.get("triggered_by", payload.get("triggered_by_finding", "")),
                    payload.get("directive_type", ""),
                ])
                payload["directive_id"] = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"directive:{dedup_key}"))

        envelope = MessageEnvelope(
            stream=stream,
            source=role,
            target=target,
            correlation_id=correlation_id,
            message_type=MessageType(msg_type),
            payload=payload,
        )

        stream_id = client._redis.xadd(stream, envelope.to_stream_dict())

        return {"content": [{"type": "text", "text": json.dumps({
            "published": True,
            "stream": stream,
            "stream_id": str(stream_id),
            "message_type": msg_type,
        })}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": json.dumps({
            "error": str(e),
        })}]}


# ── Tool: stream_read ──

@tool(
    "stream_read",
    "Read messages from a Redis audit stream. "
    "Returns pending messages from the stream's consumer group. "
    "Messages are automatically acknowledged after reading.",
    {
        "stream": str,
        "count": int,
    },
)
async def stream_read(args):
    stream = args["stream"]
    count = args.get("count", 10)

    try:
        # Use the auditor's own identity when available so each auditor
        # gets its own consumer group on audit:tasks (broadcast, not competing)
        auditor_type = os.environ.get("AUDITOR_TYPE")
        if auditor_type:
            role = f"auditor:{auditor_type}"
            client = _get_stream_client(role)
        else:
            role = "director"
            client = _get_stream_client(role)

        # Ensure consumer group exists
        group = f"group:{role}"
        try:
            client._redis.xgroup_create(stream, group, id="0", mkstream=True)
        except Exception:
            pass  # group already exists

        results = client._redis.xreadgroup(
            groupname=group,
            consumername=role,
            streams={stream: ">"},
            count=count,
            block=500,  # 500ms timeout — return quickly if no messages
        )

        messages = []
        skipped = 0
        if results:
            for _stream_name, entries in results:
                for stream_id, data in entries:
                    try:
                        envelope = MessageEnvelope.from_stream_dict(data)

                        # Filter tasks: auditors only see tasks targeted at them
                        if (stream == "audit:tasks" and auditor_type
                                and envelope.target not in (
                                    role, auditor_type, f"auditor-{auditor_type}")):
                            # ACK so it doesn't re-deliver, but don't return it
                            client._redis.xack(stream, group, stream_id)
                            skipped += 1
                            continue

                        messages.append({
                            "stream_id": str(stream_id),
                            "message_id": envelope.message_id,
                            "source": envelope.source,
                            "target": envelope.target,
                            "message_type": envelope.message_type.value,
                            "timestamp": envelope.timestamp.isoformat(),
                            "correlation_id": envelope.correlation_id,
                            "payload": envelope.payload,
                        })
                        client._redis.xack(stream, group, stream_id)
                    except Exception as e:
                        logger.error("Failed to parse message %s: %s", stream_id, e)

        result = {
            "stream": stream,
            "count": len(messages),
            "messages": messages,
        }
        if skipped:
            result["skipped_not_targeted"] = skipped

        return {"content": [{"type": "text", "text": json.dumps(result, default=str)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": json.dumps({
            "error": str(e),
        })}]}


# ── Tool: read_file (Director only) ──

@tool(
    "read_file",
    "Read a file from the filesystem. For on-demand investigation of "
    "external project code, agent definitions, or configuration files. "
    "Returns the file contents as text.",
    {
        "path": str,
    },
)
async def read_file(args):
    file_path = args["path"]

    try:
        path = Path(file_path)
        if not path.exists():
            return {"content": [{"type": "text", "text": json.dumps({
                "error": f"File not found: {file_path}",
            })}]}

        if not path.is_file():
            return {"content": [{"type": "text", "text": json.dumps({
                "error": f"Not a file: {file_path}",
            })}]}

        # Limit file size to prevent context overflow
        size = path.stat().st_size
        if size > 500_000:
            return {"content": [{"type": "text", "text": json.dumps({
                "error": f"File too large ({size} bytes). Max 500KB.",
            })}]}

        content = path.read_text(encoding="utf-8", errors="replace")
        return {"content": [{"type": "text", "text": json.dumps({
            "path": file_path,
            "size_bytes": size,
            "content": content,
        })}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": json.dumps({
            "error": str(e),
        })}]}


# ── Tool: promote_directive ──

@tool(
    "promote_directive",
    "Promote a verified directive to a standing directive in an external project. "
    "This records the full deliberation (classification reasoning, supersession analysis, "
    "alternatives considered) to SQLite and publishes exact add/remove verbiage to the "
    "project's promotion stream. The external hook will apply the changes and ack back. "
    "Use verify_promotion_ack to check the result.\n\n"
    "decision_type: PROMOTE, SUPERSEDE_AND_PROMOTE, or DECLINE_PROMOTION.\n"
    "For DECLINE_PROMOTION, no stream message is sent — only the decision record is stored.",
    {
        "project": str,
        "directive_id": str,
        "decision_type": str,
        "add_verbiage": str,
        "remove_verbiage": str,
        "target_agents": str,
        "classification_reasoning": str,
        "supersession_reasoning": str,
        "alternatives_considered": str,
        "rationale": str,
        "standing_file_snapshot": str,
        "conflict_candidates": str,
        "inputs": str,
        "outcome_superseded_ids": str,
    },
)
async def promote_directive(args):
    project = args["project"]
    directive_id = args["directive_id"]
    decision_type = args["decision_type"]
    add_verbiage = args.get("add_verbiage", "")
    remove_verbiage = args.get("remove_verbiage") or None
    target_agents_raw = args.get("target_agents", "[]")
    classification_reasoning = args.get("classification_reasoning", "")
    supersession_reasoning = args.get("supersession_reasoning", "")
    alternatives_considered = args.get("alternatives_considered", "")
    rationale = args.get("rationale", "")
    standing_file_snapshot_raw = args.get("standing_file_snapshot", "[]")
    conflict_candidates_raw = args.get("conflict_candidates", "[]")
    inputs_raw = args.get("inputs", "{}")
    outcome_superseded_ids_raw = args.get("outcome_superseded_ids", "[]")

    try:
        target_agents = json.loads(target_agents_raw) if isinstance(target_agents_raw, str) else target_agents_raw
        standing_file_snapshot = json.loads(standing_file_snapshot_raw) if isinstance(standing_file_snapshot_raw, str) else standing_file_snapshot_raw
        conflict_candidates = json.loads(conflict_candidates_raw) if isinstance(conflict_candidates_raw, str) else conflict_candidates_raw
        inputs = json.loads(inputs_raw) if isinstance(inputs_raw, str) else inputs_raw
        outcome_superseded_ids = json.loads(outcome_superseded_ids_raw) if isinstance(outcome_superseded_ids_raw, str) else outcome_superseded_ids_raw
    except json.JSONDecodeError as e:
        return {"content": [{"type": "text", "text": json.dumps({
            "error": f"Invalid JSON in arguments: {e}",
        })}]}

    promotion_id = f"PROMO-{uuid.uuid4().hex[:12]}"
    cycle_id = os.environ.get("AUDIT_CYCLE_ID")

    try:
        store = _get_audit_store()

        # Record the full deliberation
        decision = {
            "promotion_id": promotion_id,
            "directive_id": directive_id,
            "project": project,
            "decision_type": decision_type,
            "audit_cycle_id": cycle_id,
            "classification_reasoning": classification_reasoning,
            "supersession_reasoning": supersession_reasoning,
            "alternatives_considered": alternatives_considered,
            "rationale": rationale,
            "add_verbiage": add_verbiage,
            "remove_verbiage": remove_verbiage,
            "target_agents": target_agents,
            "standing_file_snapshot": standing_file_snapshot,
            "conflict_candidates": conflict_candidates,
            "inputs": inputs,
            "outcome_superseded_ids": outcome_superseded_ids,
            "status": "DECLINED" if decision_type == "DECLINE_PROMOTION" else "PENDING_ACK",
        }
        store.insert_promotion_decision(decision)

        # For DECLINE_PROMOTION, we're done — no stream message
        if decision_type == "DECLINE_PROMOTION":
            return {"content": [{"type": "text", "text": json.dumps({
                "promotion_id": promotion_id,
                "decision_type": decision_type,
                "status": "DECLINED",
                "message": "Promotion declined. Decision record stored in SQLite.",
            })}]}

        # Publish promotion instructions to the project's stream
        client = _get_stream_client("director")
        payload = PromotionPayload(
            promotion_id=promotion_id,
            directive_id=directive_id,
            decision_type=decision_type,
            add_verbiage=add_verbiage,
            remove_verbiage=remove_verbiage,
            target_agents=target_agents,
            audit_cycle_id=cycle_id,
        )
        client.publish_project_promotion(project, payload)

        return {"content": [{"type": "text", "text": json.dumps({
            "promotion_id": promotion_id,
            "decision_type": decision_type,
            "status": "PENDING_ACK",
            "stream": project_promotion_stream(project),
            "message": "Promotion published. Use verify_promotion_ack to check the external project's response.",
        })}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": json.dumps({
            "error": str(e),
        })}]}


# ── Tool: verify_promotion_ack ──

@tool(
    "verify_promotion_ack",
    "Read and verify promotion acknowledgments from an external project. "
    "Compares the verbiage the external hook actually wrote against what was intended. "
    "If satisfactory: archives the promotion as VERIFIED and creates the standing directive in SQLite. "
    "If unsatisfactory: creates a PROMOTION_FAILURE escalation with AWAITING_USER status.\n\n"
    "judgment: SATISFACTORY or UNSATISFACTORY.\n"
    "judgment_reasoning: Why the ack does or does not match intent.",
    {
        "project": str,
        "promotion_id": str,
        "judgment": str,
        "judgment_reasoning": str,
        "escalation_summary": str,
    },
)
async def verify_promotion_ack(args):
    project = args["project"]
    promotion_id = args["promotion_id"]
    judgment = args["judgment"].upper()
    judgment_reasoning = args.get("judgment_reasoning", "")
    escalation_summary = args.get("escalation_summary", "")

    if judgment not in ("SATISFACTORY", "UNSATISFACTORY"):
        return {"content": [{"type": "text", "text": json.dumps({
            "error": "judgment must be SATISFACTORY or UNSATISFACTORY",
        })}]}

    try:
        store = _get_audit_store()

        # Look up the promotion decision
        decisions = store.query_promotion_decisions(
            project=project, limit=50,
        )
        decision = next((d for d in decisions if d["promotion_id"] == promotion_id), None)

        if not decision:
            return {"content": [{"type": "text", "text": json.dumps({
                "error": f"Promotion {promotion_id} not found in SQLite",
            })}]}

        if judgment == "SATISFACTORY":
            # Check if this promotion was already verified (prevents double-creation)
            existing = store.query_standing_directives(project=project, limit=500)
            already_created = [
                sd for sd in existing
                if sd.get("promotion_id") == promotion_id and sd.get("status") == "ACTIVE"
            ]
            if already_created:
                return {"content": [{"type": "text", "text": json.dumps({
                    "promotion_id": promotion_id,
                    "status": "ALREADY_VERIFIED",
                    "standing_directive_id": already_created[0]["standing_directive_id"],
                    "message": "This promotion was already verified. Standing directive exists.",
                })}]}

            # Update promotion status
            store.update_promotion_status(promotion_id, "VERIFIED")

            # Create standing directive record
            standing_id = f"SD-{uuid.uuid4().hex[:8]}"
            store.insert_standing_directive({
                "standing_directive_id": standing_id,
                "project": project,
                "promotion_id": promotion_id,
                "verbiage": decision.get("add_verbiage", ""),
            })

            # Supersede old standing directives if applicable
            superseded_ids = decision.get("outcome_superseded_ids", [])
            for old_id in superseded_ids:
                store.supersede_standing_directive(old_id, standing_id)

            return {"content": [{"type": "text", "text": json.dumps({
                "promotion_id": promotion_id,
                "status": "VERIFIED",
                "standing_directive_id": standing_id,
                "superseded": superseded_ids,
                "message": "Promotion verified. Standing directive created in SQLite.",
            })}]}

        else:  # UNSATISFACTORY
            # Update promotion status
            store.update_promotion_status(promotion_id, "ESCALATED")

            # Create PROMOTION_FAILURE escalation
            escalation_id = f"ESC-{uuid.uuid4().hex[:12]}"
            cycle_id = os.environ.get("AUDIT_CYCLE_ID")
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()

            store.archive_escalation(
                stream_id="",
                timestamp=now,
                payload={
                    "escalation_id": escalation_id,
                    "escalation_type": "PROMOTION_FAILURE",
                    "severity": "critical",
                    "project": project,
                    "promotion_id": promotion_id,
                    "directive_id": decision.get("directive_id", ""),
                    "summary": escalation_summary or judgment_reasoning,
                    "recommended_action": "Review promotion failure and provide guidance",
                    "resolution_status": "AWAITING_USER",
                },
            )
            store.commit()

            # Add Director's initial context message to the thread
            store.insert_escalation_message(
                escalation_id=escalation_id,
                author="director",
                content=judgment_reasoning,
            )

            # Also publish to audit:escalations so it appears in Redis
            client = _get_stream_client("director")
            esc_payload = EscalationPayload(
                escalation_id=escalation_id,
                escalation_type="PROMOTION_FAILURE",
                severity="critical",
                directive_id=decision.get("directive_id"),
                summary=escalation_summary or judgment_reasoning,
                recommended_action="Review promotion failure and provide guidance",
                metrics={"promotion_id": promotion_id, "project": project},
            )
            client.publish_escalation(esc_payload)

            return {"content": [{"type": "text", "text": json.dumps({
                "promotion_id": promotion_id,
                "status": "ESCALATED",
                "escalation_id": escalation_id,
                "message": "Promotion failed verification. Critical escalation created — awaiting user guidance.",
            })}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": json.dumps({
            "error": str(e),
        })}]}


# ── Tool: create_escalation ──

@tool(
    "create_escalation",
    "Create a formal escalation to the user. This writes to both SQLite and Redis, "
    "sets status to AWAITING_USER, and starts a conversation thread. "
    "You MUST use this tool whenever you decide to escalate — natural language "
    "statements like 'escalating to the user' without calling this tool are a bug.",
    {
        "escalation_type": str,
        "severity": str,
        "project": str,
        "summary": str,
        "recommended_action": str,
        "subject_agent": str,
        "directive_id": str,
        "finding_ids": str,
        "impact_assessment": str,
        "metrics": str,
    },
)
async def create_escalation(args):
    escalation_type = args["escalation_type"]
    severity = args.get("severity", "high")
    project = args.get("project", "")
    summary = args["summary"]
    recommended_action = args.get("recommended_action", "")
    subject_agent = args.get("subject_agent")
    directive_id = args.get("directive_id")
    finding_ids_raw = args.get("finding_ids", "[]")
    impact_assessment = args.get("impact_assessment", "")
    metrics_raw = args.get("metrics", "{}")

    try:
        finding_ids = json.loads(finding_ids_raw) if isinstance(finding_ids_raw, str) else finding_ids_raw
    except json.JSONDecodeError:
        finding_ids = []

    try:
        metrics = json.loads(metrics_raw) if isinstance(metrics_raw, str) else metrics_raw
    except json.JSONDecodeError:
        metrics = {}

    try:
        escalation_id = f"ESC-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()

        # 1. Write escalation + initial thread message atomically (HARDEN-003).
        store = _get_audit_store()
        store.create_escalation_with_thread(
            escalation_id=escalation_id,
            escalation_type=escalation_type,
            severity=severity,
            project=project,
            summary=summary,
            subject_agent=subject_agent or "",
            directive_id=directive_id or "",
            finding_ids=finding_ids,
            recommended_action=recommended_action,
            impact_assessment=impact_assessment,
            metrics=metrics,
            resolution_status="AWAITING_USER",
            timestamp=now,
            initial_message_author="director",
        )

        # 2. Publish to Redis for real-time visibility
        client = _get_stream_client("director")
        esc_payload = EscalationPayload(
            escalation_id=escalation_id,
            escalation_type=escalation_type,
            severity=severity,
            subject_agent=subject_agent,
            directive_id=directive_id,
            finding_ids=finding_ids,
            summary=summary,
            impact_assessment=impact_assessment,
            recommended_action=recommended_action,
            metrics=metrics,
        )
        client.publish_escalation(esc_payload)

        return {"content": [{"type": "text", "text": json.dumps({
            "created": True,
            "escalation_id": escalation_id,
            "escalation_type": escalation_type,
            "severity": severity,
            "project": project,
            "resolution_status": "AWAITING_USER",
            "message": f"Escalation {escalation_id} created and published. Awaiting user guidance.",
        })}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": json.dumps({
            "error": str(e),
        })}]}


# ── Tool: read_escalation_resolutions ──

@tool(
    "read_escalation_resolutions",
    "Read escalation resolution guidance from the user (published via the dashboard). "
    "Returns dismissed escalations with the user's final guidance message and full "
    "conversation history. Use resolve_escalation after acting on the guidance.",
    {
        "project": str,
        "count": int,
    },
)
async def read_escalation_resolutions(args):
    project = args["project"]
    count = args.get("count", 10)

    try:
        client = _get_stream_client("director")
        stream = project_escalation_resolution_stream(project)

        # Ensure consumer group
        try:
            client._redis.xgroup_create(stream, "group:director", id="0", mkstream=True)
        except Exception:
            pass

        results = client._redis.xreadgroup(
            groupname="group:director",
            consumername="director",
            streams={stream: ">"},
            count=count,
        )

        resolutions = []
        if results:
            for _stream_name, entries in results:
                for stream_id, data in entries:
                    try:
                        envelope = MessageEnvelope.from_stream_dict(data)
                        resolutions.append({
                            "stream_id": str(stream_id),
                            "payload": envelope.payload,
                        })
                        client._redis.xack(stream, "group:director", stream_id)
                    except Exception as e:
                        logger.error("Failed to parse resolution %s: %s", stream_id, e)

        return {"content": [{"type": "text", "text": json.dumps({
            "project": project,
            "count": len(resolutions),
            "resolutions": resolutions,
        }, default=str)}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": json.dumps({
            "error": str(e),
        })}]}


# ── Tool: resolve_escalation ──

@tool(
    "resolve_escalation",
    "Mark an escalation as RESOLVED after acting on user guidance. "
    "Posts a final confirmation message to the escalation thread describing "
    "what action was taken. This closes the escalation lifecycle: "
    "AWAITING_USER → DISMISSED → RESOLVED.",
    {
        "escalation_id": str,
        "action_taken": str,
    },
)
async def resolve_escalation(args):
    escalation_id = args["escalation_id"]
    action_taken = args["action_taken"]

    try:
        store = _get_audit_store()

        # Post final confirmation to thread
        store.insert_escalation_message(
            escalation_id=escalation_id,
            author="director",
            content=f"Resolution confirmation: {action_taken}",
        )

        # Update status to RESOLVED
        store.update_escalation_status(escalation_id, "RESOLVED")

        return {"content": [{"type": "text", "text": json.dumps({
            "escalation_id": escalation_id,
            "status": "RESOLVED",
            "message": "Escalation resolved. Final confirmation posted to thread.",
        })}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": json.dumps({
            "error": str(e),
        })}]}


# ── Tool: publish_timeline (Trace Auditor only) ──

@tool(
    "publish_timeline",
    "Publish a structured session timeline to the session_timelines QDrant collection. "
    "This is reference data for other auditors — factual chronological reconstruction, "
    "not opinions or assessments. Only the Trace Auditor should call this tool. "
    "The timeline is stored with a deterministic ID from session_id, so re-publishing overwrites.",
    {
        "session_id": str,
        "project": str,
        "timeline": str,
    },
)
async def publish_timeline(args):
    session_id = args["session_id"]
    project = args.get("project", "")
    timeline_raw = args.get("timeline", "{}")

    try:
        timeline = json.loads(timeline_raw) if isinstance(timeline_raw, str) else timeline_raw
    except json.JSONDecodeError:
        return {"content": [{"type": "text", "text": json.dumps({
            "error": f"Invalid timeline JSON",
        })}]}

    try:
        qb = _get_qdrant()

        # Ensure required fields
        timeline["session_id"] = session_id
        timeline["project"] = project
        timeline["built_by"] = "trace"
        timeline["timestamp"] = datetime.now(timezone.utc).isoformat()
        timeline["timestamp_epoch"] = datetime.now(timezone.utc).timestamp()

        # Build semantic text for embedding
        parts = [
            timeline.get("sequence_summary", ""),
            timeline.get("delegation_tree", ""),
            " ".join(timeline.get("anomalies_detected", [])),
        ]
        text = f"Session {session_id} timeline: " + " | ".join(p for p in parts if p)

        qb.add_session_timeline(text=text, payload=timeline)

        return {"content": [{"type": "text", "text": json.dumps({
            "published": True,
            "session_id": session_id,
            "collection": "session_timelines",
            "total_events": timeline.get("total_events", 0),
            "total_turns": timeline.get("total_turns", 0),
        })}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": json.dumps({
            "error": str(e),
        })}]}


# ── MCP Servers ──

director_server = create_sdk_mcp_server(
    "audit-director-tools",
    tools=[
        qdrant_query, qdrant_compare_windows, qdrant_timeline,
        stream_publish, stream_read, read_file,
        promote_directive, verify_promotion_ack,
        create_escalation, read_escalation_resolutions, resolve_escalation,
    ],
)

# Trace Auditor: standard auditor tools + publish_timeline
trace_auditor_server = create_sdk_mcp_server(
    "audit-trace-tools",
    tools=[qdrant_query, qdrant_compare_windows, qdrant_timeline,
           stream_publish, stream_read, publish_timeline],
)

# Other Auditors: 5 tools (read-only QDrant + stream communication)
auditor_server = create_sdk_mcp_server(
    "audit-auditor-tools",
    tools=[qdrant_query, qdrant_compare_windows, qdrant_timeline,
           stream_publish, stream_read],
)
