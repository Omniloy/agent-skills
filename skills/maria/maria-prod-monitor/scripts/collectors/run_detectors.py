#!/usr/bin/env python3
"""The deterministic half of the pipeline: the L1 → L2 gate, plus the funnel.

This is the DAG with early exit of `hierarchical_taxonomy.md` §3, not two lists
of findings glued together:

    L1 FAIL     → verdict is that code; L2 and L3 are not evaluated.
    L1 INVALID  → not an agent failure and nothing to judge; L2 still runs,
                  L3 does not. Counted apart from failures, never inside them.
    L2 FAIL     → verdict is that code; L3 is not evaluated.
    otherwise   → the call is a candidate for the semantic judge (L3).

It also picks the queue for that judge, because the point of the gate is a
budget: ~30–60 calls a day, prioritised by result shape, not "every transfer"
(74% of prod calls are transfers, most by design — workplan §3.2).

Usage:
    python3 run_detectors.py bundles.json --out findings.json
"""
from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from typing import Dict, List, Optional

import detect_l1
import detect_l2
import detect_window
from _findings import finding, load_bundles, primary, summarize

# Results whose shape is a failure even when nothing deterministic fired: the
# call ended without doing what the patient called for.
FAILURE_SHAPED_RESULTS = {
    "auth_failed",
    "escalation_error",
    "appt_not_found",
    "no_slots_found",
    "info_unavailable",
    "unknown_call_result",
}

# A transfer the tenant asked for is not a failure. These intents explain one.
JUSTIFIED_TRANSFER_INTENTS = {"human_preference", "received_outbound_call"}

# Tenants for which transferring IS the service today, so a transfer says
# nothing about quality. Measured over 24 h of prod (2026-08-31): San Roque's
# only tools are `transfer_to_human` (125) and `get_node_content` (92), Blue's
# are `transfer_to_human` (137) and `lookup_company_info` (29) — neither
# identifies patients or books anything, and 113 of ~115 transfers each are
# `escalation_success`. HCB and Premium do complete appointments, so for them
# an unexplained transfer is a real question.
TRANSFER_IS_THE_SERVICE = {"San Roque", "Hospital Memorial Publio Cordón"}

# Judge budget for one window (workplan §3.2).
JUDGE_BUDGET = 60
# Part of the budget is reserved for calls nothing flagged. Without a floor the
# flagged list fills the budget and the pipeline stops ever looking at what it
# considers healthy — which is how a detector blind spot becomes permanent.
PASS_SAMPLE_RATE = 0.10
PASS_SAMPLE_FLOOR = 0.20


def judge_reasons(bundle: dict, findings: List[dict]) -> List[str]:
    """Why this call would be worth a judge pass, if anything."""
    reasons = []
    if bundle["call_result"] in FAILURE_SHAPED_RESULTS:
        reasons.append("failure_shaped_result")
    if (
        bundle["status"] == "transferred"
        and bundle["user_intent"] not in JUSTIFIED_TRANSFER_INTENTS
        and bundle["tenant"] not in TRANSFER_IS_THE_SERVICE
    ):
        reasons.append("unexplained_transfer")
    if bundle["turns"]["patient"] and bundle["turns"]["patient"] <= 2:
        # En los clientes donde transferir ES el servicio, una llamada de dos
        # turnos que acaba transferida es el servicio funcionando, no un
        # abandono. Medido con el juez el 2026-09-03: de 77 llamadas leídas, 40
        # eran transferencia por diseño (`L3-EXPECTED-001`), y la mayoría
        # entraban en la cola por este motivo. Sin esta exclusión el juez gasta
        # su presupuesto confirmando lo que ya se sabe.
        service_is_transfer = (
            bundle["tenant"] in TRANSFER_IS_THE_SERVICE
            and bundle["status"] == "transferred"
        )
        if not service_is_transfer:
            reasons.append("early_hangup")
    if any(f["code"] == "L2-TOOL-002" for f in findings):
        reasons.append("loop")
    return reasons


