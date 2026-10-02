#!/usr/bin/env python3
"""Los casos de `L2-AUTH-001` en la forma que hace falta para revisarlos en SINA.

Para cada llamada que acabó sin identificar al paciente, junta lo que el agente
realmente envió (`call_timeline.tool_input` de `login_and_onboard`: documento y
fecha de nacimiento), el teléfono del que llamaba (va en `calls.room_name`) y lo
que SINA respondió. Eso es exactamente lo que hay que comparar contra la ficha:
si el documento no existe, si hay más de un registro, o si existe pero con otro
teléfono / fecha / apellidos.

Salida: JSON con los casos + un resumen por consola. El JSON lleva datos de
paciente (documento, teléfono), así que se queda en `docs/wip/`, que está
excluido de git a propósito.

Uso:
    python3 auth_cases.py /tmp/f.json --out /tmp/auth_cases.json
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from typing import Any, Dict, List, Optional

from _supabase import Supabase, load_keys

CODE = "L2-AUTH-001"

#: `hcb-es-_+34*****080_sEtK5ovkT3UZ` → el teléfono del que llamaba.
_ROOM_PHONE = re.compile(r"_(\+\d{6,15})")

#: Códigos del proveedor, por si asoman en la salida de la tool.
_PROVIDER_CODE = re.compile(r"\b(EXT-[A-Z]+-\d+|SINA-[A-Z]+-[A-Z]+-[A-Z0-9]+)\b")


def phone_of(room_name: Optional[str]) -> Optional[str]:
    match = _ROOM_PHONE.search(room_name or "")
    return match.group(1) if match else None


def _as_text(value: Any, limit: int = 400) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text[:limit]


def collect(findings_path: str, env: str = "prod") -> List[dict]:
    payload = json.load(open(findings_path))
    call_ids = [
        r["call_id"] for r in payload["results"] if any(f["code"] == CODE for f in r["findings"])
    ]
    if not call_ids:
        return []

    db = Supabase(env, load_keys())
    calls = {
        row["id"]: row
        for row in db.select_by_chunks(
            "calls",
            column="id",
            values=call_ids,
            columns="id,api_key_id,room_name,status,call_result,user_intent,started_at,ended_at",
        )
    }
    tenants = {
        row["id"]: row.get("company_name")
        for row in db.select("api_keys", columns="id,company_name", limit=500)
    }
    timeline = db.select_by_chunks(
        "call_timeline",
        column="call_id",
        values=call_ids,
        columns="call_id,occurred_at,tool_name,tool_status,tool_input,tool_output,tool_error",
        filters=["type=eq.tool_call"],
        order="occurred_at.asc",
    )

    by_call: Dict[str, List[dict]] = {}
    for row in timeline:
        by_call.setdefault(row["call_id"], []).append(row)

    cases = []
    for call_id in call_ids:
        call = calls.get(call_id, {})
        tools = by_call.get(call_id, [])
        logins = [t for t in tools if t.get("tool_name") == "login_and_onboard"]
        attempts = []
        for login in logins:
            args = login.get("tool_input") if isinstance(login.get("tool_input"), dict) else {}
            output = _as_text(login.get("tool_output"))
            attempts.append(
                {
                    "at": login.get("occurred_at"),
                    "document_number": args.get("document_number"),
                    "birth_date": args.get("birth_date"),
                    "first_surname": args.get("first_surname"),
                    "phone": args.get("phone_number") or args.get("phone"),
                    "status": login.get("tool_status"),
                    "output": output,
                    "provider_codes": sorted(set(_PROVIDER_CODE.findall(output))),
                }
            )
        documents = [a["document_number"] for a in attempts if a.get("document_number")]
        cases.append(
            {
                "call_id": call_id,
                "tenant": tenants.get(call.get("api_key_id")),
                "caller_phone": phone_of(call.get("room_name")),
                "status": call.get("status"),
                "call_result": call.get("call_result"),
                "user_intent": call.get("user_intent"),
                "started_at": call.get("started_at"),
                "attempts": attempts,
                "documents_tried": sorted(set(documents)),
                "tools": [t.get("tool_name") for t in tools],
                # Lo que hay que ir a comprobar en SINA, por caso.
                "sina_check": "pending" if documents else "no_document_dictated",
            }
        )
    return cases


def summarize(cases: List[dict]) -> None:
    print(f"casos {CODE}: {len(cases)}")
    print("por cliente:", dict(Counter(c["tenant"] for c in cases).most_common()))
    with_doc = [c for c in cases if c["documents_tried"]]
    print(f"con documento dictado: {len(with_doc)} · sin ninguno: {len(cases) - len(with_doc)}")
    print("documentos distintos a buscar en SINA:",
          len({d for c in with_doc for d in c["documents_tried"]}))
    codes = Counter(code for c in cases for a in c["attempts"] for code in a["provider_codes"])
    print("códigos del proveedor visibles en tool_output:", dict(codes) or "ninguno")
    print("intentos por llamada:", dict(Counter(len(c["attempts"]) for c in cases).most_common()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("findings")
    parser.add_argument("--env", default="prod", choices=["prod", "stg", "dev"])
    parser.add_argument("--out")
    args = parser.parse_args()

    cases = collect(args.findings, args.env)
    summarize(cases)
    if args.out:
        with open(args.out, "w") as handle:
            json.dump({"code": CODE, "env": args.env, "cases": cases}, handle,
                      ensure_ascii=False, indent=1)
        print(f"escrito: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
