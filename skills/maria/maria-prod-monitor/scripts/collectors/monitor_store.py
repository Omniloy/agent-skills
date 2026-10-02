#!/usr/bin/env python3
"""The monitor's shared memory, in one SQLite file: error codes, known-error decisions,
code ↔ Jira task links, and the history of every pass.

Why a database and not YAML in git: two people running the monitor at the same time
would both read `L3-FLOW-005` as the last code and both write `L3-FLOW-006` for two
different failures, or give the same failure two codes. Here a session never assigns a
code. It PROPOSES one; proposals are visible to every other session the moment they are
written, and `propose` refuses to add one that looks like an existing code or proposal
unless told it is different. The final id is allocated atomically on APPROVAL, which is a
human step. If two proposals turn out to be the same failure, one is merged into the other.

SQLite for now, so the whole thing is one file that can be presented, copied, and later
moved to miniomni or loaded into Postgres. The schema only uses types and constraints both
engines share (TEXT, INTEGER, CHECK, UNIQUE, FKs; timestamps as ISO-8601 UTC text; JSON as
TEXT). Concurrent writers on one machine are serialised by SQLite (WAL, BEGIN IMMEDIATE,
busy timeout).

File: $MARIA_MONITOR_DB, default <private dir>/monitor.sqlite (see _paths.py). Nothing
stored here may identify a patient: evidence is stripped of free text and long digit runs
before it is recorded.

    python3 monitor_store.py init
    python3 monitor_store.py import-yaml [--data <dir with catalog/known_errors/tasks.yaml>]
    python3 monitor_store.py export-yaml --out <dir>
    python3 monitor_store.py stats

    python3 monitor_store.py similar --text "..."                 # before proposing
    python3 monitor_store.py propose --layer L3 --domain FLOW --title "..." --definition "..." \\
        --owner flow --severity medium --detection judge --example <call_id> --by <name> [--force-new]
    python3 monitor_store.py proposals
    python3 monitor_store.py approve P-12 --by <name>            # allocates L3-FLOW-00N
    python3 monitor_store.py merge P-13 --into P-12|L3-FLOW-006 --by <name>
    python3 monitor_store.py reject P-14 --reason "..." --by <name>
    python3 monitor_store.py deprecate L2-X-001 --replaced-by L2-X-002 --reason "..." --by <name>
    python3 monitor_store.py add-example <code|P-n> --call <call_id> --by <name>

    python3 monitor_store.py kedb-add --id <slug> --code <code> --decision normal|known_bug|watch \\
        --why "..." --by <name> [--tenant T] [--call ID ...] [--when k=v ...] [--unless k=v1,v2 ...]
    python3 monitor_store.py kedb-close <slug> --reason "..." --by <name>

    python3 monitor_store.py task-add --jira MAR-1 --summary "..." [--status open|done] --by <name>
    python3 monitor_store.py task-link --jira MAR-1 --code <code> [--tenant T] [--call ID ...] --by <name>
    python3 monitor_store.py task-status --jira MAR-1 --status done --by <name>
    python3 monitor_store.py tasks [--code <code>]

    python3 monitor_store.py record-run --findings findings.json --env prod --since Z --until Z --by <name>
    python3 monitor_store.py trend --code <code> [--tenant T]
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import DATA_DIR, PRIVATE_DIR  # noqa: E402

DB_PATH = Path(os.environ.get("MARIA_MONITOR_DB") or PRIVATE_DIR / "monitor.sqlite").expanduser()
SCHEMA_VERSION = 1
CODE_RE = re.compile(r"^L[123]-[A-Z]+-\d{3}$")
LONG_DIGITS = re.compile(r"\d{5,}")
#: Evidence keys that can carry what a caller said. Never recorded.
FREE_TEXT_KEYS = {"cita", "quote", "que_paso", "text", "transcript", "utterance", "content"}
SIMILARITY_BLOCK = 0.6

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- Error codes and proposals. `code` is NULL while a row is a proposal: it is allocated
-- on approval, never chosen by a session.
CREATE TABLE IF NOT EXISTS codes (
  key          INTEGER PRIMARY KEY AUTOINCREMENT,
  code         TEXT UNIQUE,
  layer        TEXT NOT NULL CHECK (layer IN ('L1','L2','L3')),
  domain       TEXT NOT NULL,
  title        TEXT NOT NULL,
  status       TEXT NOT NULL CHECK (status IN ('proposed','active','deprecated','merged','rejected')),
  replaced_by  TEXT,
  merged_into  INTEGER REFERENCES codes(key),
  body         TEXT NOT NULL,              -- the full catalog entry, JSON
  proposed_by  TEXT, proposed_at TEXT,
  approved_by  TEXT, approved_at TEXT,
  closed_by    TEXT, closed_at TEXT, closed_reason TEXT,
  updated_at   TEXT NOT NULL,
  CHECK ((status = 'proposed') = (code IS NULL) OR status IN ('merged','rejected'))
);
CREATE INDEX IF NOT EXISTS codes_layer_domain ON codes(layer, domain);

-- Next number per layer+domain, allocated inside the approving transaction.
CREATE TABLE IF NOT EXISTS code_counters (
  layer  TEXT NOT NULL,
  domain TEXT NOT NULL,
  last_n INTEGER NOT NULL,
  PRIMARY KEY (layer, domain)
);

-- Calls that illustrate a code or a proposal. Call ids only.
CREATE TABLE IF NOT EXISTS code_examples (
  code_key INTEGER NOT NULL REFERENCES codes(key),
  call_id  TEXT NOT NULL,
  added_by TEXT, added_at TEXT NOT NULL,
  PRIMARY KEY (code_key, call_id)
);

-- Known-error decisions: what the monitor stops listing, and why.
CREATE TABLE IF NOT EXISTS kedb (
  id            TEXT PRIMARY KEY,
  code          TEXT NOT NULL,
  decision      TEXT NOT NULL CHECK (decision IN ('normal','known_bug','watch','deferred')),
  status        TEXT NOT NULL CHECK (status IN ('active','closed')),
  body          TEXT NOT NULL,             -- matcher + why + who, JSON (same shape as the YAML entry)
  created_by    TEXT, created_at TEXT NOT NULL,
  closed_by     TEXT, closed_at TEXT, closed_reason TEXT,
  updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS kedb_code ON kedb(code);

-- Jira tasks, and which codes (optionally scoped by tenant or calls) each one covers.
CREATE TABLE IF NOT EXISTS tasks (
  jira       TEXT PRIMARY KEY,
  summary    TEXT NOT NULL,
  status     TEXT NOT NULL CHECK (status IN ('open','done')),
  created_by TEXT, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_codes (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  jira      TEXT NOT NULL REFERENCES tasks(jira),
  code      TEXT NOT NULL,
  tenant    TEXT NOT NULL DEFAULT '',       -- '' = every tenant
  calls     TEXT NOT NULL DEFAULT '[]',     -- JSON list of call ids; [] = every call
  linked_by TEXT, linked_at TEXT NOT NULL,
  UNIQUE (jira, code, tenant, calls)
);
CREATE INDEX IF NOT EXISTS task_codes_code ON task_codes(code);

-- Every monitoring pass and what it found: the trend and per-version regressions.
CREATE TABLE IF NOT EXISTS runs (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  env             TEXT NOT NULL,
  window_since    TEXT, window_until TEXT,
  catalog_version TEXT,
  versions        TEXT NOT NULL DEFAULT '{}',  -- JSON: deployed shas, flow versions
  calls           INTEGER,
  created_by      TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS findings (
  run_id   INTEGER NOT NULL REFERENCES runs(id),
  call_id  TEXT NOT NULL,
  tenant   TEXT,
  code     TEXT NOT NULL,
  layer    TEXT,
  severity TEXT,
  kedb_id  TEXT,
  evidence TEXT NOT NULL DEFAULT '{}',        -- sanitised JSON
  PRIMARY KEY (run_id, call_id, code)
);
CREATE INDEX IF NOT EXISTS findings_code ON findings(code);
"""


