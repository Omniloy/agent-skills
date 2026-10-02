#!/usr/bin/env python3
"""L2 (state and execution) detectors over call bundles — deterministic, cheap.

Assertions over the event trace: tool outcomes, the results the runtime itself
recorded, and loops the telemetry can see. What is NOT here, and why, is at the
bottom: the SINA error codes (`EXT-PAP-*`) never reach the database — they only
exist as free text in maria-core's logs — and the flow-transition check has no
data in production because no prod tenant runs a workflow.

Usage:
    python3 detect_l2.py bundles.json
"""
from __future__ import annotations

import sys
from typing import Callable, Dict, List, Optional

from _findings import finding, load_bundles, summarize

# Four consecutive calls to one tool. Measured on 24 h of prod: 17 calls, all
# but two of them `login_and_onboard` — a patient failing to identify over and
# over. Three is still ordinary retrying.
TOOL_LOOP_RUN = 4

# `out_of_hours` is deliberately absent: not transferring outside opening hours
# is correct behaviour, not a failure.
TRANSFER_FAILURE_CODES = {
    "sip_error": "L2-ESCALATION-001",
    "participant_absent": "L2-ESCALATION-002",
}


#: Causas de fallo de tool que tienen código propio porque son arreglables de
#: una vez para todas, en vez de caer en el paraguas `L2-TOOL-001`.
TOOL_ERROR_CAUSES = (
    ("Invalid user id format", "L2-TOOL-003"),
)


def tool_error(bundle: dict) -> List[dict]:
    failed = [t for t in bundle["tools"] if t["status"] == "error"]
    if not failed:
        return []
    findings = []
    for tool in failed:
        recovered = any(
            t["name"] == tool["name"] and t["status"] == "success" and t["at"] > tool["at"]
            for t in bundle["tools"]
        )
        text = tool["error"] or ""
        code = next((c for needle, c in TOOL_ERROR_CAUSES if needle in text), "L2-TOOL-001")
        findings.append(
            finding(
                bundle,
                code,
                evidence={
                    "tool": tool["name"],
                    "at": tool["at"],
                    # Truncated hard: the error text is patient-facing guidance,
                    # sometimes hundreds of words of it.
                    "error": (tool["error"] or "")[:240] or None,
                    "recovered_later": recovered,
                },
                severity="medium" if recovered else "high",
            )
        )
    return findings


def tool_loop(bundle: dict) -> List[dict]:
    findings = []
    run, previous = 0, None
    for tool in bundle["tools"]:
        run = run + 1 if tool["name"] == previous else 1
        previous = tool["name"]
        if run == TOOL_LOOP_RUN:
            findings.append(
                finding(
                    bundle,
                    "L2-TOOL-002",
                    evidence={
                        "tool": tool["name"],
                        "consecutive_calls": run,
                        "total_calls_of_tool": sum(
                            1 for t in bundle["tools"] if t["name"] == tool["name"]
                        ),
                        "call_result": bundle["call_result"],
                    },
                    # A loop that ended in failure cost the patient the call.
                    severity="high"
                    if bundle["call_result"] in ("auth_failed", "escalation_error")
                    else "medium",
                )
            )
    return findings


def auth_failed(bundle: dict) -> List[dict]:
    if bundle["call_result"] != "auth_failed":
        return []
    logins = [t for t in bundle["tools"] if t["name"] == "login_and_onboard"]
    return [
        finding(
            bundle,
            "L2-AUTH-001",
            evidence={
                "login_attempts": len(logins),
                "status": bundle["status"],
                "patient_turns": bundle["turns"]["patient"],
                "duration_s": bundle["duration_s"],
                "missing": "the EXT-PAP-* reason is only in maria-core's logs",
            },
        )
    ]


