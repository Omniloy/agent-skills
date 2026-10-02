#!/usr/bin/env python3
"""`L1-LATENCY-002`: latencia de turno medida con el log, no con el timeline.

Por qué existe este módulo aparte: `L1-LATENCY-001` deducía la latencia del
hueco entre intervenciones del `call_timeline`, y ese hueco incluye lo que el
agente tarda en **hablar** (su `occurred_at` se escribe al acabar). Comprobado
contra el log: 17 de 26 huecos coincidían con `E2E time + TTS audio_duration`.
Así que la métrica se toma del log y punto.

Lo que se mide: `E2E time` (último audio del paciente → primer audio del
agente) **menos** los segundos de las tools de ese turno. Es lo que costaría el
turno con un backend instantáneo, y por eso separa lo nuestro de SINA.

Dos exclusiones sin las que el número no significa nada, ambas heredadas de
`latency_report.build_turns`: los turnos que dispara el **manejador de
inactividad** (miden al paciente callado 15 s; el peor turno de prod era uno de
estos, 27,9 s) y la **reproducción de audio**.

El umbral por turno sale de la base medida, no de una opinión: sobre 1.165
turnos limpios de prod del 2026-09-02, p50 2,34 s, p90 4,19 s, p95 4,87 s y
**p99 10,10 s**. Se marca por encima de 10 s, que es el 1,1 % de aquel día: lo
bastante raro para que marcar signifique algo, y lo bastante bajo para que 10 s
de espera al teléfono no pase por normal. No es un SLA — el workplan pide dos
semanas de datos antes de comprometer uno.
"""
from __future__ import annotations

import sys
from typing import Dict, List, Optional

from _findings import finding

#: p99 de la base del 2026-09-02 (1.165 turnos limpios). Ver el docstring.
TURN_CEILING_S = 10.0

#: Referencia para poder decir «esto es peor que la base» en la evidencia.
BASELINE = {"p50": 2.34, "p90": 4.19, "p95": 4.87, "p99": 10.10, "turns": 1165,
            "window": "prod 2026-09-01T17:18 → 2026-09-02T13:29"}


def detect(bundles: List[dict], turns: Optional[List[dict]] = None) -> List[dict]:
    """Un hallazgo por llamada con al menos un turno por encima del techo.

    `turns` es la salida de `latency_report.build_turns` sobre el log de voice.
    Sin log no hay hallazgos: es un hueco declarado, no un cero.
    """
    if not turns:
        return []
    by_room: Dict[str, List[dict]] = {}
    for turn in turns:
        if turn["type"] == "inactivity" or not turn.get("room"):
            continue
        by_room.setdefault(turn["room"], []).append(turn)

    findings: List[dict] = []
    for bundle in bundles:
        room_turns = by_room.get(bundle.get("room_name") or "")
        if not room_turns:
            continue
        slow = [t for t in room_turns if t["e2e_minus_tools_s"] > TURN_CEILING_S]
        if not slow:
            continue
        worst = max(slow, key=lambda t: t["e2e_minus_tools_s"])
        findings.append(
            finding(
                bundle,
                "L1-LATENCY-002",
                evidence={
                    "peor_turno_s": round(worst["e2e_minus_tools_s"], 2),
                    "turnos_lentos": len(slow),
                    "turnos_medidos": len(room_turns),
                    "en_tools_s": round(worst["tool_s"], 2),
                    "eou_s": worst.get("eou_s"),
                    "ttft_s": worst.get("ttft_s"),
                    "ttfb_s": worst.get("ttfb_s"),
                    "tools": worst.get("tools"),
                    # Lo que decide a quién se le atribuye: si el EOU se come el
                    # turno, es el detector de fin de turno y no la inferencia.
                    "cuello": _bottleneck(worst),
                    "base_p99_s": BASELINE["p99"],
                },
                detected_by="voice_log_e2e_minus_tools",
                severity="high" if worst["e2e_minus_tools_s"] > 2 * TURN_CEILING_S else "medium",
            )
        )
    return findings


def _bottleneck(turn: dict) -> str:
    """Cuál de los componentes se lleva el turno, con nombre y no con número."""
    parts = {
        "esperar a que el paciente calle (EOU)": turn.get("eou_s") or 0,
        "inferencia del LLM (ttft)": turn.get("ttft_s") or 0,
        "primer audio del TTS (ttfb)": turn.get("ttfb_s") or 0,
        "ejecución de tools": turn.get("tool_s") or 0,
    }
    worst, seconds = max(parts.items(), key=lambda kv: kv[1])
    if seconds < 0.5 * turn["e2e_s"]:
        return "sin componente dominante: el tiempo no está en EOU, LLM, TTS ni tools"
    return f"{worst} ({seconds:.1f}s de {turn['e2e_s']:.1f}s)"


UNAVAILABLE = {
    "turn_latency_without_log": (
        "sin log de maria-voice no hay latencia medible; `L1-LATENCY-001` la "
        "aparentaba desde la base de datos y por eso está deprecado"
    ),
}


def main() -> int:
    import json

    from latency_report import build_turns
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    bundles = json.load(open(sys.argv[1]))["bundles"]
    turns = build_turns(json.load(open(sys.argv[2]))["records"])
    found = detect(bundles, turns)
    print(f"  {len(found)} llamadas con algún turno por encima de {TURN_CEILING_S:.0f}s")
    for f in sorted(found, key=lambda x: -x["evidence"]["peor_turno_s"]):
        e = f["evidence"]
        print(f"    {f['conversation_id'][:8]} {(f['tenant'] or '')[:24]:26} "
              f"{e['peor_turno_s']:6.1f}s  cuello: {e['cuello']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
