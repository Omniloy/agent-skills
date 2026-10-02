#!/usr/bin/env python3
"""L1 (transport and voice) detectors over call bundles — deterministic, free.

Everything here is an assertion over what the database already recorded. The
log-side L1 signals (`E2E time`, `unmapped livekit disconnect reason`, EOU /
transcription delays) need `fetch_logs.py` and Log Analytics access; they are
listed at the bottom so the gap is visible rather than implied.

Usage:
    python3 detect_l1.py bundles.json
"""
from __future__ import annotations

import sys
from typing import Callable, Dict, List

from _findings import finding, load_bundles, summarize

# Calibrated against 24 h of prod (2026-08-31): 35 gaps over 534 calls, p50
# 13.3 s, max 37 s. Below 12 s the signal drowns in ordinary turn latency.
SILENCE_GAP_S = 12.0
SILENCE_GAP_BAD_S = 20.0


def startup_stalled(bundle: dict) -> List[dict]:
    """La sesión no llegó a construirse: timeline vacío y nadie aceptó nada.

    Antes caía en `L1-VOICE-001` («el agente no dijo nada»), pero la causa es
    distinta —el arranque se colgó, normalmente esperando al MCP— y por tanto
    también el dueño. La duración mínima evita confundirlo con un cuelgue
    inmediato, donde tampoco hay timeline pero no hubo nada que bloquear.
    """
    if bundle["timeline_rows"] != 0 or bundle.get("acceptance") is not False:
        return []
    if (bundle.get("duration_s") or 0) <= 60:
        return []
    return [
        finding(
            bundle,
            "L1-STARTUP-001",
            evidence={
                "duration_s": bundle["duration_s"],
                "timeline_rows": 0,
                "acceptance": bundle.get("acceptance"),
                "note": "confirmar en el log el hito de `[startup]` donde se quedó",
            },
        )
    ]


def agent_never_spoke(bundle: dict) -> List[dict]:
    if bundle["turns"]["agent"] > 0:
        return []
    if bundle["timeline_rows"] == 0 and bundle.get("acceptance") is False:
        return []  # eso es `L1-STARTUP-001`: no es que no hablara, es que no arrancó
    return [
        finding(
            bundle,
            "L1-VOICE-001",
            evidence={
                "turns": bundle["turns"],
                "duration_s": bundle["duration_s"],
                "timeline_rows": bundle["timeline_rows"],
                "system_events": [e["event"] for e in bundle["system_events"]],
            },
            # A call where nothing was ever said is destroyed, not degraded.
            severity="critical" if bundle["turns"]["patient"] > 0 else "high",
        )
    ]


def caller_never_spoke(bundle: dict) -> List[dict]:
    if bundle["turns"]["patient"] > 0 or bundle["turns"]["agent"] == 0:
        return []
    # ¿Colgó el paciente, o se cayó la línea? El timeline lo dice, y la
    # diferencia importa: lo primero es que no quiso hablar con una máquina
    # (producto), lo segundo podría ser transporte (nuestro).
    hung_up = any(e["event"] == "patient_hangup" for e in bundle["system_events"])
    return [
        finding(
            bundle,
            "L1-VOICE-004" if hung_up else "L1-VOICE-002",
            evidence={
                "duration_s": bundle["duration_s"],
                "agent_turns": bundle["turns"]["agent"],
                "system_events": [e["event"] for e in bundle["system_events"]],
            },
        )
    ]


#: Tools que REPRODUCEN un audio: mientras suena no hay intervenciones que
#: registrar, así que el hueco es el audio, no un silencio. El de protección de
#: datos dura 93,1 s exactos, y aparecía como el peor "silencio" de producción.
PLAYBACK_TOOLS = {
    "play_data_protection_audio",
    "read_data_protection_message",
}


