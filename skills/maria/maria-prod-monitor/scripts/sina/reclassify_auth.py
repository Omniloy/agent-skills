#!/usr/bin/env python3
"""Reclasifica los casos de identificación con lo que contestó SINA.

La primera pasada deducía el motivo del fallo de lo que el agente volvía a
pedir. Funciona en los casos claros y se equivoca justo en los ambiguos. El log
de maria-core trae la respuesta literal del proveedor (`EXT-PAP-*`), así que
donde el log llega, **sustituye a la inferencia**; donde no llega, la inferencia
se queda pero marcada como tal, nunca ascendida a verdad.

Reglas, en orden:

1. **La respuesta del proveedor manda.** El último `EXT-PAP-*` de la llamada es
   el que la bloqueó, y ese es el código de causa (`L2-AUTH-018/019/020`). Los
   anteriores van en la evidencia como `cadena`, porque la progresión es
   información: `00006 → 00007` significa que la fecha se arregló y el muro fue
   el teléfono.

2. **Los códigos de gestión sobreviven.** `L2-AUTH-010/011/012` (si la
   validación parcial podía, corrió y tenía datos) y los de juicio
   (`002`, `008`, `013…017`) responden a otra pregunta —si lo gestionamos
   bien—, y son ortogonales a lo que dijo SINA. Un caso puede y debe llevar
   los dos: la causa y la gestión.

3. **Se retiran los códigos cuya premisa el log desmiente.** `L2-AUTH-007` (su
   premisa no es observable), `L2-AUTH-003` (el documento no llega al HIS →
   `L2-AUTH-021`), `L2-AUTH-001` (paraguas, sobra si hay causa) y `L2-AUTH-006`
   («coincide todo y no identifica») cuando SINA dijo exactamente qué no
   coincidía.

4b. **Sin intento no hay causa que buscar.** Una llamada que acaba en
   `auth_failed` sin ni una llamada a `login_and_onboard` no es un caso de
   evidencia insuficiente: es `L2-AUTH-022`, y la pregunta que abre es por qué
   no se pidió el documento.

5. **Sin cobertura de log no se inventa.** Si el intento cae fuera de la ventana
   de retención, el caso conserva lo inferido y queda `evidencia: inferida`. La
   diferencia entre «lo sabemos» y «lo dedujimos» tiene que sobrevivir al
   informe.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collectors"))

from parse_core_log import PROVIDER_CODES, annotate, parse  # noqa: E402

#: `EXT-PAP-*` → código de causa del catálogo.
CAUSE_OF = {
    "EXT-PAP-00008": "L2-AUTH-018",
    "EXT-PAP-00006": "L2-AUTH-019",
    "EXT-PAP-00007": "L2-AUTH-020",
}

#: Códigos de GESTIÓN: dicen si lo hicimos bien, no qué pasó. Ortogonales.
HANDLING_CODES = {
    "L2-AUTH-002", "L2-AUTH-008", "L2-AUTH-009",
    "L2-AUTH-010", "L2-AUTH-011", "L2-AUTH-012",
    "L2-AUTH-013", "L2-AUTH-014", "L2-AUTH-015", "L2-AUTH-016", "L2-AUTH-017",
}

#: Retirados siempre, con su sustituto.
RETIRED = {
    "L2-AUTH-007": "L2-AUTH-019",
    "L2-AUTH-003": "L2-AUTH-021",
}

#: El paraguas y el «coincide todo»: solo sobran si hay causa del proveedor.
SUPERSEDED_BY_PROVIDER = {"L2-AUTH-001", "L2-AUTH-006"}

#: Huellas de nuestra validación local (`authentication_tools.py`, main): si el
#: mensaje es nuestro, el documento no llegó al HIS. Detectable sin logs.
LOCAL_REJECT_MARKS = (
    "no se corresponde con ningún tipo de documento válido",
    "la última letra del documento debería ser",
)


def _output_text(attempt: dict) -> str:
    raw = attempt.get("output") or ""
    try:
        return json.loads(raw).get("text") or ""
    except (json.JSONDecodeError, AttributeError):
        return str(raw)


def load_cases(path: str) -> List[dict]:
    payload = json.load(open(path))
    return payload["cases"] if isinstance(payload, dict) else payload


def load_reviews(patterns: List[str]) -> Dict[str, dict]:
    """Revisiones por `call_id`, **deduplicadas**.

    Los ficheros por cliente se solapan (un mismo caso aparece en dos), y eso
    no es inocuo: el emparejamiento con el log es uno a uno, así que dos copias
    del mismo caso se roban la línea de log la una a la otra. Se unen los
    códigos y se queda la copia con más intentos.
    """
    merged: Dict[str, dict] = {}
    for pattern in patterns:
        for path in sorted(glob.glob(pattern)):
            for case in json.load(open(path)).get("cases", []):
                call_id = case["call_id"]
                previous = merged.get(call_id)
                if previous is None:
                    merged[call_id] = dict(case)
                    continue
                codes = list(dict.fromkeys((previous.get("codes") or []) + (case.get("codes") or [])))
                keep = case if len(case.get("attempts") or []) > len(previous.get("attempts") or []) else previous
                merged[call_id] = {**keep, "codes": codes,
                                   "sina_findings": (previous.get("sina_findings") or []) or case.get("sina_findings") or []}
    return merged


def reclassify(case: dict, review: Optional[dict], bundle: Optional[dict] = None) -> dict:
    """Devuelve el caso con `codes` recalculados y la traza de por qué."""
    chain: List[str] = []
    for attempt in case.get("attempts", []):
        for code in attempt.get("provider_codes") or []:
            chain.append(code)

    local_reject = [
        a for a in case.get("attempts", [])
        if any(mark in _output_text(a).lower() or mark in _output_text(a)
               for mark in LOCAL_REJECT_MARKS)
    ]
    covered = any(
        a.get("provider_codes") or a.get("provider_lookup") == "sin_linea_en_el_log"
        for a in case.get("attempts", [])
    )

    inherited = list((review or {}).get("codes") or [])
    codes: List[str] = []
    retired: List[str] = []

    # 1. la causa, según el proveedor
    if chain:
        codes.append(CAUSE_OF[chain[-1]])

    # 3. retirados / sustituidos
    for code in inherited:
        if code in RETIRED:
            retired.append(code)
            replacement = RETIRED[code]
            # `L2-AUTH-003` → `021` solo si el mensaje local lo respalda; el
            # `007` → `019` solo si el proveedor lo dijo (regla 4: no inventar).
            if replacement == "L2-AUTH-021" and local_reject:
                codes.append(replacement)
            continue
        if code in SUPERSEDED_BY_PROVIDER and chain:
            retired.append(code)
            continue
        if code in HANDLING_CODES or code in SUPERSEDED_BY_PROVIDER:
            codes.append(code)          # 2. gestión, o paraguas si no hay causa

    # el mensaje local es evidencia por sí solo, aunque la revisión no lo viera
    if local_reject and "L2-AUTH-021" not in codes:
        codes.append("L2-AUTH-021")

    codes = list(dict.fromkeys(codes))
    if not codes:
        # Sin ningún intento no es que no sepamos por qué falló: es que no se
        # intentó, y eso es un hallazgo propio, no un hueco de evidencia.
        codes = ["L2-AUTH-022"] if not case.get("attempts") else ["L2-AUTH-001"]

    if chain:
        evidence_kind = "proveedor"
    elif local_reject:
        evidence_kind = "local"
    elif not case.get("attempts"):
        evidence_kind = "sin_intento"
    else:
        evidence_kind = "inferida"
    findings = []
    for code in codes:
        entry = {"code": code, "evidencia": evidence_kind}
        if code in CAUSE_OF.values() and chain:
            entry.update({
                "codigo_del_proveedor": chain[-1],
                "significado": PROVIDER_CODES[chain[-1]],
                "cadena": " → ".join(chain) if len(chain) > 1 else None,
            })
        if code == "L2-AUTH-022" and bundle is not None:
            # ¿Colgó el paciente antes de dar el documento? Es la diferencia
            # entre «no había nada que hacer» y «no se le pidió», y decide si
            # el caso es accionable. La KEDB lo usa como condición.
            events = [e["event"] for e in bundle.get("system_events", [])]
            entry["colgo_paciente"] = "patient_hangup" in events
            entry["turnos"] = f"agente {bundle['turns']['agent']} / paciente {bundle['turns']['patient']}"
        if code == "L2-AUTH-021" and local_reject:
            entry.update({
                "documentos_rechazados": [a.get("document_number") for a in local_reject],
                "llego_al_his": False,
            })
        # Lo que se vio en la ficha de SINA, si la revisión lo trae. Va con
        # nombre propio (`_de_la_ficha`) a propósito: es otra fuente que el
        # código del proveedor, y mezclarlas en la misma clave haría creer que
        # lo dijo SINA cuando lo dedujo la comparación manual. Pueden incluso
        # discrepar —la ficha coincidía y el proveedor la rechaza—, y eso es
        # justo el hallazgo del epoch/zona horaria, no un error que tapar.
        for old in (review or {}).get("sina_findings", []):
            if old.get("code") == code or (old.get("mismatches") and code in CAUSE_OF.values()):
                entry.setdefault("veredicto_de_la_ficha", old.get("verdict"))
                entry.setdefault("desajustes_de_la_ficha", old.get("mismatches"))
                entry.setdefault("ficha", old.get("sina"))
                entry.setdefault("enviado", old.get("agent_sent"))
        findings.append(entry)

    return {
        **case,
        "codes": codes,
        "codigos_previos": inherited,
        "codigos_retirados": retired,
        "evidencia": evidence_kind,
        "cobertura_de_log": covered,
        "sina_findings": findings,
        "documents_tried": (review or {}).get("documents_tried")
                           or sorted({a["document_number"] for a in case.get("attempts", [])
                                      if a.get("document_number")}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--reviews", nargs="+", required=True)
    parser.add_argument("--core-log", nargs="+", required=True)
    parser.add_argument("--bundles", help="para saber si colgó el paciente (L2-AUTH-022)")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    cases = load_cases(args.cases)
    reviews = load_reviews(args.reviews)
    print(f"  casos: {len(cases)} · revisiones únicas: {len(reviews)}")

    events = parse([Path(p) for p in args.core_log])
    stats = annotate(cases, events)
    print(f"  intentos con respuesta de SINA: {stats['matched']} "
          f"(no llegaron al HIS: {stats['unmatched']}, "
          f"fuera de la ventana: {stats['out_of_window']})")

    bundles = {}
    if args.bundles:
        bundles = {b["call_id"]: b for b in json.load(open(args.bundles))["bundles"]}
    out = [reclassify(case, reviews.get(case["call_id"]), bundles.get(case["call_id"]))
           for case in cases]

    from collections import Counter
    before = Counter(c for case in out for c in case["codigos_previos"])
    after = Counter(c for case in out for c in case["codes"])
    print("\n  antes:", dict(before.most_common()))
    print("  ahora:", dict(after.most_common()))
    print("  por evidencia:", dict(Counter(c["evidencia"] for c in out)))
    retired = Counter(c for case in out for c in case["codigos_retirados"])
    print("  retirados:", dict(retired))

    json.dump({"code": "reclasificacion_auth", "cases": out},
              open(args.out, "w"), ensure_ascii=False, indent=1)
    print(f"\n  escrito: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
