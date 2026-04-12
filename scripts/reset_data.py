#!/usr/bin/env python3
"""
Development data reset tool.

Selectively or fully clears Redis streams, QDrant collections,
and SQLite audit store. For development use only — this destroys data.

Usage:
    python scripts/reset_data.py --all              # nuclear option
    python scripts/reset_data.py --redis             # flush all audit streams
    python scripts/reset_data.py --qdrant            # clear all QDrant collections
    python scripts/reset_data.py --sqlite            # delete and recreate audit.db
    python scripts/reset_data.py --redis --sqlite    # combine as needed
    python scripts/reset_data.py --streams audit:findings audit:escalations  # specific streams
    python scripts/reset_data.py --collections tool_calls code_changes       # specific collections
    python scripts/reset_data.py --dry-run --all     # show what would be deleted
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from observability.stream_client import StreamClient
from observability.qdrant_backend import QdrantBackend, ALL_COLLECTIONS
from observability.messages import ALL_STREAMS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("reset")

REPO_ROOT = Path(__file__).resolve().parent.parent
SQLITE_PATH = REPO_ROOT / "data" / "audit.db"


def load_projects():
    config = REPO_ROOT / "config" / "projects.json"
    if not config.exists():
        return []
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
        return [p["name"] for p in data.get("projects", [])]
    except Exception:
        return []


def reset_redis(streams=None, dry_run=False):
    """Flush Redis audit streams."""
    try:
        client = StreamClient.for_director()
    except Exception as e:
        logger.error("Cannot connect to Redis: %s", e)
        return

    # Build list of streams to clear
    if streams:
        target_streams = streams
    else:
        target_streams = list(ALL_STREAMS)
        # Add per-project streams
        for project in load_projects():
            target_streams.append(f"directives:{project}")
            target_streams.append(f"compliance:{project}")

    for stream in target_streams:
        try:
            length = client._redis.xlen(stream)
            if length == 0:
                logger.info("  %s: already empty", stream)
                continue
            if dry_run:
                logger.info("  %s: would trim %d messages", stream, length)
            else:
                client._redis.xtrim(stream, maxlen=0)
                logger.info("  %s: trimmed %d messages", stream, length)
        except Exception as e:
            logger.warning("  %s: %s", stream, e)

    client.close()


def reset_qdrant(collections=None, dry_run=False):
    """Clear QDrant collections (delete and recreate to reset point IDs)."""
    try:
        qb = QdrantBackend()
    except Exception as e:
        logger.error("Cannot connect to QDrant: %s", e)
        return

    target = collections or ALL_COLLECTIONS

    for coll in target:
        try:
            count = qb.get_collection_count(coll)
            if count == 0:
                logger.info("  %s: already empty", coll)
                continue
            if dry_run:
                logger.info("  %s: would delete %d points", coll, count)
            else:
                # Delete and recreate to fully reset
                from qdrant_client import models
                qb._client.delete_collection(coll)
                qb._client.create_collection(
                    collection_name=coll,
                    vectors_config=models.VectorParams(
                        size=384,
                        distance=models.Distance.COSINE,
                    ),
                )
                logger.info("  %s: deleted %d points, recreated", coll, count)
        except Exception as e:
            logger.warning("  %s: %s", coll, e)

    qb.close()


def reset_sqlite(dry_run=False):
    """Delete and let it recreate on next access."""
    if not SQLITE_PATH.exists():
        logger.info("  audit.db: does not exist")
        return

    import sqlite3
    conn = sqlite3.connect(str(SQLITE_PATH))
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence'"
    ).fetchall()]

    for table in tables:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if dry_run:
            logger.info("  %s: would delete %d rows", table, count)
        else:
            conn.execute(f"DELETE FROM {table}")
            logger.info("  %s: deleted %d rows", table, count)

    if not dry_run:
        conn.execute("VACUUM")
        conn.commit()
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Reset development data")
    parser.add_argument("--all", action="store_true", help="Reset everything")
    parser.add_argument("--redis", action="store_true", help="Flush Redis audit streams")
    parser.add_argument("--qdrant", action="store_true", help="Clear QDrant collections")
    parser.add_argument("--sqlite", action="store_true", help="Clear SQLite audit store")
    parser.add_argument("--streams", nargs="+", help="Specific Redis streams to flush")
    parser.add_argument("--collections", nargs="+", help="Specific QDrant collections to clear")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted")
    args = parser.parse_args()

    if not any([args.all, args.redis, args.qdrant, args.sqlite, args.streams, args.collections]):
        parser.print_help()
        print("\nNo action specified. Use --all, --redis, --qdrant, --sqlite, --streams, or --collections.")
        sys.exit(1)

    if args.dry_run:
        logger.info("DRY RUN — no data will be deleted\n")

    if args.all or args.redis or args.streams:
        logger.info("Redis streams:")
        reset_redis(streams=args.streams, dry_run=args.dry_run)
        print()

    if args.all or args.qdrant or args.collections:
        logger.info("QDrant collections:")
        reset_qdrant(collections=args.collections, dry_run=args.dry_run)
        print()

    if args.all or args.sqlite:
        logger.info("SQLite audit store:")
        reset_sqlite(dry_run=args.dry_run)
        print()

    if args.dry_run:
        logger.info("DRY RUN complete. Run without --dry-run to execute.")
    else:
        logger.info("Reset complete.")


if __name__ == "__main__":
    main()