class StoreError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=30000")
    con.executescript(SCHEMA)
    con.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    con.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('catalog_version', '0.0.0')")
    return con


@contextmanager
def tx(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE: takes the write lock up front, so two sessions allocating a code at
    the same moment are serialised instead of both reading the same counter."""
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def exists(path: Path = DB_PATH) -> bool:
    return path.is_file()


# -- catalog version ----------------------------------------------------------

def catalog_version(con) -> str:
    return con.execute("SELECT value FROM meta WHERE key='catalog_version'").fetchone()[0]


def bump(con, part: str) -> str:
    v = catalog_version(con)
    base, _, suffix = v.partition("-")
    major, minor, patch = (int(x) for x in base.split("."))
    if part == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    new = f"{major}.{minor}.{patch}" + (f"-{suffix}" if suffix else "")
    con.execute("UPDATE meta SET value=? WHERE key='catalog_version'", (new,))
    return new


# -- readers used by the collectors (same shapes as the YAML loaders) ---------

def load_catalog(con) -> dict:
    rows = con.execute("SELECT body FROM codes WHERE status IN ('active','deprecated') ORDER BY code").fetchall()
    return {"version": catalog_version(con), "codes": [json.loads(r["body"]) for r in rows]}


def load_kedb(con) -> List[dict]:
    rows = con.execute("SELECT body FROM kedb WHERE status='active' ORDER BY id").fetchall()
    return [json.loads(r["body"]) for r in rows]


def load_tasks(con) -> List[dict]:
    """One entry per (task, tenant, calls) scope, like tasks.yaml."""
    out: Dict[tuple, dict] = {}
    for r in con.execute("""SELECT t.jira, t.summary, t.status, tc.code, tc.tenant, tc.calls
                              FROM tasks t JOIN task_codes tc ON tc.jira = t.jira
                             ORDER BY t.jira, tc.id"""):
        key = (r["jira"], r["tenant"], r["calls"])
        e = out.setdefault(key, {"jira": r["jira"], "codes": [], "summary": r["summary"], "status": r["status"]})
        e["codes"].append(r["code"])
        if r["tenant"]:
            e["tenant"] = r["tenant"]
        calls = json.loads(r["calls"])
        if calls:
            e["calls"] = calls
    return list(out.values())


# -- proposals and codes ------------------------------------------------------

def _row(con, ref: str) -> sqlite3.Row:
    if ref.startswith("P-"):
        row = con.execute("SELECT * FROM codes WHERE key=?", (int(ref[2:]),)).fetchone()
    else:
        row = con.execute("SELECT * FROM codes WHERE code=?", (ref,)).fetchone()
    if not row:
        raise StoreError(f"{ref} does not exist")
    return row


def ref_of(row) -> str:
    return row["code"] or f"P-{row['key']}"


def similar(con, text: str, limit: int = 5) -> List[tuple]:
    """Active codes and open proposals whose title+definition resemble `text`."""
    text = text.lower()
    scored = []
    for r in con.execute("SELECT * FROM codes WHERE status IN ('active','proposed')"):
        body = json.loads(r["body"])
        other = f"{body.get('title', '')} {body.get('definition', '')}".lower()
        ratio = difflib.SequenceMatcher(None, text, other).ratio()
        words = set(re.findall(r"[a-záéíóúñ0-9_]{4,}", text))
        overlap = len(words & set(re.findall(r"[a-záéíóúñ0-9_]{4,}", other))) / (len(words) or 1)
        scored.append((max(ratio, overlap), ref_of(r), r["status"], body.get("title", "")))
    return sorted(scored, reverse=True)[:limit]


def propose(con, *, layer, domain, title, definition, owner, severity, detection, by, examples=(),
            force_new=False, extra=None) -> str:
    candidates = [c for c in similar(con, f"{title} {definition}") if c[0] >= SIMILARITY_BLOCK]
    if candidates and not force_new:
        lines = "\n".join(f"  {s:.2f}  {ref} [{st}] {t}" for s, ref, st, t in candidates)
        raise StoreError("looks like an existing code or an open proposal — add your call as an example "
                         f"(add-example) instead, or re-run with --force-new if it is different:\n{lines}")
    body = {"id": None, "title": title, "layer": layer, "domain": domain, "definition": definition,
            "detection": {"kind": detection}, "severity_default": severity, "owner": owner,
            "scope": "global", "status": "proposed", **(extra or {})}
    with tx(con):
        cur = con.execute("""INSERT INTO codes(layer, domain, title, status, body, proposed_by, proposed_at, updated_at)
                             VALUES (?,?,?,?,?,?,?,?)""",
                          (layer, domain, title, "proposed", json.dumps(body, ensure_ascii=False), by, now(), now()))
        key = cur.lastrowid
        for call in examples:
            con.execute("INSERT OR IGNORE INTO code_examples VALUES (?,?,?,?)", (key, call, by, now()))
    return f"P-{key}"


def approve(con, ref: str, by: str) -> str:
    with tx(con):
        row = _row(con, ref)
        if row["status"] != "proposed":
            raise StoreError(f"{ref} is {row['status']}, not a proposal")
        layer, domain = row["layer"], row["domain"]
        con.execute("INSERT OR IGNORE INTO code_counters VALUES (?,?,0)", (layer, domain))
        highest = con.execute("SELECT code FROM codes WHERE layer=? AND domain=? AND code IS NOT NULL",
                              (layer, domain)).fetchall()
        taken = max([int(r["code"].rsplit("-", 1)[1]) for r in highest] or [0])
        last = max(taken, con.execute("SELECT last_n FROM code_counters WHERE layer=? AND domain=?",
                                      (layer, domain)).fetchone()[0])
        code = f"{layer}-{domain}-{last + 1:03d}"
        con.execute("UPDATE code_counters SET last_n=? WHERE layer=? AND domain=?", (last + 1, layer, domain))
        version = bump(con, "minor")
        body = json.loads(row["body"])
        examples = [r["call_id"] for r in con.execute("SELECT call_id FROM code_examples WHERE code_key=?",
                                                      (row["key"],))]
        body.update({"id": code, "status": "active", "since_catalog": version})
        if examples:
            body["examples"] = examples
        con.execute("""UPDATE codes SET code=?, status='active', body=?, approved_by=?, approved_at=?, updated_at=?
                       WHERE key=?""", (code, json.dumps(body, ensure_ascii=False), by, now(), now(), row["key"]))
    return code


def merge(con, ref: str, into: str, by: str) -> str:
    with tx(con):
        row, target = _row(con, ref), _row(con, into)
        if row["status"] != "proposed":
            raise StoreError(f"only a proposal can be merged; {ref} is {row['status']}")
        if target["status"] not in ("proposed", "active"):
            raise StoreError(f"{into} is {target['status']}")
        con.execute("INSERT OR IGNORE INTO code_examples SELECT ?, call_id, added_by, added_at FROM code_examples "
                    "WHERE code_key=?", (target["key"], row["key"]))
        con.execute("""UPDATE codes SET status='merged', merged_into=?, closed_by=?, closed_at=?, updated_at=?
                       WHERE key=?""", (target["key"], by, now(), now(), row["key"]))
    return ref_of(target)


def reject(con, ref: str, reason: str, by: str) -> None:
    with tx(con):
        row = _row(con, ref)
        if row["status"] != "proposed":
            raise StoreError(f"{ref} is {row['status']}")
        con.execute("""UPDATE codes SET status='rejected', closed_by=?, closed_at=?, closed_reason=?, updated_at=?
                       WHERE key=?""", (by, now(), reason, now(), row["key"]))


def deprecate(con, code: str, replaced_by: Optional[str], reason: str, by: str) -> str:
    """Never redefine a code in place: a change of meaning is a new code plus this."""
    with tx(con):
        row = _row(con, code)
        if row["status"] != "active":
            raise StoreError(f"{code} is {row['status']}")
        if replaced_by:
            _row(con, replaced_by)
        body = json.loads(row["body"])
        body.update({"status": "deprecated", "replaced_by": replaced_by, "deprecation_reason": reason})
        con.execute("""UPDATE codes SET status='deprecated', replaced_by=?, body=?, closed_by=?, closed_at=?,
                       closed_reason=?, updated_at=? WHERE key=?""",
                    (replaced_by, json.dumps(body, ensure_ascii=False), by, now(), reason, now(), row["key"]))
        return bump(con, "minor")


def add_example(con, ref: str, call: str, by: str) -> None:
    with tx(con):
        row = _row(con, ref)
        con.execute("INSERT OR IGNORE INTO code_examples VALUES (?,?,?,?)", (row["key"], call, by, now()))


# -- KEDB -----------------------------------------------------------------------

def kedb_add(con, *, id, code, decision, why, by, tenant=None, calls=None, when=None, unless=None) -> None:
    body = {"id": id, "code": code, "decision": decision, "why": why, "decided_by": by,
            "decided_on": now()[:10]}
    for k, v in (("tenant", tenant), ("calls", calls), ("when", when), ("unless", unless)):
        if v:
            body[k] = v
    with tx(con):
        if not con.execute("SELECT 1 FROM codes WHERE code=? AND status IN ('active','deprecated')", (code,)).fetchone():
            raise StoreError(f"{code} is not an active code")
        con.execute("""INSERT INTO kedb(id, code, decision, status, body, created_by, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (id, code, decision, "active", json.dumps(body, ensure_ascii=False), by, now(), now()))


def kedb_close(con, id: str, reason: str, by: str) -> None:
    with tx(con):
        n = con.execute("""UPDATE kedb SET status='closed', closed_by=?, closed_at=?, closed_reason=?, updated_at=?
                           WHERE id=? AND status='active'""", (by, now(), reason, now(), id)).rowcount
        if not n:
            raise StoreError(f"no active KEDB entry {id}")


# -- tasks ----------------------------------------------------------------------

def task_add(con, jira, summary, status, by) -> None:
    with tx(con):
        con.execute("""INSERT INTO tasks(jira, summary, status, created_by, created_at, updated_at)
                       VALUES (?,?,?,?,?,?)
                       ON CONFLICT(jira) DO UPDATE SET summary=excluded.summary, status=excluded.status,
                       updated_at=excluded.updated_at""", (jira, summary, status, by, now(), now()))


def task_link(con, jira, code, by, tenant="", calls=()) -> None:
    with tx(con):
        if not con.execute("SELECT 1 FROM tasks WHERE jira=?", (jira,)).fetchone():
            raise StoreError(f"{jira} is not a known task: task-add it first")
        if not con.execute("SELECT 1 FROM codes WHERE code=?", (code,)).fetchone():
            raise StoreError(f"{code} is not a code")
        con.execute("INSERT OR IGNORE INTO task_codes(jira, code, tenant, calls, linked_by, linked_at) VALUES (?,?,?,?,?,?)",
                    (jira, code, tenant or "", json.dumps(sorted(calls)), by, now()))


def task_status(con, jira, status, by) -> None:
    with tx(con):
        if not con.execute("UPDATE tasks SET status=?, updated_at=? WHERE jira=?", (status, now(), jira)).rowcount:
            raise StoreError(f"{jira} is not a known task")


# -- runs -----------------------------------------------------------------------

def sanitise(evidence) -> dict:
    """Keep the shape of the evidence, never what a caller said or a long number."""
    def clean(v):
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items() if k not in FREE_TEXT_KEYS}
        if isinstance(v, list):
            return [clean(x) for x in v]
        if isinstance(v, str):
            return LONG_DIGITS.sub("#", v)[:200]
        if isinstance(v, float):
            return round(v, 3)
        return v
    return clean(evidence or {})


