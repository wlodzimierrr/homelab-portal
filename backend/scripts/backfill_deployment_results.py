"""Backfill deployment result column from existing status values.

Maps terminal status values to result values for existing deployment records:
  status='live'   -> result='success'
  status='failed' -> result='failure'
  status='pending' or 'deploying' -> result left NULL (still in progress)

Usage:
    python scripts/backfill_deployment_results.py [--dry-run]

The script is idempotent: rows that already have a result set are skipped.
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")

from app.db import get_psycopg_database_url

import psycopg


_STATUS_TO_RESULT: dict[str, str] = {
    "live": "success",
    "failed": "failure",
}


def _run(dry_run: bool) -> None:
    conn_str = get_psycopg_database_url()
    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT deployment_id, status
                FROM deployments
                WHERE result IS NULL
                  AND status IN ('live', 'failed')
                ORDER BY requested_at
                """
            )
            rows = cur.fetchall()

        print(f"Found {len(rows)} rows to backfill (dry_run={dry_run})")

        updated = 0
        for deployment_id, status in rows:
            result = _STATUS_TO_RESULT.get(status)
            if result is None:
                continue
            print(f"  {deployment_id}: status={status!r} -> result={result!r}")
            if not dry_run:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE deployments
                        SET result = %s, updated_at = NOW()
                        WHERE deployment_id = %s AND result IS NULL
                        """,
                        (result, deployment_id),
                    )
                    updated += cur.rowcount

        if not dry_run:
            conn.commit()
            print(f"Backfilled {updated} rows.")
        else:
            print(f"Dry run complete — {len(rows)} rows would be updated.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be changed without writing to the database.",
    )
    args = parser.parse_args()
    _run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
