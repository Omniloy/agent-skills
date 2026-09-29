#!/usr/bin/env python3
"""Create a NEW flow from a local JSON document, through core-service. Dry-run by default.

For the "from scratch" mode: the document is authored as a file in the workplan
(`docs/wip/<customer>_<topic>/flow_v1.json`), linted, and only then created. Every later
change goes through flow_edit.py like any other flow, so the version history starts at 1.

    python3 create_flow.py --env stg --file flow_v1.json --name "ACME Citación (sandbox MAR-1234)" \\
        --api-key-env ACME_API_KEY --collection <kb collection uuid> [--apply]

The new flow is referenced by no api_key: pin it per call via room metadata.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from flowdb import DB, create_flow, set_collections  # noqa: E402
from flow_lint import lint  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True)
    ap.add_argument("--file", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--description", default="")
    ap.add_argument("--api-key-env", required=True, help="env var holding the tenant api key")
    ap.add_argument("--collection", action="append", default=[])
    ap.add_argument("--language", default="es")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    doc = json.loads(pathlib.Path(a.file).read_text(encoding="utf-8"))
    findings = lint(doc)
    errors = [f for f in findings if f.severity == "error"]
    for f in findings:
        if f.severity != "info":
            print(f"  {f}")
    if errors:
        sys.exit(f"✘ {len(errors)} lint error(s): fix the document first")
    if doc.get("faq_prompt_enabled") and not a.collection:
        sys.exit("✘ faq_prompt_enabled but no --collection: the FAQ would be mute")
    print(f"create «{a.name}» in {a.env}: {len(doc.get('nodes', []))} nodes, "
          f"{len(doc.get('tools', []))} tools, collections {a.collection}")
    if not a.apply:
        print("[dry-run] nothing written. Re-run with --apply.")
        return
    key = os.environ.get(a.api_key_env) or sys.exit(f"✘ {a.api_key_env} is not set")
    res = create_flow(a.env, key, name=a.name, description=a.description, flow_document=doc,
                      default_language=a.language, metadata={"created_by": "CLAUDE"})
    flow_id = (res.get("data") or res).get("id")
    if not flow_id:
        sys.exit(f"✘ unexpected response: {str(res)[:300]}")
    if a.collection:
        set_collections(a.env, key, flow_id, a.collection)
    db = DB(a.env)
    ok, detail = db.health(flow_id)
    print(f"  created {flow_id}; health: {detail} {'✔' if ok else '✘'}; collections: {db.collections(flow_id)}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
