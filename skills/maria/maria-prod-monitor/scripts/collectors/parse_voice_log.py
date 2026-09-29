#!/usr/bin/env python3
"""Extrae de los logs de maria-voice la CAUSA de una transferencia fallida.

La base de datos solo guarda `sip_error`, que es el síntoma. La pregunta que
importaba —«¿no lo cogieron, estaba ocupado, o falló el protocolo?»— la contesta
el log, y la respuesta cambia la decisión: si nadie contesta, un warm transfer
ayuda; si el destino rechaza la llamada, no.

Medido en prod (2026-09-01/02, ~19 h de log):

    sip status: 410 Gone            15   el destino ya no existe ahí
    sip status: 482 Loop Detected     9   bucle de enrutado: vuelve a nosotros
    sip status: 403 Forbidden         3   el trunk no autoriza ese destino
    participant/room does not exist   4   el paciente ya había colgado

Ninguna es «no contestaron», así que el warm transfer no arregla ninguna.

Correlación con la llamada: exacta y sin heurística, porque el registro trae
`room` y la llamada guarda `room_name` — el mismo valor. Del nombre del room
solo se conservan el slug del cliente y los 3 últimos dígitos del teléfono,
porque el room incluye el número completo y no hace falta copiarlo a un
artefacto para nada.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Dict, List, Optional

# `sip status: <n>: <texto>` dentro del mensaje del TwirpError.
SIP_RE = re.compile(r"sip status: (?P<status>\d{3}): (?P<text>[A-Za-z ]+)")
TWIRP_RE = re.compile(r"TwirpError\(code=(?P<code>[a-z_]+)")
ROOM_RE = re.compile(r"^(?P<slug>.+?)-_\+?(?P<phone>\d+)_")

#: SIP → código del catálogo. El texto del estado es de LiveKit, no nuestro.
SIP_CODES = {
    "410": "L2-ESCALATION-007",
    "482": "L2-ESCALATION-008",
    "403": "L2-ESCALATION-009",
}
#: El paciente ya no estaba: no es un fallo de la transferencia.
GONE_MARKERS = ("participant does not exist", "requested room does not exist")


def transfer_failures(paths: List[Path]) -> List[dict]:
    """Un registro por transferencia fallida, con su causa SIP."""
    out: List[dict] = []
    for path in paths:
        with path.open(errors="replace") as handle:
            for raw in handle:
                if "transferring call to human operator" not in raw:
                    continue
                start = raw.find("{")
                if start < 0:
                    continue
                try:
                    record = json.loads(raw[start:].strip())
                except json.JSONDecodeError:
                    continue
                message = record.get("message", "")
                sip = SIP_RE.search(message)
                twirp = TWIRP_RE.search(message)
                room = record.get("room") or ""
                parsed_room = ROOM_RE.match(room)
                caller_gone = any(marker in message for marker in GONE_MARKERS)
                out.append(
                    {
                        "at": record.get("timestamp"),
                        "sip_status": sip.group("status") if sip else None,
                        "sip_text": sip.group("text").strip() if sip else None,
                        "twirp_code": twirp.group("code") if twirp else None,
                        "code": (
                            "L2-ESCALATION-002" if caller_gone
                            else SIP_CODES.get(sip.group("status") if sip else "", "L2-ESCALATION-001")
                        ),
                        "tenant_slug": parsed_room.group("slug") if parsed_room else None,
                        # Solo la cola del teléfono: basta para emparejar.
                        "phone_tail": parsed_room.group("phone")[-3:] if parsed_room else None,
                        "room": room,
                        "job_id": record.get("job_id"),
                        "pod": path.stem,
                    }
                )
    out.sort(key=lambda r: r["at"] or "")
    return out


TTS_RE = re.compile(r"TTS metrics: ttfb=[\d.]+, duration=[\d.]+, audio_duration=(?P<audio>[\d.]+)")
CALL_LOG_FAIL_RE = re.compile(r"Failed to update call log for call (?P<call_id>[0-9a-f-]{36})")


def spoke_audio(paths: List[Path]) -> Dict[str, float]:
    """`{room: segundos de audio sintetizados}` según el log.

    Es la prueba de que el agente habló, independiente de lo que guarde la
    llamada. `TTS metrics … audio_duration=14.08` significa que el TTS produjo
    14 segundos y se enviaron a la sala.
    """
    out: Dict[str, float] = {}
    for path in paths:
        with path.open(errors="replace") as handle:
            for raw in handle:
                match = TTS_RE.search(raw)
                if not match:
                    continue
                start = raw.find("{")
                if start < 0:
                    continue
                try:
                    room = (json.loads(raw[start:]).get("room") or "")
                except json.JSONDecodeError:
                    continue
                if room:
                    out[room] = out.get(room, 0.0) + float(match.group("audio"))
    return out


def failed_call_log(paths: List[Path]) -> set:
    """Llamadas cuyo registro final no se pudo escribir.

    Esta línea trae el `call_id` **sin enmascarar**, así que la correlación es
    directa. Es el único sitio donde se ve que la telemetría de una llamada
    puede haber quedado incompleta.
    """
    out = set()
    for path in paths:
        with path.open(errors="replace") as handle:
            for raw in handle:
                match = CALL_LOG_FAIL_RE.search(raw)
                if match:
                    out.add(match.group("call_id"))
    return out


def log_span(paths: List[Path]) -> Optional[str]:
    """El timestamp más antiguo que cubre el log, para saber qué NO cubre."""
    earliest = None
    for path in paths:
        with path.open(errors="replace") as handle:
            first = handle.readline()[:23].strip()
        if first and (earliest is None or first < earliest):
            earliest = first
    return earliest


def match_to_calls(failures: List[dict], bundles: List[dict]) -> Dict[str, dict]:
    """`{call_id: causa}`. Une por `room`, que la llamada guarda como `room_name`."""
    by_room: Dict[str, str] = {
        b["room_name"]: b["call_id"] for b in bundles if b.get("room_name")
    }
    matched: Dict[str, dict] = {}
    for failure in failures:
        call_id = by_room.get(failure.get("room") or "")
        if call_id:
            matched[call_id] = failure
    return matched


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+")
    parser.add_argument("--bundles", help="para emparejar con llamadas")
    args = parser.parse_args()

    failures = transfer_failures([Path(p) for p in args.logs])
    from collections import Counter
    print(f"  transferencias fallidas en el log: {len(failures)}")
    for (status, text, code), n in Counter(
        (f["sip_status"], f["sip_text"], f["code"]) for f in failures
    ).most_common():
        print(f"    {n:>3} × SIP {status or '—':<4} {text or '(sin estado SIP)':<16} → {code}")
    print("  por cliente:", dict(Counter(f["tenant_slug"] for f in failures)))

    if args.bundles:
        from _findings import load_bundles
        bundles = load_bundles(args.bundles)["bundles"]
        matched = match_to_calls(failures, bundles)
        print(f"\n  emparejadas con una llamada: {len(matched)} de {len(failures)}")
        for call_id, cause in sorted(matched.items(), key=lambda kv: kv[1]["at"] or ""):
            print(f"    {call_id[:8]}  SIP {cause['sip_status'] or '—':<4} "
                  f"{cause['sip_text'] or cause['twirp_code'] or '—':<22} {cause['code']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