def evaluate(bundle: dict, window: Optional[List[dict]] = None) -> dict:
    """One call through the gate.

    `window` son los hallazgos de ventana (`detect_window`) que le tocan a esta
    llamada. Entran como L1 a propósito: si la dependencia estaba caída, el
    fallo de la tool y el `auth_failed` que vino detrás son consecuencia, no
    hallazgos independientes — y la puerta del DAG los suprime sola. Eso es lo
    que convierte «96 errores de tool + 35 identificaciones fallidas» en «un
    incidente de configuración».
    """
    l1 = detect_l1.detect(bundle) + list(window or [])
    # Un `FAIL` de L1 marcado `blocks_upper: false` (la latencia) es un fallo
    # real que NO invalida L2: la máquina de estados siguió funcionando, y sus
    # fallos —una transferencia rechazada, por ejemplo— siguen siendo medibles.
    blocking_l1 = [
        f for f in l1
        if f["verdict"] == "FAIL" and f.get("blocks_upper", True)
    ]
    invalid = [f for f in l1 if f["verdict"] == "INVALID"]

    l2: List[dict] = []
    if not blocking_l1:
        l2 = detect_l2.detect(bundle)

    findings = l1 + l2
    if blocking_l1:
        stage, verdict = "L1", "FAIL"
    elif l2:
        stage, verdict = "L2", "FAIL"
    elif invalid:
        stage, verdict = "L1", "INVALID"
    else:
        stage, verdict = "L3_pending", "PASS_DETERMINISTIC"

    return {
        "call_id": bundle["call_id"],
        "tenant": bundle["tenant"],
        "stage": stage,
        "verdict": verdict,
        "primary": primary(findings),
        "findings": findings,
        "judge_reasons": judge_reasons(bundle, findings)
        if verdict == "PASS_DETERMINISTIC"
        else [],
    }


def refine_with_sip(results: List[dict], causes: Dict[str, dict]) -> int:
    """Sustituye `L2-ESCALATION-001` por el código del estado SIP concreto.

    El paraguas dice «falló por SIP», que no permite decidir nada. El código
    concreto sí: `L2-ESCALATION-007` (410, el destino ya no está),
    `008` (482, bucle de enrutado), `009` (403, el trunk no autoriza).
    """
    changed = 0
    for result in results:
        cause = causes.get(result["call_id"])
        if not cause or cause["code"] == "L2-ESCALATION-001":
            continue
        for i, f in enumerate(result["findings"]):
            if f["code"] != "L2-ESCALATION-001":
                continue
            result["findings"][i] = finding(
                {"call_id": result["call_id"], "tenant": result["tenant"]},
                cause["code"],
                evidence={
                    "sip_status": cause["sip_status"],
                    "sip_text": cause["sip_text"],
                    "twirp_code": cause["twirp_code"],
                    "at": cause["at"],
                },
                detected_by="voice_log_sip_status",
            )
            changed += 1
        result["primary"] = primary(result["findings"])
    return changed


def fix_unrecorded_speech(results: List[dict], bundles: Dict[str, dict],
                          spoke: Dict[str, float], write_failures: set) -> int:
    """`L1-VOICE-001` → `L1-PERSIST-001` cuando el log demuestra que sí habló.

    «El agente no dijo nada en toda la llamada» se detecta con `turns.agent == 0`,
    que es lo que la llamada **guarda**, no lo que pasó. Si el log tiene audio
    de TTS para esa sala, el agente habló y lo que falla es la persistencia; y si
    además el paciente no habló, la historia real es que escuchó el saludo y
    colgó (`L1-VOICE-004`).
    """
    changed = 0
    for result in results:
        audio = spoke.get(result["call_id"])
        if not audio:
            continue
        bundle = bundles[result["call_id"]]
        kept = [f for f in result["findings"] if f["code"] != "L1-VOICE-001"]
        if len(kept) == len(result["findings"]):
            continue
        stub = {"call_id": result["call_id"], "tenant": result["tenant"]}
        kept.append(
            finding(
                stub,
                "L1-PERSIST-001",
                evidence={
                    "audio_de_agente_s": round(audio, 2),
                    "turnos_agente_en_la_llamada": bundle["turns"]["agent"],
                    "registro_final_fallido": result["call_id"] in write_failures,
                    "nota": "el log tiene `TTS metrics` para esta sala: el saludo se sintetizó y se envió",
                },
                detected_by="voice_log_tts_vs_db",
            )
        )
        if bundle["turns"]["patient"] == 0:
            kept.append(
                finding(
                    stub,
                    "L1-VOICE-004",
                    evidence={
                        "audio_de_agente_s": round(audio, 2),
                        "nota": "escuchó el saludo y colgó; los turnos no se guardaron",
                    },
                    detected_by="voice_log_tts_vs_db",
                )
            )
        result["findings"] = kept
        result["primary"] = primary(kept)
        changed += 1
    return changed


