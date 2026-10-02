#!/usr/bin/env python3
"""Generate the SQL that promotes a validated sandbox clone onto the customer's flow in
another environment (typically stg clone -> prod flow). It never touches a database: it
READS both documents and WRITES files for a human to review and apply.

Why SQL and not the API: production has no core-service you can point a script at from
here, so the three things that must move together (the version row, `version`,
`active_version_id`) are written in ONE transaction, with a guard on the version the file
was generated against. That keeps the worker's flow cache honest: `version` changes, so
every running worker re-fetches.

What is NOT copied from the clone, because it belongs to the environment:
  * webhook hosts          --rewrite-host  pre-api.example=pro-api.example   (repeatable)
  * credential headers     --keep-header   X-API-Key                         (repeatable)
                           value taken from the TARGET document, per host, never printed
  * environment variables  --keep-var      prm_number                        (repeatable)
                           (transfer numbers, queue extensions…): the target's value wins
Both `tools[]` and every `nodes[].prefetch_tools` are rewritten.

Outputs, in --out-dir:
  01_promote_v<N>.sql       BEGIN; guard; INSERT version; UPDATE flow; COMMIT; health query
  99_rollback_to_v<M>.sql   restores v<M>'s document and pointer (the v<N> row stays: deleting
                            a version falsifies the audit)
  DIFF_v<M>_to_v<N>.txt     structural diff target(before) vs promoted document

Activation (pointing `api_keys` at the flow) is a SEPARATE file on purpose — see
references/versioning-and-promotion.md. Promotion changes nothing callers hear if the flow
is not active; activation is the switch, applied at the start of a watched window.

Example:
    python3 gen_promotion_sql.py \\
      --source-env stg --source-flow <clone> --min-source-version 51 \\
      --target-env prod --target-flow <flow> --expect-target-version 43 \\
      --rewrite-host pre-api.acme.example=pro-api.acme.example \\
      --keep-header X-API-Key --keep-var prm_number --keep-var cola_es_privado \\
      --jira MAR-1608 --description "…what changed, concretely, and why…" \\
      --out-dir docs/wip/acme/promotion
"""
from __future__ import annotations

import argparse
import copy
import json
import pathlib
import sys
import urllib.parse
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from flowdb import DB, host, iter_webhooks  # noqa: E402


def die(msg: str) -> None:
    sys.exit(f"✘ {msg}")


def lit(s) -> str:
    return "'" + str(s if s is not None else "").replace("'", "''") + "'"


def jsonb(o) -> str:
    return "'" + json.dumps(o, ensure_ascii=False).replace("'", "''") + "'::jsonb"


def rewrite(doc: dict, target: dict, host_map: dict, keep_headers: list) -> dict:
    """Rewrite hosts and credential headers in place. Returns counters for the report."""
    # credential values of the target, per (new) host and header name
    creds: dict = {}
    for _, t in iter_webhooks(target):
        for h in keep_headers:
            v = (t.get("headers") or {}).get(h)
            if v:
                creds.setdefault((host(t["url"]), h), v)
    stats = {"webhooks": 0, "headers": 0}
    for where, t in iter_webhooks(doc):
        p = urllib.parse.urlparse(t["url"])
        old = p.netloc
        new = next((dst for src, dst in host_map.items() if src in old), None)
        if new is None:
            die(f"{where}:{t.get('name')} points at {old}, which no --rewrite-host covers")
        netloc = old.replace(next(src for src in host_map if src in old), new)
        t["url"] = p._replace(netloc=netloc).geturl()
        stats["webhooks"] += 1
        for h in keep_headers:
            if (t.get("headers") or {}).get(h) is None:
                continue
            v = creds.get((netloc, h))
            if v is None:
                die(f"the target has no {h} header for host {netloc}: cannot keep its credential")
            t["headers"][h] = v
            stats["headers"] += 1
    return stats