def record_run(con, findings_path: Path, *, env, since, until, by, versions=None) -> int:
    payload = json.loads(findings_path.read_text(encoding="utf-8"))
    results = payload.get("results") or payload.get("calls") or []
    flat = payload.get("findings") or [f for r in results for f in (r.get("findings") or [])]
    with tx(con):
        cur = con.execute("""INSERT INTO runs(env, window_since, window_until, catalog_version, versions, calls,
                             created_by, created_at) VALUES (?,?,?,?,?,?,?,?)""",
                          (env, since, until, catalog_version(con), json.dumps(versions or {}),
                           len(results) or None, by, now()))
        run = cur.lastrowid
        for f in flat:
            con.execute("""INSERT OR IGNORE INTO findings(run_id, call_id, tenant, code, layer, severity, kedb_id, evidence)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (run, f.get("conversation_id") or f.get("call_id"), f.get("tenant"), f["code"],
                         f.get("layer"), f.get("severity"), f.get("kedb_id"),
                         json.dumps(sanitise(f.get("evidence")), ensure_ascii=False)))
    return run


# -- YAML seed / snapshot -------------------------------------------------------

def _refs(value) -> Optional[str]:
    """`replaced_by` is one code or several (a split); stored as text either way."""
    if value is None or isinstance(value, str):
        return value
    return ",".join(value)


def import_yaml(con, data: Path, by: str) -> dict:
    """Idempotent: codes and KEDB entries are upserted by id, task links deduplicated."""
    import yaml
    cat = yaml.safe_load((data / "catalog.yaml").read_text(encoding="utf-8"))
    kedb = yaml.safe_load((data / "known_errors.yaml").read_text(encoding="utf-8")) or {}
    tasks = yaml.safe_load((data / "tasks.yaml").read_text(encoding="utf-8")) or {}
    counts = {"codes": 0, "kedb": 0, "tasks": 0, "links": 0}
    with tx(con):
        for c in cat["codes"]:
            if not CODE_RE.match(c["id"]):
                raise StoreError(f"bad code id {c['id']}")
            status = c.get("status", "active")
            con.execute("""INSERT INTO codes(code, layer, domain, title, status, replaced_by, body, approved_by,
                           approved_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(code) DO UPDATE SET title=excluded.title, status=excluded.status,
                           replaced_by=excluded.replaced_by, body=excluded.body, updated_at=excluded.updated_at""",
                        (c["id"], c["layer"], c["domain"], c["title"], status, _refs(c.get("replaced_by")),
                         json.dumps(c, ensure_ascii=False, default=str), by, now(), now()))
            counts["codes"] += 1
        con.execute("UPDATE meta SET value=? WHERE key='catalog_version'", (str(cat["version"]),))
        for e in kedb.get("entries") or []:
            con.execute("""INSERT INTO kedb(id, code, decision, status, body, created_by, created_at, updated_at)
                           VALUES (?,?,?,?,?,?,?,?)
                           ON CONFLICT(id) DO UPDATE SET code=excluded.code, decision=excluded.decision,
                           body=excluded.body, updated_at=excluded.updated_at""",
                        (e["id"], e["code"], e["decision"], "active", json.dumps(e, ensure_ascii=False, default=str),
                         e.get("decided_by") or by, now(), now()))
            counts["kedb"] += 1
        for t in tasks.get("tasks") or []:
            con.execute("""INSERT INTO tasks(jira, summary, status, created_by, created_at, updated_at)
                           VALUES (?,?,?,?,?,?) ON CONFLICT(jira) DO UPDATE SET status=excluded.status,
                           updated_at=excluded.updated_at""",
                        (t["jira"], " ".join(str(t["summary"]).split()), t.get("status", "open"), by, now(), now()))
            counts["tasks"] += 1
            for code in t["codes"]:
                cur = con.execute("""INSERT OR IGNORE INTO task_codes(jira, code, tenant, calls, linked_by, linked_at)
                                     VALUES (?,?,?,?,?,?)""",
                                  (t["jira"], code, t.get("tenant") or "", json.dumps(sorted(t.get("calls") or [])),
                                   by, now()))
                counts["links"] += cur.rowcount
    return counts


def export_yaml(con, out: Path) -> None:
    import yaml
    out.mkdir(parents=True, exist_ok=True)
    header = "# Snapshot exported from monitor.sqlite by monitor_store.py export-yaml. Do not edit: the\n" \
             "# database is the source of truth.\n"
    cat = load_catalog(con)
    cat["updated"] = now()[:10]
    (out / "catalog.yaml").write_text(header + yaml.safe_dump(cat, allow_unicode=True, sort_keys=False, width=100),
                                      encoding="utf-8")
    (out / "known_errors.yaml").write_text(header + yaml.safe_dump({"version": catalog_version(con),
                                           "entries": load_kedb(con)}, allow_unicode=True, sort_keys=False,
                                           width=100), encoding="utf-8")
    (out / "tasks.yaml").write_text(header + yaml.safe_dump({"version": catalog_version(con), "updated": now()[:10],
                                    "tasks": load_tasks(con)}, allow_unicode=True, sort_keys=False, width=100),
                                    encoding="utf-8")


# -- CLI ------------------------------------------------------------------------

def _kv(items: List[str], listy: bool = False) -> dict:
    out = {}
    for it in items:
        k, _, v = it.partition("=")
        out[k] = v.split(",") if listy else v
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", type=Path, default=DB_PATH)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    p = sub.add_parser("import-yaml"); p.add_argument("--data", type=Path, default=DATA_DIR); p.add_argument("--by", default="import")
    p = sub.add_parser("export-yaml"); p.add_argument("--out", type=Path, required=True)
    sub.add_parser("stats")
    p = sub.add_parser("similar"); p.add_argument("--text", required=True)
    p = sub.add_parser("propose")
    for a in ("layer", "domain", "title", "definition", "owner", "severity", "detection", "by"):
        p.add_argument(f"--{a}", required=True)
    p.add_argument("--example", action="append", default=[]); p.add_argument("--force-new", action="store_true")
    sub.add_parser("proposals")
    p = sub.add_parser("approve"); p.add_argument("ref"); p.add_argument("--by", required=True)
    p = sub.add_parser("merge"); p.add_argument("ref"); p.add_argument("--into", required=True); p.add_argument("--by", required=True)
    p = sub.add_parser("reject"); p.add_argument("ref"); p.add_argument("--reason", required=True); p.add_argument("--by", required=True)
    p = sub.add_parser("deprecate"); p.add_argument("code"); p.add_argument("--replaced-by")
    p.add_argument("--reason", required=True); p.add_argument("--by", required=True)
    p = sub.add_parser("add-example"); p.add_argument("ref"); p.add_argument("--call", required=True); p.add_argument("--by", required=True)
    p = sub.add_parser("kedb-add")
    for a in ("id", "code", "why", "by"):
        p.add_argument(f"--{a}", required=True)
    p.add_argument("--decision", required=True, choices=["normal", "known_bug", "watch"])
    p.add_argument("--tenant"); p.add_argument("--call", action="append", default=[])
    p.add_argument("--when", action="append", default=[]); p.add_argument("--unless", action="append", default=[])
    p = sub.add_parser("kedb-close"); p.add_argument("id"); p.add_argument("--reason", required=True); p.add_argument("--by", required=True)
    p = sub.add_parser("task-add"); p.add_argument("--jira", required=True); p.add_argument("--summary", required=True)
    p.add_argument("--status", default="open", choices=["open", "done"]); p.add_argument("--by", required=True)
    p = sub.add_parser("task-link"); p.add_argument("--jira", required=True); p.add_argument("--code", required=True)
    p.add_argument("--tenant", default=""); p.add_argument("--call", action="append", default=[]); p.add_argument("--by", required=True)
    p = sub.add_parser("task-status"); p.add_argument("--jira", required=True)
    p.add_argument("--status", required=True, choices=["open", "done"]); p.add_argument("--by", required=True)
    p = sub.add_parser("tasks"); p.add_argument("--code")
    p = sub.add_parser("record-run"); p.add_argument("--findings", type=Path, required=True)
    p.add_argument("--env", required=True); p.add_argument("--since"); p.add_argument("--until"); p.add_argument("--by", required=True)
    p.add_argument("--versions", help="JSON: deployed shas and flow versions")
    p = sub.add_parser("trend"); p.add_argument("--code", required=True); p.add_argument("--tenant")
    a = ap.parse_args(argv)

    con = connect(a.db)
    try:
        if a.cmd == "init":
            print(f"{a.db} ready (schema v{SCHEMA_VERSION}, catalog {catalog_version(con)})")
        elif a.cmd == "import-yaml":
            print(json.dumps(import_yaml(con, a.data, a.by)), f"catalog {catalog_version(con)}")
        elif a.cmd == "export-yaml":
            export_yaml(con, a.out); print(f"exported to {a.out}")
        elif a.cmd == "stats":
            q = lambda s: con.execute(s).fetchall()  # noqa: E731
            print("codes:", dict(q("SELECT status, count(*) FROM codes GROUP BY status")))
            print("kedb:", dict(q("SELECT decision || '/' || status, count(*) FROM kedb GROUP BY 1")))
            print("tasks:", dict(q("SELECT status, count(*) FROM tasks GROUP BY status")),
                  "links:", q("SELECT count(*) FROM task_codes")[0][0])
            print("runs:", q("SELECT count(*) FROM runs")[0][0], "findings:", q("SELECT count(*) FROM findings")[0][0],
                  "| catalog", catalog_version(con), "|", a.db)
        elif a.cmd == "similar":
            for s, ref, st, t in similar(con, a.text):
                print(f"{s:.2f}  {ref:14} [{st}] {t}")
        elif a.cmd == "propose":
            print(propose(con, layer=a.layer, domain=a.domain.upper(), title=a.title, definition=a.definition,
                          owner=a.owner, severity=a.severity, detection=a.detection, by=a.by,
                          examples=a.example, force_new=a.force_new))
        elif a.cmd == "proposals":
            for r in con.execute("SELECT * FROM codes WHERE status='proposed' ORDER BY key"):
                n = con.execute("SELECT count(*) FROM code_examples WHERE code_key=?", (r["key"],)).fetchone()[0]
                print(f"P-{r['key']:<5} {r['layer']}-{r['domain']:12} {n} call(s)  by {r['proposed_by']} "
                      f"{r['proposed_at']}  {r['title']}")
        elif a.cmd == "approve":
            print(approve(con, a.ref, a.by))
        elif a.cmd == "merge":
            print(f"{a.ref} merged into {merge(con, a.ref, a.into, a.by)}")
        elif a.cmd == "reject":
            reject(con, a.ref, a.reason, a.by); print(f"{a.ref} rejected")
        elif a.cmd == "deprecate":
            print(f"{a.code} deprecated; catalog {deprecate(con, a.code, a.replaced_by, a.reason, a.by)}")
        elif a.cmd == "add-example":
            add_example(con, a.ref, a.call, a.by); print("ok")
        elif a.cmd == "kedb-add":
            kedb_add(con, id=a.id, code=a.code, decision=a.decision, why=a.why, by=a.by, tenant=a.tenant,
                     calls=a.call, when=_kv(a.when), unless=_kv(a.unless, listy=True)); print("ok")
        elif a.cmd == "kedb-close":
            kedb_close(con, a.id, a.reason, a.by); print("ok")
        elif a.cmd == "task-add":
            task_add(con, a.jira, a.summary, a.status, a.by); print("ok")
        elif a.cmd == "task-link":
            task_link(con, a.jira, a.code, a.by, a.tenant, a.call); print("ok")
        elif a.cmd == "task-status":
            task_status(con, a.jira, a.status, a.by); print("ok")
        elif a.cmd == "tasks":
            sql = """SELECT tc.code, t.jira, t.status, tc.tenant, tc.calls, t.summary FROM task_codes tc
                     JOIN tasks t ON t.jira = tc.jira""" + (" WHERE tc.code=?" if a.code else "") + " ORDER BY tc.code"
            for r in con.execute(sql, (a.code,) if a.code else ()):
                scope = r["tenant"] or "all tenants"
                calls = json.loads(r["calls"])
                print(f"{r['code']:20} {r['jira']:9} {r['status']:5} {scope}"
                      + (f" · {len(calls)} call(s)" if calls else "") + f"  {r['summary'][:70]}")
        elif a.cmd == "record-run":
            run = record_run(con, a.findings, env=a.env, since=a.since, until=a.until, by=a.by,
                             versions=json.loads(a.versions) if a.versions else None)
            n = con.execute("SELECT count(*) FROM findings WHERE run_id=?", (run,)).fetchone()[0]
            print(f"run {run}: {n} findings recorded")
        elif a.cmd == "trend":
            sql = """SELECT r.id, r.env, r.window_since, r.window_until, r.catalog_version, r.calls,
                            count(f.code) AS n
                       FROM runs r LEFT JOIN findings f ON f.run_id = r.id AND f.code = ?""" + \
                  (" AND f.tenant = ?" if a.tenant else "") + " GROUP BY r.id ORDER BY r.id"
            for r in con.execute(sql, (a.code, a.tenant) if a.tenant else (a.code,)):
                print(f"run {r['id']:4} {r['env']:4} {r['window_since'] or '?'} → {r['window_until'] or '?'} "
                      f"catalog {r['catalog_version']}  {r['n']} / {r['calls'] or '?'} calls")
    except StoreError as exc:
        print(f"✘ {exc}", file=sys.stderr)
        return 1
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
