#!/usr/bin/env python3
"""Read-only probe: the real column names of the tables the collectors read.

The workplan describes these tables from the code; this prints what the
deployed database actually has, per environment, so a collector is never
written against a column that only exists in stg (or only in a migration).

Usage: python3 schema_probe.py [prod|stg|dev ...]
"""
from __future__ import annotations

import sys

from _supabase import AccessError, Supabase, load_keys

TABLES = [
    "calls",
    "call_timeline",
    "call_events",
    "call_workflow_runs",
    "call_workflow_transitions",
    "api_keys",
    "call_alert_events",
]


def probe(env: str, keys: dict) -> None:
    print(f"\n===== {env} =====")
    try:
        db = Supabase(env, keys)
    except AccessError as exc:
        print(f"  unavailable: {exc}")
        return

    for table in TABLES:
        try:
            rows = db.select(table, columns="*", limit=1, page=1)
        except AccessError as exc:
            print(f"  {table}: NOT READABLE ({str(exc)[:120]})")
            continue
        if not rows:
            print(f"  {table}: readable but empty")
            continue
        cols = sorted(rows[0].keys())
        print(f"  {table}: {len(cols)} columns")
        print(f"    {', '.join(cols)}")


def main() -> int:
    envs = sys.argv[1:] or ["prod", "stg"]
    keys = load_keys()
    for env in envs:
        probe(env, keys)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
