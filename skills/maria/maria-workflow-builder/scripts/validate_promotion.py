#!/usr/bin/env python3
"""Rehearse a promotion SQL file in a throwaway Postgres before anyone applies it for real.

Starts a disposable `postgres:16` container, creates the minimal faithful schema the files
touch (with the real CHECK on change_type and the real FKs), seeds it with the TARGET flow
exactly as it is now (row + every version row, read-only from the target environment), and:

  1. applies 01_promote_v<N>.sql            -> must commit
  2. health: version = N = max(version_number), active_version_id set, the stored document is
     the one embedded in the file
  3. re-applies it                          -> the version guard must REFUSE it
  4. applies 99_rollback_to_v<M>.sql        -> version back to M, document = v<M>'s, v<N> row kept
  5. removes the container

    python3 validate_promotion.py --target-env prod --target-flow <uuid> --dir <out-dir>

Needs docker. Reads the target environment; writes nothing outside the container.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from flowdb import DB  # noqa: E402

SCHEMA = """
CREATE TABLE api_keys (id uuid PRIMARY KEY, voice_mode text NOT NULL DEFAULT 'standard',
  default_conversation_flow_id uuid);
CREATE TABLE conversation_flows (
  id uuid PRIMARY KEY, api_key_id uuid NOT NULL REFERENCES api_keys(id),
  name text NOT NULL DEFAULT '', description text DEFAULT '',
  flow_document jsonb NOT NULL DEFAULT '{}'::jsonb, version integer NOT NULL DEFAULT 1,
  metadata jsonb DEFAULT '{}'::jsonb, default_language text, llm_config jsonb,
  active_version_id uuid, created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE conversation_flows_versions (
  id uuid PRIMARY KEY, conversation_flow_id uuid NOT NULL REFERENCES conversation_flows(id),
  version_number integer NOT NULL, name text NOT NULL DEFAULT '', description text DEFAULT '',
  flow_document jsonb NOT NULL DEFAULT '{}'::jsonb, metadata jsonb DEFAULT '{}'::jsonb,
  default_language text, llm_config jsonb,
  change_type text NOT NULL CHECK (change_type IN
    ('create','edit','promote_from_sandbox','activate','backfill')),
  change_description text, created_by_user_id text,
  created_at timestamptz NOT NULL DEFAULT now(), UNIQUE (conversation_flow_id, version_number));
ALTER TABLE conversation_flows ADD FOREIGN KEY (active_version_id)
  REFERENCES conversation_flows_versions(id) ON DELETE SET NULL;
CREATE TABLE conversation_flow_collections (conversation_flow_id uuid NOT NULL
  REFERENCES conversation_flows(id), collection_id uuid NOT NULL,
  PRIMARY KEY (conversation_flow_id, collection_id));
"""


def lit(v) -> str:
    return "NULL" if v is None else "'" + str(v).replace("'", "''") + "'"


def jsonb(o) -> str:
    return "NULL" if o is None else "$j$" + json.dumps(o, ensure_ascii=False) + "$j$::jsonb"


def seed(env: str, flow_id: str) -> tuple:
    db = DB(env)
    f = db.flow(flow_id, columns="id,api_key_id,name,description,flow_document,version,metadata,"
                "default_language,llm_config,active_version_id")
    vs = db.select("conversation_flows_versions", columns="id,version_number,name,flow_document,"
                   "change_type", filters=[f"conversation_flow_id=eq.{flow_id}"], order="version_number")
    out = [f"INSERT INTO api_keys (id) VALUES ('{f['api_key_id']}');",
           f"INSERT INTO conversation_flows (id, api_key_id, name, description, flow_document, version, "
           f"metadata, default_language, llm_config) VALUES ('{flow_id}', '{f['api_key_id']}', "
           f"{lit(f['name'])}, {lit(f['description'])}, {jsonb(f['flow_document'])}, {f['version']}, "
           f"{jsonb(f['metadata'])}, {lit(f['default_language'])}, {jsonb(f['llm_config'])});"]
    for v in vs:
        out.append(f"INSERT INTO conversation_flows_versions (id, conversation_flow_id, version_number, "
                   f"name, flow_document, change_type) VALUES ('{v['id']}', '{flow_id}', "
                   f"{v['version_number']}, {lit(v['name'])}, {jsonb(v['flow_document'])}, "
                   f"{lit(v['change_type'])});")
    if f["active_version_id"]:
        out.append(f"UPDATE conversation_flows SET active_version_id = '{f['active_version_id']}' "
                   f"WHERE id = '{flow_id}';")
    for c in db.collections(flow_id):
        out.append(f"INSERT INTO conversation_flow_collections VALUES ('{flow_id}', '{c}');")
    return "\n".join(out), f


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target-env", required=True)
    ap.add_argument("--target-flow", required=True)
    ap.add_argument("--dir", required=True, help="where 01_promote_*.sql and 99_rollback_*.sql are")
    a = ap.parse_args()
    d = pathlib.Path(a.dir)
    promote = sorted(d.glob("01_promote_v*.sql"))[-1]
    rollback = sorted(d.glob("99_rollback_to_v*.sql"))[-1]
    n = int(re.search(r"v(\d+)", promote.name).group(1))
    m = int(re.search(r"v(\d+)", rollback.name).group(1))
    embedded = json.loads(re.search(r"SET flow_document = '(.*?)'::jsonb", promote.read_text(), re.S)
                          .group(1).replace("''", "'"))
    seed_sql, before = seed(a.target_env, a.target_flow)
    if before["version"] != m:
        sys.exit(f"✘ the target is at v{before['version']} but the files promote v{m}->v{n}: regenerate")

    name = f"promo-check-{uuid.uuid4().hex[:8]}"
    subprocess.run(["docker", "run", "-d", "--rm", "--name", name, "-e", "POSTGRES_PASSWORD=x",
                    "postgres:16"], check=True, capture_output=True)

    def psql(sql: str, ok: bool = True) -> subprocess.CompletedProcess:
        r = subprocess.run(["docker", "exec", "-i", name, "psql", "-U", "postgres", "-v", "ON_ERROR_STOP=1",
                            "-qAt"], input=sql, text=True, capture_output=True)
        if ok and r.returncode:
            raise RuntimeError(r.stderr[-800:])
        return r

    def state() -> tuple:
        out = psql(f"SELECT version, active_version_id IS NOT NULL, "
                   f"(SELECT max(version_number) FROM conversation_flows_versions "
                   f" WHERE conversation_flow_id = '{a.target_flow}'), flow_document::text "
                   f"FROM conversation_flows WHERE id = '{a.target_flow}';").stdout.strip()
        v, act, top, doc = out.split("|", 3)
        return int(v), act == "t", int(top), json.loads(doc)

    try:
        for _ in range(60):
            if psql("SELECT 1;", ok=False).returncode == 0:
                break
            time.sleep(1)
        psql(SCHEMA)
        psql(seed_sql)
        checks = []
        psql(promote.read_text())
        v, act, top, doc = state()
        checks.append(("promotion commits and is healthy", v == n == top and act))
        checks.append(("stored document == embedded document", doc == embedded))
        again = psql(promote.read_text(), ok=False)
        checks.append(("re-applying is refused by the version guard",
                       again.returncode != 0 and "regenerate" in again.stderr))
        psql(rollback.read_text())
        v, act, top, doc = state()
        checks.append((f"rollback returns to v{m}", v == m and act))
        checks.append(("rollback restores the original document", doc == before["flow_document"]))
        checks.append((f"the v{n} row stays in the history", top == n))
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    for label, ok in checks:
        print(f"  {'✔' if ok else '✘'} {label}")
    sys.exit(0 if all(ok for _, ok in checks) else 1)


if __name__ == "__main__":
    main()
