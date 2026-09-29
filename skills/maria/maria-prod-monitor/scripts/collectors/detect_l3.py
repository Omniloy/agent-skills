#!/usr/bin/env python3
"""Convierte los veredictos del juez en hallazgos con código del catálogo.

El juez devuelve prosa y señales; el catálogo habla en códigos. Esta es la
traducción, y vive aparte a propósito: el juez puede cambiar de motor sin que
cambie el vocabulario, y el vocabulario puede crecer sin volver a juzgar nada.

Dos tablas, y la segunda es deuda declarada:

* `RULES` — el vocabulario CERRADO de la rúbrica actual, con las condiciones
  de dueño que hacen falta para no confundir un código con otro.
* `LEGACY_FAMILIES` — patrones de texto para los veredictos emitidos ANTES de
  cerrar la rúbrica. La primera pasada la dejé abierta y salieron 176 etiquetas
  distintas en dos idiomas para 77 llamadas; esto las recupera en vez de tirar
  esa lectura. Es un puente, y cuando no queden veredictos viejos se borra.

Lo que NO hace: inventar un código cuando el juez dice que la llamada no
resolvió y ninguna señal encaja. Eso es **residuo de L3** y se cuenta como tal,
porque es de donde crece esta capa — igual que el residuo determinista es de
donde crecieron L1 y L2.
"""
from __future__ import annotations

import sys
from typing import Dict, List, Optional

from _findings import finding

#: Reglas de traducción, evaluadas EN ORDEN: la primera que encaja gana, así que
#: las específicas van antes que las generales.
#:
#: No es un diccionario por señal porque varios códigos dependen de la señal Y
#: del dueño: `servicio_descatalogado` con `de_quien: ninguno` es comportamiento
#: correcto (`L3-EXPECTED-002`); con `diseno_del_flujo` sería otra cosa. Un mapeo
#: por señal sola los confundiría.
#:
#: Cada entrada: (código, señal, `resolvio` exigido o None, `de_quien` exigido o None)
RULES = [
    ("L3-SAFETY-001",   "urgencia_no_reconocida",        None, None),
    ("L3-AGENT-001",    "agente_se_contradice",          None, None),
    ("L3-FLOW-001",     "llamada_saliente_sin_contexto", None, None),
    ("L3-FLOW-004",     "detalle_de_cita_no_soportado",  None, None),
    ("L3-FLOW-005",     "menor_sin_documento",           None, None),
    ("L3-FLOW-003",     "centro_no_reconocido",          None, None),
    ("L3-EXPECTED-002", "servicio_descatalogado",        None, "ninguno"),
    ("L3-DEP-001",      "sin_huecos",                    None, "dependencia"),
    ("L3-EXPECTED-003", "paciente_abandona",             None, "paciente"),
    ("L3-FLOW-002",     "cierre_sin_salida",             None, None),
    ("L3-EXPECTED-001", "transferencia_por_diseno",      "si", None),
]

#: Puente para los veredictos anteriores al cierre de la rúbrica. El orden
#: importa: la primera familia que encaja gana, y las más específicas van antes.
LEGACY_FAMILIES: List[tuple] = [
    ("L3-SAFETY-001", ("urgencia", "urgent")),
    ("L3-AGENT-001", ("contradic", "se_desdice", "retract", "incoheren")),
    ("L3-FLOW-001", ("llamada_perdida", "outbound", "saliente", "devolucion_de_llamada")),
    ("L3-FLOW-003", ("centro_no_reconocido", "centro_no_citable", "centro_desconocido")),
    ("L3-FLOW-002", ("sin_oferta_de_transferencia", "no_transfer_offered", "unmet_need_left_open",
                     "derivacion_a_telefono", "sin_alternativa", "no_exit", "dead_end", "sin_salida")),
    ("L3-EXPECTED-001", ("transfer_only", "solo_transferencia", "transferencia_por_diseno",
                         "sin_capacidad_de_agenda", "transferencia_inmediata", "escalado_correcto",
                         "escalado_inmediato", "human_requested", "peticion_explicita_de_humano",
                         "transferencia_ofrecida", "transfer_offered", "correct_escalation",
                         "transferencia_solicitada", "no_agendable", "transferencia_correcta",
                         "transferencia_directa")),
]

#: Un veredicto sin resolver y sin señal que encaje no se fuerza a un código.
UNMAPPED = "residuo_l3"


