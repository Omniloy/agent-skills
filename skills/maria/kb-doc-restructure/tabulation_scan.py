#!/usr/bin/env python3
"""Scan a source FAQ document for repetitive info worth tabulating (MAR-1140).

Repetitive, structured data — phones, schedules/horarios, prices, addresses,
emails — is far easier for the agent to retrieve correctly when it lives in a
single Markdown table than when it is scattered through prose. This helper does
NOT tabulate anything: it just counts candidates and prints them so the agent
running the skill can SHOW the user and ASK for confirmation before building a
table (the skill never tabulates silently).

Markdown tables produced after confirmation must be GitHub-flavoured pipe tables
(``| col | col |`` + ``| --- | --- |``) so PageIndex's md pipeline ingests them.

Usage:
    python3 tabulation_scan.py --doc ORIGINAL.txt
    # exit 0 always; this is advisory, not a gate.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

_PATTERNS = {
    "phone": re.compile(r"(?:\+?\d{1,3}[\s.-]?)?(?:\d[\s.-]?){8}\d"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "price": re.compile(r"\d{1,4}(?:[.,]\d{1,2})?\s*(?:€|euros?)", re.IGNORECASE),
    # schedules: "de 9 a 14", "9:00-14:00", "lunes a viernes", "horario"
    "schedule": re.compile(
        r"\b\d{1,2}[:h]\d{0,2}\s*(?:-|a)\s*\d{1,2}[:h]\d{0,2}\b"
        r"|\b(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)\b"
        r"|\bhorario\b",
        re.IGNORECASE,
    ),
    # addresses: "c/", "calle", "avenida", "av.", "plaza", postal code
    "address": re.compile(
        r"\b(?:c/|calle|avda?\.?|avenida|plaza|pza\.?|paseo)\b|\b\d{5}\b",
        re.IGNORECASE,
    ),
}

# Tabulating only makes sense when a kind appears repeatedly.
_MIN_OCCURRENCES = 2


def scan(text: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for kind, rx in _PATTERNS.items():
        hits = [m.group(0).strip() for m in rx.finditer(text)]
        if len(hits) >= _MIN_OCCURRENCES:
            found[kind] = hits
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--doc", required=True, type=Path)
    args = ap.parse_args()
    text = args.doc.read_text(encoding="utf-8", errors="replace")
    found = scan(text)

    print("=" * 64)
    print("TABULATION CANDIDATES (advisory — ASK THE USER before tabulating)")
    print(f"  doc: {args.doc}")
    print("=" * 64)
    if not found:
        print("No clearly repetitive structured data found. Nothing to propose.")
        return 0
    for kind, hits in found.items():
        sample = ", ".join(dict.fromkeys(hits))[:300]
        print(f"\n[{kind}] {len(hits)} occurrence(s)")
        print(f"   e.g.: {sample}")
    print(
        "\nPropose tabulating the above as Markdown pipe tables, then ASK the "
        "user to confirm before changing the document. Do NOT tabulate silently."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
