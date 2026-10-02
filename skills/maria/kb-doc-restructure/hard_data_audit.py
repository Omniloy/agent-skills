#!/usr/bin/env python3
"""Layer-1 faithfulness audit: deterministic hard-data diff.

Compares the ORIGINAL document against a RESTRUCTURED ``.md`` and reports any
*hard data* — phone numbers, emails, URLs, percentages, money amounts — that
was LOST (present in the original, missing in the restructured) or INVENTED
(present in the restructured, absent from the original).

This is the cheap, exact half of the two-layer audit described in the skill.
It deliberately normalizes formatting and trailing punctuation so it doesn't
raise false positives like ``foo@bar.com`` vs ``foo@bar.com.`` or a phone
written ``928 35 35 35`` vs ``928353535``. The second half — an independent
LLM reader that catches *semantic* loss/invention (a dropped sentence, a
mis-grouped section) — is a separate step run by the agent, not this script.

Usage:
    python3 hard_data_audit.py --original ORIG.(txt|md) --restructured NEW.md
    # exit code 0 = clean, 1 = findings (lost or invented hard data)
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

# tts_rewrite lives next to this script; the skill runs it as a standalone file
# so the script dir is already on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tts_rewrite import email_to_words, phone_to_words, url_to_words  # noqa: E402

# --- extractors ---------------------------------------------------------------

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"https?://[^\s)>\]]+", re.IGNORECASE)
_PCT_RE = re.compile(r"\d{1,3}(?:[.,]\d+)?\s*%")
_MONEY_RE = re.compile(r"\d{1,3}(?:[.,]\d{1,2})?\s*(?:€|euros?)", re.IGNORECASE)
# Phone: Spanish landline/mobile, possibly grouped with spaces; 9 digits.
_PHONE_RE = re.compile(r"(?:\+?\d{1,3}[\s.-]?)?(?:\d[\s.-]?){8}\d")


def _strip_accents(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def _emails(text: str) -> set[str]:
    return {m.group(0).rstrip(".,;:)").lower() for m in _EMAIL_RE.finditer(text)}


def _urls(text: str) -> set[str]:
    return {m.group(0).rstrip(".,;:)>").lower() for m in _URL_RE.finditer(text)}


def _pcts(text: str) -> set[str]:
    return {re.sub(r"\s+", "", m.group(0)).replace(",", ".") for m in _PCT_RE.finditer(text)}


def _money(text: str) -> set[str]:
    out = set()
    for m in _MONEY_RE.finditer(text):
        norm = re.sub(r"\s+", "", _strip_accents(m.group(0)).lower())
        norm = norm.replace("euros", "e").replace("euro", "e").replace("€", "e").replace(",", ".")
        out.add(norm)
    return out


def _phones(text: str) -> set[str]:
    """Normalize phones to a bare digit string, dropping a leading country code
    so ``+34 600 071 234`` and ``600071234`` compare equal."""
    out = set()
    for m in _PHONE_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) < 9:
            continue
        # keep the last 9 digits (national number) for comparison
        out.add(digits[-9:])
    return out


_EXTRACTORS = {
    "email": _emails,
    "url": _urls,
    "percentage": _pcts,
    "money": _money,
    "phone": _phones,
}

# Kinds whose value may have been TTS-rewritten in the restructured doc, so the
# raw token (e.g. "928353535") is gone and only its spoken form is present
# ("nueve dos ocho treinta y cinco treinta y cinco treinta y cinco"). For these, a raw item from the
# original counts as PRESENT if its spoken form is found verbatim in the
# restructured text — otherwise the TTS bake would be flagged as a false LOST.
_SPOKEN = {
    "phone": phone_to_words,
    "email": email_to_words,
    "url": url_to_words,
}


def _norm_text(s: str) -> str:
    """Lowercase, strip accents and collapse whitespace, for substring search of
    a spoken form inside the restructured document."""
    s = _strip_accents(s).lower()
    return re.sub(r"\s+", " ", s)


def audit(original: str, restructured: str) -> dict[str, dict[str, list[str]]]:
    report: dict[str, dict[str, list[str]]] = {}
    restructured_norm = _norm_text(restructured)
    for kind, fn in _EXTRACTORS.items():
        o, r = fn(original), fn(restructured)
        if kind in _SPOKEN:
            speak = _SPOKEN[kind]
            lost = []
            for item in sorted(o):
                if item in r:
                    continue  # raw form preserved
                spoken = _norm_text(speak(item))
                if spoken and spoken in restructured_norm:
                    continue  # TTS-equivalent spoken form present
                lost.append(item)
        else:
            lost = sorted(o - r)
        invented = sorted(r - o)
        if lost or invented:
            report[kind] = {"lost": lost, "invented": invented}
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--original", required=True, type=Path)
    ap.add_argument("--restructured", required=True, type=Path)
    args = ap.parse_args()

    original = args.original.read_text(encoding="utf-8", errors="replace")
    restructured = args.restructured.read_text(encoding="utf-8", errors="replace")

    report = audit(original, restructured)

    print("=" * 64)
    print("LAYER-1 HARD-DATA AUDIT (deterministic)")
    print(f"  original:     {args.original}")
    print(f"  restructured: {args.restructured}")
    print("=" * 64)
    if not report:
        print(
            "CLEAN — every phone / email / URL / % / amount in the original "
            "appears in the restructured doc, and nothing new was invented."
        )
        print(
            "\nNOTE: this only checks hard data. You MUST still run the "
            "layer-2 independent LLM reader for semantic loss/invention, and "
            "the grounded eval against the live system."
        )
        return 0

    for kind, diff in report.items():
        if diff["lost"]:
            print(f"\n[{kind}] LOST (in original, missing in restructured):")
            for v in diff["lost"]:
                print(f"   - {v}")
        if diff["invented"]:
            print(f"\n[{kind}] INVENTED (in restructured, not in original):")
            for v in diff["invented"]:
                print(f"   + {v}")
    print(
        "\nFINDINGS — resolve each before accepting the .md. A 'lost' item is "
        "usually a real omission; an 'invented' item is usually a "
        "hallucination or a reformatting artifact (verify by hand)."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