def _hangup_delay_s(bundle: dict) -> Optional[float]:
    """Segundos entre el intento de transferencia y el cuelgue del paciente.

    Decide si el cuelgue es causa o consecuencia, que es la diferencia entre
    «se fue y por eso falló» y «falló y por eso se fue». Medido en la ventana
    del 2026-09-01/02: los 15 fallos de transferencia dan entre +4 s y +154 s,
    es decir **siempre consecuencia** — el paciente espera media vuelta en
    silencio y cuelga.
    """
    from datetime import datetime

    def when(stamp: str) -> datetime:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))

    transfers = [t["at"] for t in bundle["tools"] if t["name"] == "transfer_to_human"]
    hangups = [e["at"] for e in bundle["system_events"] if e["event"] == "patient_hangup"]
    if not transfers or not hangups:
        return None
    return round((when(min(hangups)) - when(max(transfers))).total_seconds(), 1)


def transfer_failed(bundle: dict) -> List[dict]:
    code = TRANSFER_FAILURE_CODES.get(bundle["transfer_failure_reason"] or "")
    if not code:
        return []
    return [
        finding(
            bundle,
            code,
            evidence={
                "transfer_failure_reason": bundle["transfer_failure_reason"],
                "colgo_tras_transferir_s": _hangup_delay_s(bundle),
                "transfer_reason": bundle["transfer_reason"],
                "status": bundle["status"],
                "call_result": bundle["call_result"],
            },
        )
    ]


def escalation_error(bundle: dict) -> List[dict]:
    """`call_result = 'escalation_error'` **sin que otro código ya lo explique**.

    El catálogo dice de este código que «cuando aparece solo, es un escalado que
    se dio por roto sin causa registrada — que es en sí mismo el hallazgo», y
    esa es exactamente la condición que se aplica aquí: si `transfer_failed` ya
    emitió el código de la causa (`sip_error` → `L2-ESCALATION-001` y sus
    refinados, `participant_absent` → `002`), repetirlo cuenta el mismo
    incidente dos veces e infla el recuento de hallazgos del informe.

    `out_of_hours` es la excepción deliberada: no tiene código de causa —no
    transferir fuera del horario del centro es correcto— así que aquí es donde
    aparece, y la KEDB lo oculta como comportamiento normal.
    """
    if bundle["call_result"] != "escalation_error":
        return []
    if TRANSFER_FAILURE_CODES.get(bundle["transfer_failure_reason"] or ""):
        return []
    return [
        finding(
            bundle,
            "L2-ESCALATION-003",
            evidence={
                "status": bundle["status"],
                "transfer_reason": bundle["transfer_reason"],
                "transfer_failure_reason": bundle["transfer_failure_reason"],
                "cause_recorded": bool(bundle["transfer_failure_reason"]),
            },
            # No recorded cause is worse than a recorded one: nothing to route.
            severity="high" if not bundle["transfer_failure_reason"] else "medium",
        )
    ]


DETECTORS: Dict[str, Callable[[dict], List[dict]]] = {
    "tool_error": tool_error,
    "tool_loop": tool_loop,
    "auth_failed": auth_failed,
    "transfer_failed": transfer_failed,
    "escalation_error": escalation_error,
}

UNAVAILABLE = {
    "sina_error_codes": (
        "no EXT-PAP-* / SINA-* code appears in `call_timeline.tool_output` "
        "(checked over 24 h of prod): they are free text in maria-core's logs. "
        "Needs fetch_logs.py, or the structured field of workplan §5-P1"
    ),
    "invalid_transition": (
        "L2-FLOW-001 has no data: every prod tenant is voice_mode='standard' "
        "and call_workflow_transitions is empty, so there is no node path"
    ),
    "node_loop": "same reason: needs the node path",
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
    print(f"== L2 over {payload['meta']['env']} "
          f"{payload['meta']['since']} → {payload['meta']['until']} ==")
    summarize(findings, len(payload["bundles"]))
    for name, why in UNAVAILABLE.items():
        print(f"  not evaluated: {name} — {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