def explain_missing_log(results: List[dict], bundles: Dict[str, dict],
                        covered: set, log_from: Optional[str],
                        code: str, field: str) -> int:
    """Dice POR QUÉ falta la evidencia de log, en vez de dejar el hueco en blanco.

    Argo solo sirve el log de los pods **vivos** y maria-voice autoescala, así
    que un fallo de transferencia puede no tener línea por dos motivos muy
    distintos: la llamada es anterior al log disponible, o el pod que la atendió
    ya no existe. Lo segundo no se arregla esperando, y el informe tiene que
    poder decirlo.
    """
    marked = 0
    for result in results:
        for f in result["findings"]:
            if f["code"] != code or result["call_id"] in covered:
                continue
            started = (bundles[result["call_id"]].get("started_at") or "")[:19]
            if log_from and started < log_from[:19]:
                why = f"anterior al log disponible (empieza {log_from[:19]})"
            else:
                why = "el pod que atendió la llamada ya no existe (maria-voice autoescala)"
            f["evidence"][field] = f"sin línea de log: {why}"
            marked += 1
    return marked


def build_judge_queue(results: List[dict], seed: int = 20260831) -> List[dict]:
    """Flagged calls first, then a sample of the clean ones, up to the budget."""
    flagged = [r for r in results if r["verdict"] == "PASS_DETERMINISTIC" and r["judge_reasons"]]
    clean = [r for r in results if r["verdict"] == "PASS_DETERMINISTIC" and not r["judge_reasons"]]

    rng = random.Random(seed)
    reserved = int(JUDGE_BUDGET * PASS_SAMPLE_FLOOR)
    sample_size = min(
        len(clean),
        max(1, int(len(clean) * PASS_SAMPLE_RATE)),
        max(reserved, JUDGE_BUDGET - len(flagged)),
    )
    sampled = rng.sample(clean, sample_size) if clean and sample_size > 0 else []

    queue = [
        {"call_id": r["call_id"], "tenant": r["tenant"], "reasons": r["judge_reasons"]}
        for r in flagged
    ][: JUDGE_BUDGET - len(sampled)]
    queue += [
        {"call_id": r["call_id"], "tenant": r["tenant"], "reasons": ["random_sample"]}
        for r in sampled
    ]
    return queue


def funnel(results: List[dict]) -> Dict[str, Dict[str, int]]:
    """Calls surviving each gate, overall and per tenant (workplan §2.4)."""
    per: Dict[str, Counter] = defaultdict(Counter)
    for result in results:
        for key in ("ALL", result["tenant"] or "unknown"):
            per[key]["calls"] += 1
            if result["verdict"] == "INVALID":
                per[key]["invalid"] += 1
                continue
            if result["stage"] == "L1":
                continue
            per[key]["passed_l1"] += 1
            if result["stage"] == "L2":
                continue
            per[key]["passed_l2"] += 1
    return {k: dict(v) for k, v in per.items()}


def residue(payload: dict, results: List[dict]) -> List[dict]:
    """Calls that ended badly and that no code explains (pipeline step 6).

    This is the queue that grows the catalog: a result shaped like a failure
    with nothing deterministic to file it under. Cluster it, and anything
    recurring becomes a candidate code (workplan §2.3.6).
    """
    verdict_by_call = {r["call_id"]: r for r in results}
    out = []
    for bundle in payload["bundles"]:
        result = verdict_by_call[bundle["call_id"]]
        if result["primary"] is not None:
            continue
        if bundle["call_result"] in FAILURE_SHAPED_RESULTS:
            out.append(
                {
                    "call_id": bundle["call_id"],
                    "tenant": bundle["tenant"],
                    "call_result": bundle["call_result"],
                    "user_intent": bundle["user_intent"],
                }
            )
    return out


