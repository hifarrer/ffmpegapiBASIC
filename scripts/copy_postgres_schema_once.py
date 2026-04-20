#!/usr/bin/env python3
"""
One-time: copy PostgreSQL schema (no data) from SOURCE to TARGET.

Requires pg_dump and psql on PATH (e.g. PostgreSQL client tools).

Usage:
  python scripts/copy_postgres_schema_once.py --source "$SOURCE_URL" --target "$TARGET_URL"

Optional:
  --clean   prepend DROP statements (use if target may already have objects)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy Postgres schema only (pg_dump | psql).")
    parser.add_argument("--source", help="Source DB URL (or set SOURCE_DATABASE_URL).")
    parser.add_argument("--target", help="Target DB URL (or set TARGET_DATABASE_URL).")
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Include DROP IF EXISTS before creates (safer for partially populated targets).",
    )
    args = parser.parse_args()

    source = args.source or os.environ.get("SOURCE_DATABASE_URL")
    target = args.target or os.environ.get("TARGET_DATABASE_URL")
    if not source or not target:
        print(
            "Error: pass --source and --target, or set SOURCE_DATABASE_URL and TARGET_DATABASE_URL.",
            file=sys.stderr,
        )
        return 1

    dump_cmd = [
        "pg_dump",
        "--schema-only",
        "--no-owner",
        "--no-privileges",
        "--dbname",
        source,
    ]
    if args.clean:
        dump_cmd.append("--clean")

    dump = subprocess.run(dump_cmd, capture_output=True)
    if dump.returncode != 0:
        print(dump.stderr.decode(errors="replace"), file=sys.stderr)
        print(f"pg_dump failed with exit {dump.returncode}", file=sys.stderr)
        return dump.returncode

    restore = subprocess.run(
        ["psql", "--variable=ON_ERROR_STOP=1", "--dbname", target],
        input=dump.stdout,
        capture_output=True,
    )
    if restore.returncode != 0:
        print(restore.stderr.decode(errors="replace"), file=sys.stderr)
        print(restore.stdout.decode(errors="replace"), file=sys.stderr)
        print(f"psql failed with exit {restore.returncode}", file=sys.stderr)
        return restore.returncode

    print("Schema copy finished successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
