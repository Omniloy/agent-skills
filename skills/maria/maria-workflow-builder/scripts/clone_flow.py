#!/usr/bin/env python3
"""Clone a conversation flow into a sandbox the customer never sees. Dry-run by default.

Cloning is the one routine direct write: there is no flow to PUT yet. So all three version
fields are written together (flow row, version row 1, active_version_id), and — the step
everybody forgets — the knowledge-base collections are copied too. Without them every node
logs `[FAQ] … total_docs=0` and the agent answers "no dispongo de esa información" to every
factual question while the FAQ rules look perfect.

The clone is referenced by no api_key; tests and manual calls pin it through room metadata
(`conversation_flow_id`), so the customer never moves while you work.

    # prod flow -> stg sandbox pointing at the customer's TEST backend
    python3 clone_flow.py --source-env prod --source-flow <uuid> --target-env stg \\
        --name "ACME Citación — sandbox MAR-1234" \\
        --rewrite-host pro-api.acme.example=pre-api.acme.example \\
        --set-header X-API-Key=env:ACME_PRE_KEY \\
        --set-var prm_number=+1XXXXXXXXXX            # a TEST transfer number, never the real desk
    # add --apply to write

`--set-header NAME=env:VAR` reads the value from the environment so it never lands in shell
history. Across environments the api_key must exist in the target (same id in San Roque's
case); `--api-key-id` overrides it.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from flowdb import DB, FlowDBError, host, iter_webhooks  # noqa: E402


def post(db: DB, path: str, body, method: str = "POST") -> list:
    req = urllib.request.Request(
        f"https://{db.ref}.supabase.co/rest/v1/{path}", data=json.dumps(body).encode(), method=method,
        headers={"apikey": db._key, "Authorization": f"Bearer {db._key}",
                 "Content-Type": "application/json", "Prefer": "return=representation"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"[]")
    except urllib.error.HTTPError as exc:
        raise FlowDBError(f"{method} {path} -> {exc.code}: {exc.read().decode()[:400]}") from exc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-env", required=True)
    ap.add_argument("--source-flow", required=True)
    ap.add_argument("--target-env", required=True)
    ap.add_argument("--name", required=True, help="say it is a sandbox, and for what")
    ap.add_argument("--api-key-id")
    ap.add_argument("--rewrite-host", action="append", default=[], metavar="SRC=DST")
    ap.add_argument("--set-header", action="append", default=[], metavar="NAME=VALUE|NAME=env:VAR")
    ap.add_argument("--set-var", action="append", default=[], metavar="NAME=VALUE")
    ap.add_argument("--allowed-host", action="append", default=[],
                    help="after rewriting, every webhook must be on one of these (repeatable)")
    ap.add_argument("--collection", action="append", default=[],
                    help="collection id to link instead of the source's (e.g. across environments)")
    ap.add_argument("--jira", default="")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    src_db, dst_db = DB(a.source_env), DB(a.target_env)
    S = src_db.flow(a.source_flow)
    doc = copy.deepcopy(S["flow_document"])
    host_map = dict(x.split("=", 1) for x in a.rewrite_host)
    headers = {}
    for x in a.set_header:
        k, v = x.split("=", 1)
        if v.startswith("env:"):
            v = os.environ.get(v[4:]) or sys.exit(f"✘ {v[4:]} is not set")
        headers[k] = v
    for where, t in iter_webhooks(doc):
        p = urllib.parse.urlparse(t["url"])
        for s_, d_ in host_map.items():
            if s_ in p.netloc:
                t["url"] = p._replace(netloc=p.netloc.replace(s_, d_)).geturl()
        for k, v in headers.items():
            if k in (t.get("headers") or {}):
                t["headers"][k] = v
    dv = doc.setdefault("default_dynamic_variables", {})
    for x in a.set_var:
        k, v = x.split("=", 1)
        if k not in dv:
            sys.exit(f"✘ the flow does not declare {k}")
        dv[k] = v
    cred_like = {k for _, t in iter_webhooks(doc) for k in (t.get("headers") or {})
                 if any(w in k.lower() for w in ("key", "token", "auth", "secret"))}
    if host_map and cred_like - set(headers):
        print(f"⚠ hosts rewritten but {sorted(cred_like - set(headers))} kept from the SOURCE: the clone "
              "would carry the source environment's credential. Pass --set-header.")
    hosts = sorted({host(t["url"]) for _, t in iter_webhooks(doc)})
    if a.allowed_host and any(not any(h in x for h in a.allowed_host) for x in hosts):
        sys.exit(f"✘ webhooks outside {a.allowed_host} after rewriting: {hosts}")
    collections = a.collection or src_db.collections(a.source_flow)
    if doc.get("faq_prompt_enabled") and not collections:
        sys.exit("✘ FAQ enabled but no collection to link: pass --collection")

    api_key_id = a.api_key_id or S["api_key_id"]
    if not dst_db.select("api_keys", columns="id", filters=[f"id=eq.{api_key_id}"]):
        sys.exit(f"✘ api_key {api_key_id} does not exist in {a.target_env}; pass --api-key-id")
    new_id, vid = str(uuid.uuid4()), str(uuid.uuid4())
    meta = {**(S.get("metadata") or {}), "cloned_from": a.source_flow, "cloned_from_env": a.source_env,
            "cloned_from_version": S["version"], "jira": a.jira}
    desc = (f"Sandbox clone of {a.source_env} {a.source_flow[:8]} v{S['version']}"
            + (f" for {a.jira}" if a.jira else "") + f". Hosts: {hosts}. Variables set: "
            + (", ".join(k for k in (x.split('=', 1)[0] for x in a.set_var)) or "none")
            + ". Not referenced by any api_key: pin it via room metadata conversation_flow_id.")
    print(f"clone {a.source_env}:{a.source_flow[:8]} v{S['version']} «{S['name']}» -> {a.target_env}:{new_id}")
    print(f"  name: {a.name}\n  hosts: {hosts}\n  collections: {collections}\n  api_key: {api_key_id}")
    if not a.apply:
        print("\n[dry-run] nothing written. Re-run with --apply.")
        return
    common = {"name": a.name, "description": desc, "flow_document": doc,
              "default_language": S.get("default_language"), "llm_config": S.get("llm_config"),
              "metadata": meta}
    post(dst_db, "conversation_flows", {"id": new_id, "api_key_id": api_key_id, "version": 1, **common})
    post(dst_db, "conversation_flows_versions", {
        "id": vid, "conversation_flow_id": new_id, "version_number": 1, **common,
        "change_type": "create", "change_description": desc, "created_by_user_id": "CLAUDE"})
    post(dst_db, f"conversation_flows?id=eq.{new_id}", {"active_version_id": vid}, method="PATCH")
    for c in collections:
        post(dst_db, "conversation_flow_collections", {"conversation_flow_id": new_id, "collection_id": c})
    ok, detail = dst_db.health(new_id)
    linked = dst_db.collections(new_id)
    print(f"  written. health: {detail} {'✔' if ok else '✘'}; collections linked: {len(linked)}")
    print(f"  FLOW ID: {new_id}")
    sys.exit(0 if ok and len(linked) == len(collections) else 1)


if __name__ == "__main__":
    main()