def code_for(verdict: dict) -> Optional[str]:
    """El código que le toca a este veredicto, o None si ninguno encaja."""
    signals = set(verdict.get("senales") or [])
    resolved, blame = verdict.get("resolvio"), verdict.get("de_quien")
    for code, signal, want_resolved, want_blame in RULES:
        if signal not in signals:
            continue
        if want_resolved is not None and resolved != want_resolved:
            continue
        if want_blame is not None and blame != want_blame:
            continue
        return code
    # Puente para los veredictos del vocabulario abierto, que no traen señales
    # de la lista cerrada. Se borra cuando no queden.
    for code, needles in LEGACY_FAMILIES:
        if any(needle in signal for signal in signals for needle in needles):
            return code
    return None


def detect(bundles: List[dict], verdicts: List[dict],
           judgeable: Optional[set] = None) -> tuple:
    """`(hallazgos, residuo)` a partir de los veredictos.

    `judgeable` son los `call_id` que llegaron a L3 según la puerta del DAG. Si
    se pasa, un veredicto de una llamada que falló antes se descarta: la puerta
    dice que L3 no se evalúa ahí, y emitirlo sería medir semántica sobre una
    llamada cuya máquina no funcionó.
    """
    by_call = {b["call_id"]: b for b in bundles}
    findings: List[dict] = []
    residue: List[dict] = []

    for verdict in verdicts:
        call_id = verdict.get("call_id")
        bundle = by_call.get(call_id)
        if not bundle:
            continue
        if judgeable is not None and call_id not in judgeable:
            continue

        code = code_for(verdict)
        resolved = verdict.get("resolvio")
        blame = verdict.get("de_quien")

        if code is None:
            # Solo es residuo si además salió mal: un «sí» sin señal conocida es
            # una llamada que fue bien, y eso no hay que catalogarlo.
            if resolved in ("no", "parcial"):
                residue.append({
                    "call_id": call_id,
                    "tenant": bundle.get("tenant"),
                    "resolvio": resolved,
                    "de_quien": blame,
                    "que_paso": verdict.get("que_paso"),
                    "senales": verdict.get("senales"),
                })
            continue

        # `L3-EXPECTED-001` solo aplica cuando el juez dice que salió bien. Si
        # transfirió por diseño y AUN ASÍ no resolvió, lo que hay es otra cosa.
        if code == "L3-EXPECTED-001" and resolved != "si":
            if resolved in ("no", "parcial"):
                residue.append({
                    "call_id": call_id,
                    "tenant": bundle.get("tenant"),
                    "resolvio": resolved,
                    "de_quien": blame,
                    "que_paso": verdict.get("que_paso"),
                    "senales": verdict.get("senales"),
                    "nota": "transferencia por diseño pero sin resolver",
                })
            continue

        # La confianza del juez modula la severidad, no el veredicto: un caso
        # que él mismo marca dudoso no debe pesar como uno claro.
        severity = None
        if verdict.get("confianza") == "baja":
            severity = "low" if code != "L3-SAFETY-001" else None

        findings.append(
            finding(
                bundle,
                code,
                evidence={
                    "resolvio": resolved,
                    "de_quien": blame,
                    "que_paso": verdict.get("que_paso"),
                    "cita": verdict.get("cita"),
                    "senales": verdict.get("senales"),
                    "confianza": verdict.get("confianza"),
                },
                severity=severity,
                detected_by="judge_l3",
            )
        )
    return findings, residue


def main() -> int:
    import json
    from collections import Counter

    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    bundles = json.load(open(sys.argv[1]))["bundles"]
    verdicts = json.load(open(sys.argv[2]))["verdicts"]
    findings, residue = detect(bundles, verdicts)
    print(f"  {len(verdicts)} veredictos → {len(findings)} hallazgos L3, "
          f"{len(residue)} de residuo")
    for code, n in Counter(f["code"] for f in findings).most_common():
        calls = len({f["conversation_id"] for f in findings if f["code"] == code})
        print(f"    {code:18} n={n:<4} llamadas={calls}")
    if residue:
        print(f"\n  residuo de L3 ({len(residue)}): sin resolver y sin señal conocida")
        for item in residue[:8]:
            print(f"    {item['call_id'][:8]} {(item['tenant'] or '?')[:22]:24} "
                  f"{item['resolvio']}/{item['de_quien']} · {str(item['senales'])[:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
