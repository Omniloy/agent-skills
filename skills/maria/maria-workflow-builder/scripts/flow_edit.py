#!/usr/bin/env python3
"""The frame every flow edit script is written in: dry-run by default, one assert per change.

An edit script is a small file that declares its changes and hands control to `Edit.main()`:

    import sys; sys.path.insert(0, "<skill>/scripts")
    from flow_edit import Edit, node, edge, text, set_text

    ed = Edit(env="stg", flow_id="<clone uuid>", api_key_env="ACME_API_KEY",
              description="[ACME][MAR-1234] v7 · n_citas: an empty list is «I cannot see it», "
                          "never «you have none» (call 1a2b3c4d).",
              allowed_hosts=["pre-api.acme.example"])

    OLD = "Si no hay citas, dile que no tiene ninguna."
    NEW = "Si no hay citas, dile que a ti no te aparece ninguna y ofrécele una persona."

    @ed.change("M1 · n_citas: empty list is not a negative")
    def m1(doc):
        n = node(doc, "n_citas")
        t = text(n["instruction"])
        assert t.count(OLD) == 1, "the sentence moved: re-read the node before editing"
        set_text(n, "instruction", t.replace(OLD, NEW))
        return "1 node"

    if __name__ == "__main__":
        ed.main()

What `main()` guarantees, in order:

1. Reads the live document (never a local copy) and its version. `--expect-version N` aborts
   if someone saved in between.
2. Refuses to write to a flow that any `api_keys.default_conversation_flow_id` points at —
   that is the customer's live flow — unless `--live-flow-ok` is passed. Clone, fix the clone,
   promote.
3. Environment guard, before AND after the changes: every webhook (including
   `nodes[].prefetch_tools`) must point at one of `allowed_hosts`. A test clone whose write
   tools reach production books real appointments.
4. Runs every change; the first failed assert aborts the whole edit with nothing written.
5. Runs the static lint on the result and prints any NEW findings (see flow_lint.py).
6. Writes the resulting document to a temp file and prints a text diff of what changed.
7. Only with `--apply`: saves through core-service (a new version), re-reads the stored
   document, checks it is byte-identical to what was sent, and runs the health query.
"""
from __future__ import annotations

import argparse
import copy
import difflib
import json
import os
import pathlib
import sys
import tempfile
from typing import Callable, List, Optional, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from flowdb import DB, FlowDBError, edge, host, iter_webhooks, node, put_flow, set_text, text  # noqa: E402,F401

__all__ = ["Edit", "node", "edge", "text", "set_text", "FlowDBError"]


def _inline_diff(old: str, new: str, context: int = 60) -> str:
    """Only the changed spans of a long text: …context[-removed-]{+added+}context…"""
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    parts = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        before = old[max(0, i1 - context):i1]
        after = old[i2:i2 + context]
        removed = f"[-{old[i1:i2]}-]" if i2 > i1 else ""
        added = f"{{+{new[j1:j2]}+}}" if j2 > j1 else ""
        parts.append(f"…{before}{removed}{added}{after}…".replace("\n", "⏎"))
    return "\n      ".join(parts)


def _print_diff(before: dict, after: dict) -> None:
    old = dict(line.split(" = ", 1) for line in _flat(before))
    new = dict(line.split(" = ", 1) for line in _flat(after))
    keys = sorted(set(old) | set(new))
    changed = [k for k in keys if old.get(k) != new.get(k)]
    print(f"\ndiff ({len(changed)} field(s)):")
    for k in changed:
        if k not in old:
            print(f"  + {k} = {new[k][:300]}")
        elif k not in new:
            print(f"  - {k} = {old[k][:300]}")
        else:
            a, b = json.loads(old[k]), json.loads(new[k])
            if isinstance(a, str) and isinstance(b, str) and max(len(a), len(b)) > 200:
                print(f"  ~ {k}\n      {_inline_diff(a, b)}")
            else:
                print(f"  ~ {k}: {old[k][:200]} -> {new[k][:200]}")


def _flat(doc: dict) -> List[str]:
    """One line per leaf, keyed by a readable path, for a diff a human can review."""
    out: List[str] = []

    def walk(prefix: str, value):
        if isinstance(value, dict):
            for k in sorted(value):
                walk(f"{prefix}.{k}" if prefix else k, value[k])
        elif isinstance(value, list):
            for i, item in enumerate(value):
                key = item.get("node_id") or item.get("edge_id") or item.get("name") \
                    if isinstance(item, dict) else None
                walk(f"{prefix}[{key or i}]", item)
        else:
            out.append(f"{prefix} = {json.dumps(value, ensure_ascii=False)}")
    walk("", doc)
    return out


