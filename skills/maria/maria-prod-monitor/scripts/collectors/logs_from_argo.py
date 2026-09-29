#!/usr/bin/env python3
"""Adaptador: logs descargados de Argo → el payload que espera `latency_report.py`.

`fetch_logs.py` saca las líneas de Log Analytics, al que esta cuenta no tiene
acceso en producción. Pero el parser de ese módulo (`parse_line`, `attribute`,
`PARSERS`) no depende de la fuente: solo necesita filas con la forma de Log
Analytics. Así que en vez de duplicar los patrones —que es como se desincronizan
dos parsers— se reusan tal cual, y aquí solo se traduce la fuente.

Con esto, el informe de latencia del workplan (§2.5: percentiles y el reparto
`e2e − tools`) funciona sobre producción usando lo que Argo sí sirve.

Uso:
    python3 ../argo/argo_logs.py --app mariavoice-prd --out /tmp/logs/voice
    python3 logs_from_argo.py /tmp/logs/voice/*.log --out /tmp/logs.json
    python3 latency_report.py /tmp/logs.json
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Dict, List

import fetch_logs

#: Los ficheros de `argo_logs.py` traen `<timestamp> <contenido>` por línea, y el
#: contenido de maria-voice es a su vez el JSON con el envelope (`room`,
#: `job_id`, `pid`, `message`).
STAMP_LEN = 23


def rows_from(paths: List[Path], container: str = "maria-voice") -> List[dict]:
    rows = []
    for path in paths:
        pod = path.stem
        with path.open(errors="replace") as handle:
            for raw in handle:
                line = raw.rstrip("\n")
                if not line:
                    continue
                stamp, _, content = line.partition(" ")
                rows.append(
                    {
                        # `parse_line` prefiere el timestamp del envelope cuando
                        # existe; este es el del contenedor y sirve de respaldo.
                        "TimeGenerated": stamp[:STAMP_LEN],
                        "PodName": pod,
                        "ContainerName": container,
                        "LogMessage": content,
                    }
                )
    return rows


def collect(paths: List[Path], env: str = "prod") -> dict:
    rows = rows_from(paths)
    records = [fetch_logs.parse_line(row) for row in rows]
    # El timestamp del envelope es el bueno: el del contenedor puede ir unos ms
    # por detrás, y los turnos se ordenan por tiempo.
    for record in records:
        envelope = record.get("envelope") or {}
        if envelope.get("timestamp"):
            record["at"] = envelope["timestamp"]
    fetch_logs.attribute(records)
    stamps = [r["at"] for r in records if r.get("at")]
    return {
        "meta": {
            "env": env,
            "service": "maria-voice",
            "source": "argo",
            "since": min(stamps) if stamps else None,
            "until": max(stamps) if stamps else None,
            "pods": sorted({r.get("pod") for r in records if r.get("pod")}),
            # `latency_report` lo imprime en la cabecera; se rellena aquí para
            # que el payload sea intercambiable con el de `fetch_logs.py`.
            "rows": len(records),
        },
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+")
    parser.add_argument("--out", required=True)
    parser.add_argument("--env", default="prod")
    args = parser.parse_args()

    payload = collect([Path(p) for p in args.logs], args.env)
    records = payload["records"]
    print(f"  {len(records)} líneas · {payload['meta']['since']} → {payload['meta']['until']}")
    kinds = Counter(r["kind"] for r in records)
    print("  por tipo:", dict(kinds.most_common(10)))
    print("  atribución:", dict(Counter(r.get("attribution") for r in records).most_common()))
    with open(args.out, "w") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)
    print(f"  escrito: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