def slow_answer(bundle: dict) -> List[dict]:
    """Huecos largos en el timeline. **No es latencia de turno**, y el código lo
    dice: el `occurred_at` del agente se escribe cuando acaba de hablar, así que
    el hueco incluye su respuesta entera. Para latencia real, `E2E time` del log
    (ver `logs_from_argo.py` + `latency_report.py`).
    """
    gaps = [
        g for g in bundle["silence_gaps"]
        if g["seconds"] >= SILENCE_GAP_S
        and not (set(g.get("tools_inside") or []) & PLAYBACK_TOOLS)
    ]
    if not gaps:
        return []
    worst = max(g["seconds"] for g in gaps)
    return [
        finding(
            bundle,
            "L1-LATENCY-001",
            evidence={
                "gaps": gaps[:5],
                "worst_gap_s": worst,
                "gap_count": len(gaps),
                "note": "timeline timestamps; `E2E time` from the logs is the real metric",
            },
            severity="high" if worst >= SILENCE_GAP_BAD_S or len(gaps) >= 3 else "medium",
        )
    ]


def killed_by_deploy(bundle: dict) -> List[dict]:
    """Timeline sin aceptación: la sesión corrió y luego murió su proceso.

    La contradicción es la huella. `L1-STARTUP-001` es `acceptance=false` con
    timeline VACÍO (nunca arrancó); esto es `acceptance=false` con timeline
    lleno, que solo pasa si el trabajo se re-despachó porque el pod que lo
    sostenía desapareció — un despliegue sin drenar, típicamente.
    """
    if bundle.get("acceptance") is not False or bundle["timeline_rows"] <= 0:
        return []
    return [
        finding(
            bundle,
            "L1-DEPLOY-001",
            evidence={
                "timeline_rows": bundle["timeline_rows"],
                "acceptance": bundle.get("acceptance"),
                "turnos": bundle["turns"],
                "duration_s": bundle["duration_s"],
                "idle_before_end_s": bundle.get("idle_before_end_s"),
                "cerrada_por_el_cron": bundle["janitorial_failure"],
                "nota": "cruzar `started_at` con el arranque de los pods para confirmar",
            },
        )
    ]


def session_died(bundle: dict) -> List[dict]:
    if not bundle["janitorial_failure"]:
        return []
    # Si tiene timeline sin aceptación, la causa es el despliegue y ya la nombra
    # `L1-DEPLOY-001`. Emitir los dos contaría el mismo incidente dos veces.
    if bundle.get("acceptance") is False and bundle["timeline_rows"] > 0:
        return []
    return [
        finding(
            bundle,
            "L1-TRANSPORT-001",
            evidence={
                "status": bundle["status"],
                "idle_before_end_s": bundle["idle_before_end_s"],
                "last_system_events": [e["event"] for e in bundle["system_events"][-3:]],
            },
        )
    ]


DETECTORS: Dict[str, Callable[[dict], List[dict]]] = {
    "startup_stalled": startup_stalled,
    "agent_never_spoke": agent_never_spoke,
    "caller_never_spoke": caller_never_spoke,
    "killed_by_deploy": killed_by_deploy,
    "session_died": session_died,
}

# L1 codes that cannot be decided from the database. Kept here so the runner can
# report them as "not evaluated" instead of letting a clean L1 pass look
# complete (workplan §2.1: disconnect reason, unmapped disconnect, EOU and
# transcription delays all live only in the worker log).
UNAVAILABLE = {
    # `slow_answer` ya no se ejecuta: emitía `L1-LATENCY-001`, que medía el hueco
    # del timeline (nuestra latencia + lo que el agente tarda en hablar). La
    # función se conserva para poder leer informes viejos, pero la latencia se
    # mide con `detect_latency.py` a partir del log.
    "turn_latency_from_db": (
        "la latencia no es medible desde la base de datos; usar detect_latency.py "
        "con el log de maria-voice (L1-LATENCY-002)"
    ),
    "disconnect_reason": "needs fetch_logs.py (Log Analytics): SIP_TRUNK_FAILURE, MEDIA_FAILURE, unmapped reasons",
    "turn_latency": "needs fetch_logs.py: `E2E time`, EOU / transcription delay per turn",
}


def detect(bundle: dict) -> List[dict]:
    findings: List[dict] = []
    for detector in DETECTORS.values():
        findings.extend(detector(bundle))
    return findings


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    payload = load_bundles(sys.argv[1])
    findings = [f for b in payload["bundles"] for f in detect(b)]
    print(f"== L1 over {payload['meta']['env']} "
          f"{payload['meta']['since']} → {payload['meta']['until']} ==")
    summarize(findings, len(payload["bundles"]))
    for name, why in UNAVAILABLE.items():
        print(f"  not evaluated: {name} — {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
