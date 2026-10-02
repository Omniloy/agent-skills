#!/usr/bin/env python3
"""Summarise a labelled pilot window (the output of pilot_watch.py) as Markdown.

Labels (in `labels.json`, one per call id prefix), the same yardstick used for San Roque's
pilots so that windows compare:

  correcto   the caller got what they called for, or was handed to a person with a reason
  parcial    part of it, or with friction (repeated data, a wrong turn recovered)
  sin_nada   the caller left with nothing
  directa    the caller asked for a person/service straight away (outside the denominator)
  muda       nobody spoke (outside the denominator)
  prueba     the team's own test call (outside everything)

`offered_person: true` marks a `correcto` that ended with the agent offering a person — the
number that tells you how much the flow resolves by itself.

    python3 pilot_report.py <dir> [--title "Pilot 5 · flow v43"] [--out report.md]
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

VALID = {"correcto", "parcial", "sin_nada", "directa", "muda", "prueba"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", type=pathlib.Path)
    ap.add_argument("--title", default="Pilot window")
    ap.add_argument("--out", type=pathlib.Path)
    a = ap.parse_args()
    labels = json.loads((a.dir / "labels.json").read_text(encoding="utf-8"))
    calls = {}
    for f in a.dir.glob("*-*-*-*-*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        calls[d["call"]["id"][:8]] = d
    pending = [k for k, v in labels.items() if v.get("label") is None]
    wrong = [k for k, v in labels.items() if v.get("label") not in VALID | {None}]
    if wrong:
        sys.exit(f"✘ unknown labels on {wrong}; valid: {sorted(VALID)}")
    done = {k: v for k, v in labels.items() if v.get("label") and v["label"] != "prueba"}
    c = collections.Counter(v["label"] for v in done.values())
    den = len(done) - c["directa"] - c["muda"]
    pct = (lambda n: f"{n} ({round(100 * n / den)} %)") if den else (lambda n: str(n))
    offered = sum(1 for v in done.values() if v["label"] == "correcto" and v.get("offered_person"))
    times = sorted(calls[k]["call"]["created_at"] for k in done if k in calls)
    lines = [f"# {a.title}", ""]
    if times:
        lines.append(f"Window {times[0][:16]}Z → {times[-1][:16]}Z · {len(done)} calls "
                     f"({c['directa']} asked for a person directly, {c['muda']} silent) · denominator {den}")
    if pending:
        lines.append(f"\n> ⚠ {len(pending)} call(s) not labelled yet: {', '.join(sorted(pending))}")
    lines += ["", "| outcome | calls |", "|---|---|",
              f"| correcto | {pct(c['correcto'])} — of which {offered} handed to a person by the agent |",
              f"| parcial | {pct(c['parcial'])} |", f"| sin nada | {pct(c['sin_nada'])} |", "",
              "## Calls that did not end well", ""]
    for k in sorted(done, key=lambda k: calls.get(k, {}).get("call", {}).get("created_at", "")):
        v = done[k]
        if v["label"] in ("parcial", "sin_nada"):
            flags = " · ".join(calls.get(k, {}).get("flags", []))
            lines.append(f"- `{k}` **{v['label']}** — {v.get('note', '')}" + (f"  _({flags})_" if flags else ""))
    text = "\n".join(lines) + "\n"
    if a.out:
        a.out.write_text(text, encoding="utf-8")
        print(f"written {a.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
