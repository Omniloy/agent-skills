#!/usr/bin/env python3
"""Extrae de los logs de maria-core la verdad de SINA sobre cada identificación.

Por qué: hasta ahora el código de auth se *infería* del transcript (qué dato
volvió a pedir el agente). El log de core dice literalmente qué contestó SINA:

    Restrictive validation failed with error code: EXT-PAP-00008 |
      {"docType":"DNI","docNumber":"***84P","phoneNumber":"***392","company":"HCB"}

Y el significado está fijado en core (`sina-replay/replayErrors.ts`, rama main):

    EXT-PAP-00008  el documento no existe en SINA
    EXT-PAP-00006  documento correcto, fecha de nacimiento incorrecta
    EXT-PAP-00007  documento y fecha correctos, teléfono que no es el de la ficha

La correlación no necesita el `call_id` (core todavía no lo loguea; eso es
core#486): el documento y el teléfono van enmascarados a 3 caracteres, y esos
tres, más el cliente y el minuto, ya identifican un intento entre los ~500 de
un día. Es un puente hasta que F1 esté desplegado, no un sustituto.

Ojo con la ventana: Argo solo sirve el log del pod vivo. Fuera de ese rango no
hay nada que correlacionar, y eso se reporta como tal en lugar de contarse
como «sin error de SINA».
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

# `EXT-PAP-*` según core (main): sina-replay/replayErrors.ts
PROVIDER_CODES = {
    "EXT-PAP-00008": "el documento no existe en SINA",
    "EXT-PAP-00006": "documento correcto, fecha de nacimiento incorrecta",
    "EXT-PAP-00007": "documento y fecha correctos, teléfono distinto al de la ficha",
}

LINE_RE = re.compile(
    r"^(?P<stamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+)\s.*?"
    r"(?P<kind>Attempting restrictive validation with (?P<attempt_doc>\w+) and birthDate offset (?P<offset>-?\d+)h"
    r"|Restrictive validation failed with error code: (?P<code>EXT-PAP-\d+)"
    r"|Error in (?P<op>restrictiveValidation|minimumValidation))"
    r"\s*\|\s*(?P<meta>\{.*\})\s*$"
)
TAIL_RE = re.compile(r"\*{2,}(?P<tail>[\w]+)$")


def _tail(masked: Optional[str]) -> Optional[str]:
    """`***84P` → `84P`. El log enmascara todo menos los últimos caracteres."""
    if not masked:
        return None
    match = TAIL_RE.search(masked)
    return match.group("tail").upper() if match else None


def parse(paths: List[Path]) -> List[dict]:
    events: List[dict] = []
    for path in paths:
        with path.open(errors="replace") as handle:
            for raw in handle:
                match = LINE_RE.match(raw.strip())
                if not match:
                    continue
                try:
                    meta = json.loads(match.group("meta"))
                except json.JSONDecodeError:
                    continue
                events.append(
                    {
                        "at": match.group("stamp"),
                        "code": match.group("code"),
                        "offset_h": int(match.group("offset")) if match.group("offset") else None,
                        "attempt_doc_type": match.group("attempt_doc"),
                        "operation": meta.get("operation") or match.group("op"),
                        "doc_type": meta.get("docType"),
                        "doc_tail": _tail(meta.get("docNumber")),
                        "phone_tail": _tail(meta.get("phoneNumber")),
                        "company": meta.get("company"),
                        "company_id": meta.get("companyId"),
                        "pod": path.stem,
                    }
                )
    events.sort(key=lambda e: e["at"])
    return events


def window_of(events: List[dict]) -> tuple:
    stamps = [e["at"] for e in events]
    return (min(stamps), max(stamps)) if stamps else (None, None)


def _dt(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).replace(tzinfo=timezone.utc)


def annotate(cases: List[dict], events: List[dict], window_s: int = 25) -> dict:
    """Cuelga de cada intento el código que SINA devolvió, si el log lo alcanza.

    Emparejamiento **uno a uno**: tres intentos del mismo documento en 74
    segundos son indistinguibles con una ventana amplia, y repartir el mismo
    código entre los tres inventaría evidencia. Así que cada línea de log se
    consume una sola vez, se asigna al intento más cercano en el tiempo, y la
    ventana es asimétrica —core loguea *después* de que la tool se invoque—.

    Lo que queda sin pareja se marca; no se cuenta como «SINA no dijo nada».
    """
    unused = [e for e in events if e["code"]]
    low, high = window_of(events)
    stats = {"matched": 0, "unmatched": 0, "out_of_window": 0, "codes": {}, "ambiguous": 0}

    pairs = []
    for case in cases:
        phone_tail = (case.get("caller_phone") or "")[-3:].upper()
        for attempt in case.get("attempts", []):
            doc = (attempt.get("document_number") or "").upper()
            pairs.append((_dt(attempt["at"]), case, attempt, doc[-3:] if doc else None, phone_tail))
    pairs.sort(key=lambda p: p[0])

    for when, case, attempt, doc_tail, phone_tail in pairs:
        if low and not (_dt(low) - timedelta(seconds=window_s) <= when
                        <= _dt(high) + timedelta(seconds=window_s)):
            attempt["provider_lookup"] = "fuera_de_la_ventana_de_log"
            stats["out_of_window"] += 1
            continue
        candidates = [
            e for e in unused
            if e["doc_tail"] == doc_tail
            and (e["phone_tail"] in (None, phone_tail))
            and -5 <= (_dt(e["at"]) - when).total_seconds() <= window_s
        ]
        if not candidates:
            attempt["provider_lookup"] = "sin_linea_en_el_log"
            stats["unmatched"] += 1
            continue
        best = min(candidates, key=lambda e: abs((_dt(e["at"]) - when).total_seconds()))
        unused.remove(best)
        attempt["provider_codes"] = [best["code"]]
        attempt["provider_meaning"] = PROVIDER_CODES.get(best["code"], "?")
        attempt["provider_log_at"] = best["at"]
        attempt["provider_lag_s"] = round((_dt(best["at"]) - when).total_seconds(), 2)
        stats["matched"] += 1
        stats["codes"][best["code"]] = stats["codes"].get(best["code"], 0) + 1

    stats["log_window"] = [low, high]
    stats["unclaimed_log_lines"] = len(unused)
    return stats



# -- estado de la dependencia, por cliente ---------------------------------
#
# Lo que decide si un incidente del HIS es nuestro o suyo. Se saca del código
# HTTP con el que responde el despliegue **propio** del cliente, porque cada
# cliente tiene el suyo (host y mecanismo distintos) y compararlos entre sí no
# prueba nada.

TOKEN_FAIL_RE = re.compile(
    r"Failed to obtain Sina (?:token|ticket)\s*\|\s*(?P<meta>\{.*?\})", re.S
)
SYNC_FAIL_RE = re.compile(
    r"Error syncing client (?P<tenant>[^:]+): (?P<reason>[^\n]{0,120})"
)
STATUS_RE = re.compile(r"status code (?P<status>\d{3})")
WEBHOOK_FAIL_RE = re.compile(
    r"Webhook invocation failed after all retries\s*\|\s*(?P<meta>\{.*\})"
)


def _verdict_for(status: Optional[int]) -> Optional[str]:
    """4xx = nuestras credenciales; 5xx / sin respuesta = su servicio."""
    if status is None:
        return None
    if 400 <= status < 500:
        return "credentials"
    if status >= 500:
        return "provider"
    return None


def dependency_verdicts(paths: List[Path]) -> Dict[str, dict]:
    """`{cliente: {verdict, evidence, at, status}}` a partir del log de core.

    Empareja `Error syncing client <X>: …` con el `Failed to obtain Sina token`
    inmediatamente anterior, que es el que trae el `baseUrl` y el código HTTP.
    El fallo del cron es la señal más limpia que hay: corre a diario, autentica
    contra los dos clientes y su log sobrevive días (es un pod terminado, no el
    pod vivo).
    """
    verdicts: Dict[str, dict] = {}
    for path in paths:
        pending_status: Optional[int] = None
        pending_line = ""
        with path.open(errors="replace") as handle:
            for raw in handle:
                line = raw.rstrip("\n")
                token_fail = TOKEN_FAIL_RE.search(line)
                if token_fail:
                    status = STATUS_RE.search(line)
                    pending_status = int(status.group("status")) if status else None
                    pending_line = line[:400]
                    continue
                sync_fail = SYNC_FAIL_RE.search(line)
                if not sync_fail:
                    continue
                tenant = sync_fail.group("tenant").strip()
                reason = sync_fail.group("reason").strip()
                status = STATUS_RE.search(reason)
                if status:                       # el error trae su propio código
                    code, evidence = int(status.group("status")), reason
                elif "token generation failed" in reason and pending_status:
                    code, evidence = pending_status, f"{reason} — {pending_line}"
                else:
                    code, evidence = None, reason
                verdict = _verdict_for(code)
                previous = verdicts.get(tenant)
                # Un 4xx pesa más que un 5xx posterior: si nuestras credenciales
                # no valen, arreglarlas es lo primero y lo único que podemos hacer.
                if previous and previous.get("verdict") == "credentials" and verdict != "credentials":
                    continue
                verdicts[tenant] = {
                    "verdict": verdict,
                    "status": code,
                    "evidence": evidence[:300],
                    "at": line[:23],
                    "pod": path.stem,
                }
    return verdicts


def webhook_failures(paths: List[Path]) -> List[dict]:
    """Webhooks de cliente que agotaron los reintentos, agrupados por URL.

    No dejan rastro en la llamada, así que el resto del monitor es ciego a
    esto: solo se ve aquí.
    """
    from collections import Counter
    seen: Counter = Counter()
    detail: Dict[str, dict] = {}
    for path in paths:
        with path.open(errors="replace") as handle:
            for raw in handle:
                match = WEBHOOK_FAIL_RE.search(raw)
                if not match:
                    continue
                try:
                    meta = json.loads(match.group("meta"))
                except json.JSONDecodeError:
                    continue
                url = meta.get("url") or "?"
                seen[url] += 1
                detail.setdefault(url, {
                    "url": url,
                    "error": meta.get("error"),
                    "retries": meta.get("retries"),
                    "webhook_id": meta.get("webhookId"),
                    "primero": raw[:23],
                })
                detail[url]["ultimo"] = raw[:23]
    return [{**detail[u], "fallos": n} for u, n in seen.most_common()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+", help="ficheros de log de maria-core")
    parser.add_argument("--cases", help="auth_cases_*.json a anotar in-place")
    parser.add_argument("--out")
    args = parser.parse_args()

    paths = [Path(p) for p in args.logs]
    events = parse(paths)

    deps = dependency_verdicts(paths)
    if deps:
        print("  estado de la dependencia por cliente (del log, no inferido):")
        for tenant, info in sorted(deps.items()):
            print(f"    {tenant:<28} {info['verdict'] or 'indeterminado':<12} "
                  f"HTTP {info['status']}  {info['at']}")
    hooks = webhook_failures(paths)
    if hooks:
        print("  webhooks de cliente agotando reintentos:")
        for hook in hooks[:5]:
            print(f"    {hook['fallos']:>4} × {hook['error']}  {hook['url'][:78]}")

    low, high = window_of(events)
    print(f"  {len(events)} eventos de validación en el log ({low} → {high})")
    from collections import Counter
    print("  por código:", dict(Counter(e["code"] for e in events if e["code"])))
    print("  por offset probado:", dict(Counter(e["offset_h"] for e in events if e["offset_h"] is not None)))
    print("  por cliente:", dict(Counter(e["company"] for e in events if e["company"])))

    if args.cases:
        payload = json.load(open(args.cases))
        cases = payload["cases"] if isinstance(payload, dict) else payload
        stats = annotate(cases, events)
        print(f"\n  intentos con código de SINA: {stats['matched']}")
        print(f"  sin línea en el log:          {stats['unmatched']}")
        print(f"  fuera de la ventana del log:  {stats['out_of_window']}")
        print("  códigos asignados:", stats["codes"])
        target = args.out or args.cases
        json.dump(payload, open(target, "w"), ensure_ascii=False, indent=1)
        print(f"  escrito: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