def structural_diff(a: dict, b: dict) -> list:
    out = []
    na, nb = {n["node_id"]: n for n in a.get("nodes", [])}, {n["node_id"]: n for n in b.get("nodes", [])}
    for nid in sorted(set(na) | set(nb)):
        if nid not in na:
            out.append(f"+ node {nid}")
        elif nid not in nb:
            out.append(f"- node {nid}")
        else:
            for k in sorted(set(na[nid]) | set(nb[nid])):
                if k in ("position",):
                    continue
                if na[nid].get(k) != nb[nid].get(k):
                    if k == "edges":
                        ea = {e["edge_id"]: e for e in na[nid].get("edges") or []}
                        eb = {e["edge_id"]: e for e in nb[nid].get("edges") or []}
                        for eid in sorted(set(ea) | set(eb)):
                            if eid not in ea:
                                out.append(f"+ edge {nid}/{eid} -> {eb[eid].get('target_node_id')}")
                            elif eid not in eb:
                                out.append(f"- edge {nid}/{eid}")
                            elif ea[eid] != eb[eid]:
                                out.append(f"~ edge {nid}/{eid}")
                    else:
                        out.append(f"~ node {nid}.{k}")
    ta, tb = {t.get("name"): t for t in a.get("tools", [])}, {t.get("name"): t for t in b.get("tools", [])}
    for name in sorted(set(ta) | set(tb), key=str):
        if name not in ta:
            out.append(f"+ tool {name}")
        elif name not in tb:
            out.append(f"- tool {name}")
        elif ta[name] != tb[name]:
            missing = object()
            changed = sorted(k for k in set(ta[name]) | set(tb[name])
                             if ta[name].get(k, missing) != tb[name].get(k, missing))
            out.append(f"~ tool {name}: {', '.join('headers(values hidden)' if k == 'headers' else k for k in changed)}")
    for k in sorted(set(a) | set(b)):
        if k not in ("nodes", "tools") and a.get(k) != b.get(k):
            if k == "default_dynamic_variables":
                da, db_ = a.get(k) or {}, b.get(k) or {}
                for v in sorted(set(da) | set(db_)):
                    if da.get(v) != db_.get(v):
                        out.append(f"~ var {v}")
            else:
                out.append(f"~ root {k}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate promotion + rollback SQL (reads only).")
    ap.add_argument("--source-env", required=True)
    ap.add_argument("--source-flow", required=True)
    ap.add_argument("--min-source-version", type=int, required=True)
    ap.add_argument("--target-env", required=True)
    ap.add_argument("--target-flow", required=True)
    ap.add_argument("--expect-target-version", type=int, required=True)
    ap.add_argument("--rewrite-host", action="append", default=[], metavar="SRC=DST")
    ap.add_argument("--keep-header", action="append", default=[])
    ap.add_argument("--keep-var", action="append", default=[])
    ap.add_argument("--jira", required=True)
    ap.add_argument("--description", required=True, help="what changed, concretely, and why")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--forbid", action="append", default=[],
                    help="a string that must NOT appear in the promoted document (e.g. a test "
                         "phone number, the test host). Repeatable")
    a = ap.parse_args()

    host_map = dict(x.split("=", 1) for x in a.rewrite_host)
    T = DB(a.target_env).flow(a.target_flow, columns="id,name,description,version,default_language,"
                              "llm_config,flow_document")
    S = DB(a.source_env).flow(a.source_flow, columns="version,flow_document")
    if T["version"] != a.expect_target_version:
        die(f"target is at v{T['version']}, not v{a.expect_target_version}: someone changed it; re-check")
    if S["version"] < a.min_source_version:
        die(f"source is at v{S['version']} < {a.min_source_version}")
    target, doc = T["flow_document"], copy.deepcopy(S["flow_document"])

    stats = rewrite(doc, target, host_map, a.keep_header)
    dumped = json.dumps(doc, ensure_ascii=False)
    for src in host_map:
        if src in dumped:
            die(f"source host {src!r} still appears somewhere in the document (outside a url?)")
    for s_ in a.forbid:
        if s_ in dumped:
            die(f"forbidden string {s_!r} appears in the promoted document")
    for h in a.keep_header:   # no credential of the source may survive
        src_vals = {(t.get("headers") or {}).get(h) for _, t in iter_webhooks(S["flow_document"])} - {None}
        tgt_vals = {(t.get("headers") or {}).get(h) for _, t in iter_webhooks(target)} - {None}
        leaked = {v for v in src_vals - tgt_vals if v in dumped}
        if leaked:
            die(f"{len(leaked)} {h} value(s) of the source environment survive in the document")
    tv, dv = target.get("default_dynamic_variables") or {}, doc.setdefault("default_dynamic_variables", {})
    for k in a.keep_var:
        if k not in tv:
            die(f"the target does not declare {k}; nothing to keep")
        dv[k] = tv[k]
    if doc.get("faq_prompt_enabled") and not DB(a.target_env).collections(a.target_flow):
        die("FAQ enabled but the target flow has NO collection linked: link it first (or the FAQ is mute)")

    new_v, vid, fid = T["version"] + 1, str(uuid.uuid4()), a.target_flow
    desc = f"[{a.jira}] v{new_v} from {a.source_env} clone {a.source_flow[:8]} v{S['version']}. {a.description}"
    meta = {"promoted_from": a.source_flow, "promoted_from_env": a.source_env,
            "promoted_from_version": S["version"], "jira": a.jira}
    out = pathlib.Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    sql = f"""-- Promote {a.source_env} clone {a.source_flow} v{S['version']} onto {a.target_env} flow {fid}
-- v{T['version']} -> v{new_v}  ·  {a.jira}. Generated by gen_promotion_sql.py; REVIEW before applying.
-- Moves the three things together (version row, version, active_version_id): the only way the
-- worker flow cache notices. Apply all of it or none of it.
BEGIN;
DO $$
BEGIN
  IF (SELECT version FROM conversation_flows WHERE id = '{fid}') <> {T['version']} THEN
    RAISE EXCEPTION 'conversation_flows.version is not {T['version']}: regenerate this file';
  END IF;
END $$;
INSERT INTO conversation_flows_versions
  (id, conversation_flow_id, version_number, name, description, flow_document,
   default_language, llm_config, metadata, change_type, change_description, created_by_user_id)
VALUES
  ('{vid}', '{fid}', {new_v}, {lit(T['name'])}, {lit(T['description'] or '')},
   {jsonb(doc)},
   {lit(T['default_language'] or 'es')}, {jsonb(T['llm_config'] or {})}, {jsonb(meta)},
   'promote_from_sandbox', {lit(desc)}, 'CLAUDE');
UPDATE conversation_flows
   SET flow_document = {jsonb(doc)}, version = {new_v}, active_version_id = '{vid}', updated_at = now()
 WHERE id = '{fid}';
COMMIT;
-- health: version must equal max(version_number) and active_version_id must be set
SELECT f.version, f.active_version_id IS NOT NULL AS has_active, max(v.version_number) AS highest
  FROM conversation_flows f LEFT JOIN conversation_flows_versions v ON v.conversation_flow_id = f.id
 WHERE f.id = '{fid}' GROUP BY 1, 2;
SELECT collection_id FROM conversation_flow_collections WHERE conversation_flow_id = '{fid}';
"""
    rollback = f"""-- Rollback {a.target_env} flow {fid} to v{T['version']}  ·  {a.jira}
-- Keeps the v{new_v} row on purpose: deleting a version falsifies the audit.
BEGIN;
UPDATE conversation_flows f
   SET flow_document = v.flow_document, version = v.version_number, active_version_id = v.id,
       updated_at = now()
  FROM conversation_flows_versions v
 WHERE f.id = '{fid}' AND v.conversation_flow_id = f.id AND v.version_number = {T['version']};
COMMIT;
"""
    (out / f"01_promote_v{new_v}.sql").write_text(sql, encoding="utf-8")
    (out / f"99_rollback_to_v{T['version']}.sql").write_text(rollback, encoding="utf-8")
    diff = structural_diff(target, doc)
    (out / f"DIFF_v{T['version']}_to_v{new_v}.txt").write_text("\n".join(diff) + "\n", encoding="utf-8")
    print(f"{a.target_env} v{T['version']} -> v{new_v} (from {a.source_env} v{S['version']})")
    print(f"  webhooks rewritten: {stats['webhooks']}  credential headers kept: {stats['headers']}")
    print(f"  variables kept from target: {', '.join(a.keep_var) or '-'} (values not printed)")
    print(f"  structural changes: {len(diff)}  -> {out}/DIFF_v{T['version']}_to_v{new_v}.txt")
    print(f"  {out}/01_promote_v{new_v}.sql\n  {out}/99_rollback_to_v{T['version']}.sql")
    print("Next: validate in a throwaway Postgres with the target's real row (references/"
          "versioning-and-promotion.md), then apply with the user's OK.")


if __name__ == "__main__":
    main()