def report(payload: dict, results: List[dict], queue: List[dict]) -> None:
    meta = payload["meta"]
    print(f"== {meta['env']} {meta['since']} → {meta['until']} ==")
    print(f"  calls: {len(results)}")

    print("\n  funnel (valid calls only; INVALID counted apart):")
    rows = funnel(results)
    for name in ["ALL"] + sorted(k for k in rows if k != "ALL"):
        row = rows[name]
        calls = row.get("calls", 0)
        valid = calls - row.get("invalid", 0)
        l1 = row.get("passed_l1", 0)
        l2 = row.get("passed_l2", 0)
        pct = lambda n: f"{100.0 * n / valid:.0f}%" if valid else "-"  # noqa: E731
        print(
            f"    {name:<34} n={calls:<4} invalid={row.get('invalid', 0):<3} "
            f"pass L1 {l1:<4}({pct(l1)})  pass L2 {l2:<4}({pct(l2)})"
        )

    all_findings = [f for r in results for f in r["findings"]]
    print()
    summarize(all_findings, len(results))

    print("\n  filed under (primary code per call):")
    by_primary = Counter(
        (r["primary"] or {}).get("code", "—none—") for r in results
    )
    for code, n in by_primary.most_common():
        print(f"    {code:<20} {n}")

    print(f"\n  judge queue: {len(queue)} of budget {JUDGE_BUDGET}")
    print("    reasons:", dict(Counter(r for q in queue for r in q["reasons"])))
    print("    per tenant:", dict(Counter(q["tenant"] for q in queue)))

    left = residue(payload, results)
    print(f"\n  residue (bad result, no code): {len(left)}")
    print("    by result:", dict(Counter(r["call_result"] for r in left).most_common()))
    print("    by tenant:", dict(Counter(r["tenant"] for r in left).most_common()))

    for module in (detect_l1, detect_l2):
        for name, why in module.UNAVAILABLE.items():
            print(f"  not evaluated: {name} — {why}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundles")
    parser.add_argument("--out", help="write findings + verdicts as JSON here")
    parser.add_argument(
        "--l3", help="veredictos del juez (judge_l3.py collect): añade la capa "
                     "semántica a los hallazgos. Se pasa en una SEGUNDA pasada, "
                     "porque la primera es la que produce la cola que el juez lee.",
    )
    parser.add_argument(
        "--voice-log", nargs="*", default=[],
        help="logs de maria-voice: refinan `L2-ESCALATION-001` con el estado SIP "
             "(410 Gone / 482 Loop Detected / 403 Forbidden)",
    )
    parser.add_argument(
        "--core-log", nargs="*", default=[],
        help="logs de maria-core (argo/argo_logs.py): deciden si un incidente "
             "del HIS es de nuestras credenciales o de su servicio",
    )
    args = parser.parse_args()

    payload = load_bundles(args.bundles)

    # El log solo puede atribuir lo que cubre: Argo sirve el pod vivo (~25 h de
    # maria-core). Fuera de esa ventana el hallazgo sale como `L1-HIS-003`, sin
    # dueño, en vez de adivinarlo.
    deps = {}
    if args.core_log:
        from pathlib import Path

        import parse_core_log
        deps = parse_core_log.dependency_verdicts([Path(p) for p in args.core_log])
        for tenant, info in sorted(deps.items()):
            print(f"  log: {tenant} → {info['verdict']} (HTTP {info['status']}, {info['at']})")
    window = detect_window.detect(payload["bundles"], deps)
    by_call_latency: Dict[str, List[dict]] = {}

    # La base de datos solo sabe `sip_error`. El log de voice dice qué estado SIP
    # fue, y de eso depende la decisión: un rechazo del destino no se arregla con
    # un warm transfer y un «no contestan» sí. Se une por `room`, que la llamada
    # guarda como `room_name`, así que la correlación es exacta.
    sip_causes = {}
    if args.voice_log:
        from pathlib import Path

        import parse_voice_log
        voice_paths = [Path(p) for p in args.voice_log]
        failures = parse_voice_log.transfer_failures(voice_paths)
        sip_causes = parse_voice_log.match_to_calls(failures, payload["bundles"])
        print(f"  log de voice: {len(failures)} transferencias fallidas, "
              f"{len(sip_causes)} dentro de esta ventana")
        # ¿habló el agente? El log lo sabe aunque la llamada diga 0 turnos.
        audio_by_room = parse_voice_log.spoke_audio(voice_paths)
        voice_log_from = parse_voice_log.log_span(voice_paths)
        spoke = {
            b["call_id"]: audio_by_room[b["room_name"]]
            for b in payload["bundles"]
            if b.get("room_name") in audio_by_room
        }
        write_failures = parse_voice_log.failed_call_log(voice_paths)
        # La latencia se mide aquí, con el log, y entra como hallazgo de L1 que
        # NO bloquea (`blocks_upper: false`): un turno lento no invalida nada de
        # lo que la máquina de estados hizo después.
        import detect_latency
        from latency_report import build_turns

        import logs_from_argo
        turns = build_turns(logs_from_argo.collect(voice_paths)["records"])
        latency_findings = detect_latency.detect(payload["bundles"], turns)
        by_call_latency = {}
        for f in latency_findings:
            by_call_latency.setdefault(f["conversation_id"], []).append(f)
        print(f"  log de voice: {len(turns)} turnos medidos · "
              f"{len(latency_findings)} llamadas con un turno por encima de "
              f"{detect_latency.TURN_CEILING_S:.0f}s")
        print(f"  log de voice: audio de agente en {len(spoke)} llamadas · "
              f"registro final fallido en {len(write_failures & {b['call_id'] for b in payload['bundles']})}")
    by_call: Dict[str, List[dict]] = defaultdict(list)
    for f in window:
        by_call[f["conversation_id"]].append(f)
    if window:
        print(f"  ventana: {len(window)} hallazgos de dependencia "
              f"({len(by_call)} llamadas) — entran como L1 y cortan lo de arriba")
    results = [
        evaluate(b, (by_call.get(b["call_id"]) or []) + (by_call_latency.get(b["call_id"]) or []))
        for b in payload["bundles"]
    ]
    refined = refine_with_sip(results, sip_causes)
    if refined:
        print(f"  {refined} transferencias fallidas con su causa SIP del log")
    if args.voice_log:
        by_call = {b["call_id"]: b for b in payload["bundles"]}
        fixed = fix_unrecorded_speech(results, by_call, spoke, write_failures)
        if fixed:
            print(f"  {fixed} llamadas donde el agente SÍ habló y el registro dice 0 turnos")
        explained = explain_missing_log(
            results, by_call, set(sip_causes), voice_log_from,
            "L2-ESCALATION-001", "causa_sip")
        if explained:
            print(f"  {explained} fallos de transferencia sin causa: motivo declarado en el informe")
        # Mismo trato para «el agente no dijo nada»: si el log no llega, la
        # llamada no queda desmentida ni confirmada, y hay que decirlo.
        unverified = explain_missing_log(
            results, by_call, set(spoke), voice_log_from,
            "L1-VOICE-001", "verificacion")
        if unverified:
            print(f"  {unverified} casos de agente mudo sin log que lo verifique")
    if args.l3:
        import detect_l3
        verdicts = json.load(open(args.l3))["verdicts"]
        # La puerta manda también aquí: un veredicto semántico de una llamada
        # que falló en L1 o L2 no se emite, porque medir semántica sobre una
        # llamada cuya máquina no funcionó es el error que este orden evita.
        judgeable = {r["call_id"] for r in results if r["stage"] == "L3_pending"}
        l3_findings, l3_residue = detect_l3.detect(payload["bundles"], verdicts, judgeable)
        by_call_l3: Dict[str, List[dict]] = {}
        for f in l3_findings:
            by_call_l3.setdefault(f["conversation_id"], []).append(f)
        for result in results:
            extra = by_call_l3.get(result["call_id"])
            if not extra:
                continue
            result["findings"].extend(extra)
            result["primary"] = primary(result["findings"])
            if any(f["verdict"] == "FAIL" for f in extra):
                result["stage"], result["verdict"] = "L3", "FAIL"
                result["judge_reasons"] = []
        print(f"  juez: {len(verdicts)} veredictos → {len(l3_findings)} hallazgos L3 "
              f"en {len(by_call_l3)} llamadas · {len(l3_residue)} de residuo semántico")
        if l3_residue:
            print("  residuo de L3 (sin resolver y sin señal conocida): "
                  + ", ".join(r["call_id"][:8] for r in l3_residue[:10]))

    queue = build_judge_queue(results)
    report(payload, results, queue)

    if args.out:
        with open(args.out, "w") as handle:
            json.dump(
                {
                    "meta": {**payload["meta"], "stage": "deterministic"},
                    "funnel": funnel(results),
                    "results": results,
                    "judge_queue": queue,
                    "residue": residue(payload, results),
                },
                handle,
                ensure_ascii=False,
                indent=1,
            )
        print(f"\n  written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
