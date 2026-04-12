"""
One-time migration script: backfill `timestamp_epoch` float field on all
QDrant collections and create payload indexes.

Usage:
    python scripts/backfill_timestamp_epoch.py [--dry-run]

Environment:
    QDRANT_URL  QDrant server URL (default: http://localhost:6333)
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

# Allow importing from the project root
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from observability.qdrant_backend import ALL_COLLECTIONS  # noqa: E402
from qdrant_client import QdrantClient, models  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

BATCH_SIZE = 100

# Payload indexes to create (idempotent)
PAYLOAD_INDEXES: list[tuple[str, models.PayloadSchemaType]] = [
    ("timestamp_epoch", models.PayloadSchemaType.FLOAT),
    ("agent",           models.PayloadSchemaType.KEYWORD),
    ("project",         models.PayloadSchemaType.KEYWORD),
    ("session_id",      models.PayloadSchemaType.KEYWORD),
    ("status",          models.PayloadSchemaType.KEYWORD),
    ("severity",        models.PayloadSchemaType.KEYWORD),
]


def _parse_timestamp(ts_str: str) -> float:
    """Parse an ISO-8601 timestamp string to a UTC epoch float.

    Assumes UTC if no tzinfo is present.
    """
    dt = datetime.fromisoformat(ts_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _ensure_indexes(client: QdrantClient, collection: str, dry_run: bool) -> None:
    """Create payload indexes on a collection (idempotent, no-op if they exist)."""
    for field, schema_type in PAYLOAD_INDEXES:
        if dry_run:
            logger.info("[DRY-RUN] Would create payload index: %s.%s (%s)",
                        collection, field, schema_type)
            continue
        try:
            client.create_payload_index(
                collection_name=collection,
                field_name=field,
                field_schema=schema_type,
            )
            logger.debug("Ensured payload index: %s.%s", collection, field)
        except Exception as exc:
            # Index already exists or unsupported — both are acceptable
            logger.debug("Index %s.%s already present or not applicable: %s",
                         collection, field, exc)


def _migrate_collection(
    client: QdrantClient,
    collection: str,
    dry_run: bool,
) -> dict:
    """Scroll through all points in a collection and backfill timestamp_epoch.

    Returns a stats dict: total, migrated, skipped, failed.
    """
    stats = {"total": 0, "migrated": 0, "skipped": 0, "failed": 0}

    offset = None  # scroll cursor; None means start from beginning

    while True:
        scroll_result = client.scroll(
            collection_name=collection,
            limit=BATCH_SIZE,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        points, next_offset = scroll_result

        if not points:
            break

        for point in points:
            stats["total"] += 1
            payload = point.payload or {}

            # Skip already-migrated points
            if "timestamp_epoch" in payload:
                stats["skipped"] += 1
                continue

            ts_str = payload.get("timestamp")
            if not ts_str:
                logger.warning(
                    "Point %s in %s has no 'timestamp' field — skipping.",
                    point.id, collection,
                )
                stats["failed"] += 1
                continue

            try:
                epoch = _parse_timestamp(ts_str)
            except (ValueError, TypeError) as exc:
                logger.warning(
                    "Point %s in %s: cannot parse timestamp %r — %s",
                    point.id, collection, ts_str, exc,
                )
                stats["failed"] += 1
                continue

            if dry_run:
                logger.info(
                    "[DRY-RUN] Would set timestamp_epoch=%.3f on point %s in %s",
                    epoch, point.id, collection,
                )
                stats["migrated"] += 1
                continue

            try:
                client.set_payload(
                    collection_name=collection,
                    payload={"timestamp_epoch": epoch},
                    points=[point.id],
                )
                stats["migrated"] += 1
            except Exception as exc:
                logger.error(
                    "Failed to update point %s in %s: %s",
                    point.id, collection, exc,
                )
                stats["failed"] += 1

        if next_offset is None:
            break
        offset = next_offset

    return stats


def run(dry_run: bool = False) -> None:
    qdrant_url = os.environ.get("QDRANT_URL", "http://localhost:6333")
    logger.info("Connecting to QDrant at %s", qdrant_url)

    try:
        client = QdrantClient(url=qdrant_url, timeout=10)
        client.get_collections()  # quick connectivity check
        logger.info("Connected to QDrant successfully.")
    except Exception as exc:
        logger.error("Cannot connect to QDrant at %s: %s", qdrant_url, exc)
        sys.exit(1)

    existing_collections = {c.name for c in client.get_collections().collections}

    overall = {"total": 0, "migrated": 0, "skipped": 0, "failed": 0}

    for collection in ALL_COLLECTIONS:
        if collection not in existing_collections:
            logger.warning("Collection '%s' does not exist — skipping.", collection)
            continue

        logger.info("Processing collection: %s", collection)

        _ensure_indexes(client, collection, dry_run)

        stats = _migrate_collection(client, collection, dry_run)

        prefix = "[DRY-RUN] " if dry_run else ""
        logger.info(
            "%s%s — total: %d, migrated: %d, skipped: %d, failed: %d",
            prefix, collection,
            stats["total"], stats["migrated"], stats["skipped"], stats["failed"],
        )

        for key in overall:
            overall[key] += stats[key]

    prefix = "[DRY-RUN] " if dry_run else ""
    logger.info(
        "%sOverall — total: %d, migrated: %d, skipped: %d, failed: %d",
        prefix,
        overall["total"], overall["migrated"], overall["skipped"], overall["failed"],
    )

    client.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill timestamp_epoch on QDrant collections.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without writing any changes.",
    )
    args = parser.parse_args()

    if args.dry_run:
        logger.info("--- DRY-RUN MODE: no writes will be performed ---")

    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
