#!/usr/bin/env python3
"""Watch a production pilot window of ONE customer, live. Read-only.

Distilled from the monitors of San Roque's five production pilots (21/09–25/09/2026). Every
few minutes it reads the customer's new or changed calls from Supabase and saves a full copy
of each one (call row + workflow transitions) to --out, so no call is lost when pods rotate.
It also prints one line per call with the flags that proved to be signal.

The flags triage; they do not classify. The classification is done by reading the
transcripts: label every call in `labels.json` (created next to the copies), then run
`pilot_report.py`.

    python3 pilot_watch.py --api-key-id <uuid> --since 2026-09-25T09:25:05Z --out <dir> \
        [--until <Z>] [--env prod] [--every 180] [--minutes 480] [--exclude-phone-suffix 1234 …] \
        [--error-node n_error] [--human-failure-node n_fallo_operador] \
        [--flag NAME=REGEX …]      # extra agent-text flags for this pilot's changes

`--exclude-phone-suffix` marks the team's own test calls, identified by the LAST digits only:
never put a full phone number in a command, a file or a report.
Only the last three digits of the caller's phone are stored in the copies.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
import time
import unicodedata

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "collectors"))
from _supabase import Supabase  # noqa: E402


def norm(t: str) -> str:
    t = "".join(c for c in unicodedata.normalize("NFD", t or "") if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]+", " ", t.lower())


#: Agent-text flags that were signal in every pilot (Spanish flows).
AGENT_FLAGS = {
    "ID-EN-VOZ-ALTA": r"\b\d{6,}\b|\b[a-z]?\d{7,8}[a-z]\b|\bpat \d+",
    "DISCULPA-TECNICA": r"problema tecnico|error tecnico|no he podido|ha habido un problema",
    "LEE-UN-ERROR": r"\bhttp\b|\b50\d\b|servicio de la herramienta|codigo de error",
    "NIEGA-LO-QUE-NO-VE": r"no (tiene|existe|consta) ninguna cita|no se puede citar por telefono",
}
ASKS_DOCUMENT = re.compile(r"(me (indica|dice|facilita|da|proporciona)|indiqueme|digame|necesito)\b.{0,40}"
                           r"\b(dni|nie|documento)")
HELLO = re.compile(r"^\s*(hola|oiga|oye|hay alguien|me oye|me escucha)\s*$")
ASKS_PERSON = re.compile(r"\b(persona|operador|companer|agente humano|alguien que)\b")


def flags(call: dict, transitions: list, transcript: list, a) -> list[str]:
    out = []
    agent = [norm(str(m.get("content") or "")) for m in transcript if m.get("speaker") != "patient"]
    caller = [m for m in transcript if m.get("speaker") == "patient"]
    targets = [t.get("to_node_id") for t in transitions]
    if not transitions:
        out.append("SIN-TRANSICIONES")
    else:
        if targets[-1] == a.error_node:
            out.append("FIN-EN-ERROR")
        if a.human_failure_node and a.human_failure_node in targets:
            out.append("FALLO→PERSONA")
        for i in range(len(targets) - 2):
            if targets[i] == targets[i + 1] == targets[i + 2]:
                out.append(f"BUCLE-NODO:{targets[i]}")
                break
    if not caller:
        out.append("NADIE-HABLO")
    if call.get("status") == "transferred" and call.get("transfer_failure_reason"):
        out.append("TRANSFER-FALLIDA")
    blob = " ".join(agent)
    for name, rx in {**AGENT_FLAGS, **dict(f.split("=", 1) for f in a.flag)}.items():
        if re.search(rx, blob):
            out.append(name)
    if sum(1 for t in agent if ASKS_DOCUMENT.search(t)) >= 2:
        out.append("PIDE-DOCUMENTO-2-VECES")
    if any(HELLO.match(norm(str(m.get("content") or ""))) for m in caller[1:]):
        out.append("¿HOLA?")          # the caller probing a silence
    if ASKS_PERSON.search(norm(" ".join(str(m.get("content") or "") for m in caller))):
        out.append("PIDE-PERSONA")
    return out


def one_pass(db: Supabase, a, seen: dict, phones: dict) -> list[str]:
    new = []
    window = [f"api_key_id=eq.{a.api_key_id}", f"created_at=gte.{a.since}"]
    if a.until:
        window.append(f"created_at=lt.{a.until}")
    calls = db.select("calls", columns="*", filters=window, order="created_at.asc")
    for c in calls:
        cid = c["id"]
        signature = (c.get("status"), c.get("ended_at"), len(c.get("transcription") or []))
        if seen.get(cid) == signature:
            continue
        seen[cid] = signature
        transitions = db.select("call_workflow_transitions", columns="*", filters=[f"call_id=eq.{cid}"],
                                order="created_at.asc")
        eu = c.get("end_user_id")
        if eu and eu not in phones:
            rows = db.select("end_users", columns="phone", filters=[f"id=eq.{eu}"])
            phones[eu] = (rows[0]["phone"] if rows else "") or ""
        phone = phones.get(eu, "")
        transcript = c.get("transcription") or []
        fl = flags(c, transitions, transcript, a)
        if any(phone.endswith(s) for s in a.exclude_phone_suffix):
            fl.insert(0, "PRUEBA-DEL-EQUIPO")
        (a.out / f"{cid}.json").write_text(json.dumps(
            {"call": c, "transitions": transitions, "flags": fl, "phone_tail": phone[-3:]},
            ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        path = " → ".join(t.get("to_node_id") or "?" for t in transitions) or "(no workflow)"
        new.append(f"{c['created_at'][11:19]} {cid[:8]} {str(c.get('status')):12} {str(c.get('call_result')):22} "
                   f"turns={len(transcript):3} | {path[:90]} | {' · '.join(fl) or 'no flags'}")
    labels = a.out / "labels.json"
    current = json.loads(labels.read_text()) if labels.exists() else {}
    for cid in seen:
        current.setdefault(cid[:8], {"label": None, "note": "", "offered_person": False})
    labels.write_text(json.dumps(current, ensure_ascii=False, indent=1), encoding="utf-8")
    return new


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--env", default="prod", choices=["prod", "stg", "dev"])
    ap.add_argument("--api-key-id", required=True)
    ap.add_argument("--since", required=True, help="ISO UTC with Z: the activation instant")
    ap.add_argument("--until", help="ISO UTC with Z: end of the window (e.g. the deactivation instant)")
    ap.add_argument("--out", required=True, type=pathlib.Path)
    ap.add_argument("--every", type=int, default=180, help="seconds between passes")
    ap.add_argument("--minutes", type=int, default=480, help="stop after this long")
    ap.add_argument("--exclude-phone-suffix", action="append", default=[])
    ap.add_argument("--error-node", default="n_error")
    ap.add_argument("--human-failure-node", default="n_fallo_operador")
    ap.add_argument("--flag", action="append", default=[], metavar="NAME=REGEX")
    ap.add_argument("--once", action="store_true", help="a single pass, then exit")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    db, seen, phones = Supabase(a.env), {}, {}
    end = time.time() + a.minutes * 60
    while True:
        try:
            new = one_pass(db, a, seen, phones)
        except Exception as exc:  # keep watching: one failed poll must not end the pilot's record
            new = [f"!! poll failed: {type(exc).__name__}: {exc}"]
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%H:%M:%S")
        print(f"--- {stamp} UTC · {len(new)} new/changed ({len(seen)} calls)")
        for line in new:
            print("   ", line)
        sys.stdout.flush()
        if a.once or time.time() >= end:
            print(f"=== done: {len(seen)} calls in {a.out}; label them in {a.out / 'labels.json'}")
            return 0
        time.sleep(a.every)


if __name__ == "__main__":
    raise SystemExit(main())
