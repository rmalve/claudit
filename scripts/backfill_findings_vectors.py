#!/usr/bin/env python3
"""
Backfill findings from SQLite into QDrant for semantic clustering.

Reads all archived findings from the SQLite audit store and upserts them
into the QDrant `findings` collection with embeddings. Idempotent —
deterministic IDs mean re-running is safe.

Usage:
    python scripts/backfill_findings_vectors.py
    python scripts/backfill_findings_vectors.py --project my-project
"""

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from observability.audit_store import AuditStore
from observability.qdrant_backend import QdrantBackend
from observability.stream_client import StreamClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("backfill-findings")


def _build_semantic_text(f: dict) -> str:
    return (
        f"Finding [{f.get('auditor_type', '')}] "
        f"[{f.get('finding_type', '')}] "
        f"[{f.get('severity', '')}] "
        f"Confidence: {f.get('confidence', 0):.2f} | "
        f"Claim: {f.get('claim', '')} | "
        f"Evidence: {str(f.get('evidence', ''))[:300]} | "
        f"Recommendation: {str(f.get('recommendation', ''))[:200]}"
    )


def backfill(project: str | None = None) -> int:
    store = AuditStore()
    qb = QdrantBackend()
    count = 0

    # Source 1: SQLite (archived findings)
    findings = store.query_findings(project=project)
    if findings:
        logger.info("Backfilling %d archived findings from SQLite...", len(findings))
        for f in findings:
            qb.add_finding(text=_build_semantic_text(f), payload=f)
            count += 1

    # Source 2: Redis (live, unarchived findings)
    try:
        import json
        sc = StreamClient.for_director()
        results = sc._redis.xrange("audit:findings", count=2000)
        if results:
            logger.info("Backfilling %d live findings from Redis...", len(results))
            from observability.messages import MessageEnvelope
            for stream_id, data in results:
                try:
                    env = MessageEnvelope.from_stream_dict(data)
                    p = env.payload
                    if project and p.get("project") != project:
                        continue
                    if not p.get("finding_id"):
                        p["finding_id"] = stream_id
                    qb.add_finding(text=_build_semantic_text(p), payload=p)
                    count += 1
                except Exception as e:
                    logger.warning("Skipping finding: %s", e)
        sc.close()
    except Exception as e:
        logger.warning("Redis backfill failed (non-fatal): %s", e)

    if count == 0:
        logger.info("No findings to backfill.")
    else:
        logger.info("Backfilled %d total findings.", count)

    qb.close()
    store._conn.close()
    return count


def main():
    parser = argparse.ArgumentParser(description="Backfill findings from SQLite to QDrant")
    parser.add_argument("--project", help="Filter to a specific project")
    args = parser.parse_args()
    backfill(project=args.project)


if __name__ == "__main__":
    main()