class Edit:
    def __init__(self, *, env: str, flow_id: str, api_key_env: str, description: str,
                 allowed_hosts: Optional[List[str]] = None, change_type: str = "edit") -> None:
        self.env, self.flow_id, self.api_key_env = env, flow_id, api_key_env
        self.description, self.change_type = description, change_type
        self.allowed_hosts = allowed_hosts
        self.changes: List[Tuple[str, Callable[[dict], str]]] = []

    def change(self, name: str):
        def deco(fn):
            self.changes.append((name, fn))
            return fn
        return deco

    # -- guards --------------------------------------------------------------

    def _env_guard(self, doc: dict, label: str) -> str:
        if self.allowed_hosts is None:
            return "no host guard declared (allowed_hosts=None)"
        bad = [(where, t.get("name"), host(t["url"])) for where, t in iter_webhooks(doc)
               if not any(h in host(t["url"]) for h in self.allowed_hosts)]
        if bad:
            raise FlowDBError(f"{label}: webhooks outside {self.allowed_hosts}: "
                              + ", ".join(f"{w}:{n}@{h}" for w, n, h in bad))
        return f"{sum(1 for _ in iter_webhooks(doc))} webhooks, all on {self.allowed_hosts}"

    def _live_guard(self, db: DB, allow: bool) -> None:
        users = db.select("api_keys", columns="id",
                          filters=[f"default_conversation_flow_id=eq.{self.flow_id}"])
        if users and not allow:
            raise FlowDBError(
                f"{len(users)} api_key(s) serve this flow as their default: it is a LIVE customer "
                "flow. Clone it, edit the clone, promote. Pass --live-flow-ok only for a hotfix "
                "the user explicitly approved."
            )

    # -- main ------------------------------------------------------------------

    def main(self, argv: Optional[List[str]] = None) -> None:
        ap = argparse.ArgumentParser(description=self.description[:200])
        ap.add_argument("--apply", action="store_true", help="save a new version (default: dry-run)")
        ap.add_argument("--expect-version", type=int, help="abort unless the flow is at this version")
        ap.add_argument("--live-flow-ok", action="store_true",
                        help="allow writing to a flow an api_key serves (hotfix only)")
        ap.add_argument("--no-lint", action="store_true")
        args = ap.parse_args(argv)

        db = DB(self.env)
        row = db.flow(self.flow_id, columns="id,name,version,flow_document")
        print(f"flow {row['id'][:8]} «{row['name']}» v{row['version']} ({self.env})")
        if args.expect_version is not None and row["version"] != args.expect_version:
            sys.exit(f"  ✘ expected v{args.expect_version}, found v{row['version']}: someone saved "
                     "in between. Re-read and re-run.")
        before = row["flow_document"]
        doc = copy.deepcopy(before)
        try:
            self._live_guard(db, args.live_flow_ok)
            print(f"  ✔ host guard (before): {self._env_guard(doc, 'original')}")
            for name, fn in self.changes:
                print(f"  ✔ {name}\n      {fn(doc)}")
            print(f"  ✔ host guard (after): {self._env_guard(doc, 'result')}")
        except (AssertionError, FlowDBError) as exc:
            sys.exit(f"  ✘ ABORT: {exc!r}\n  nothing was written.")

        if not args.no_lint:
            from flow_lint import lint
            old = {f.key() for f in lint(before)}
            new = [f for f in lint(doc) if f.key() not in old]
            print(f"\nlint: {len(new)} new finding(s) introduced by this edit")
            for f in new:
                print(f"  {f}")
            if any(f.severity == "error" for f in new):
                sys.exit("  ✘ the edit introduces lint ERRORS; fix them or justify with --no-lint")

        _print_diff(before, doc)
        out = pathlib.Path(tempfile.gettempdir()) / f"flow_{self.flow_id[:8]}_v{row['version'] + 1}.json"
        out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nresulting document: {out}")
        if doc == before:
            sys.exit("  ✘ the changes produced an identical document; nothing to save.")

        if not args.apply:
            print("\n[dry-run] nothing written. Re-run with --apply to save a new version.")
            return
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            sys.exit(f"  ✘ {self.api_key_env} is not set (the tenant api key the PUT authenticates with)")
        put_flow(self.env, self.flow_id, api_key, flow_document=doc,
                 change_description=self.description, change_type=self.change_type)
        stored = db.flow(self.flow_id, columns="version,flow_document")
        if stored["flow_document"] != doc:
            sys.exit(f"  ✘ saved v{stored['version']} but the stored document differs from the one "
                     "sent. Inspect it before anything else.")
        ok, detail = db.health(self.flow_id)
        print(f"\n  saved v{stored['version']}; health: {detail} {'✔' if ok else '✘ CHECK'}")
        if not ok:
            sys.exit(1)
