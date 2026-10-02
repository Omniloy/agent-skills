#!/usr/bin/env python3
"""Descarga y busca en los logs de los pods de un despliegue de Argo.

Es el sustituto *parcial* del Log Analytics de PRD (al que esta cuenta no tiene
permisos): Argo sirve los logs del pod **vivo**, así que hay ventana desde el
último reinicio y nada más. Para un incidente en curso vale; para el histórico
de un día concreto, no.

La API devuelve NDJSON (`{"result":{"content":…,"timeStampStr":…}}`), no texto,
así que aquí se aplana a `<timestamp> <línea>` para poder pasarle grep.

Hallazgo que hace esto útil: el MCP ya loguea `'call_id': '7d***0f'` en cada
petición HTTP. Enmascarado, pero dos caracteres por punta identifican una
llamada entre las ~500 de un día, así que se puede correlacionar log ↔ llamada
sin esperar a que F1 esté desplegado (ver `match_call`).

Solo GET. Nada de sync, restart ni delete.

Uso:
    python3 argo_logs.py --app mariavoice-prd --out ../logs/voice
    python3 argo_logs.py --app mariacore-prd --grep 'EXT-PAP|restrictive'
    python3 argo_logs.py --app mariavoice-prd --call 81f4a854-...
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Iterator, List, Optional

from argo_api import ArgoError, pods_of, resource_tree, token

MASK_RE = re.compile(r"'call_id': '([0-9a-f]{2})\*\*\*([0-9a-f]{2})'")


def flatten(ndjson: str) -> List[str]:
    """NDJSON de Argo → líneas `<timestamp> <contenido>`."""
    out = []
    for raw in ndjson.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            result = json.loads(raw).get("result") or {}
        except json.JSONDecodeError:
            out.append(raw)
            continue
        stamp = (result.get("timeStampStr") or "")[:23]
        out.append(f"{stamp} {result.get('content', '')}")
    return out


def masks_of(call_id: str) -> str:
    """La máscara con la que el MCP loguea este call_id."""
    flat = call_id.replace("-", "")
    return f"'call_id': '{call_id[:2]}***{flat[-2:]}'"


def match_call(line: str, call_id: str) -> bool:
    return masks_of(call_id) in line


def fetch(app: str, *, tail: int, since: Optional[int], containers: Optional[List[str]] = None) -> Iterator[tuple]:
    """(pod, líneas) para cada pod del despliegue."""
    from argo_api import pod_logs

    tok = token()
    pods = pods_of(resource_tree(tok, app))
    for pod in pods:
        for container in containers or [""]:
            try:
                text = pod_logs(tok, app, pod["name"], pod["namespace"], container, tail, since)
            except ArgoError as exc:
                print(f"    ! {pod['name']}{'/' + container if container else ''}: {exc}")
                continue
            yield pod["name"], flatten(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True)
    parser.add_argument("--tail", type=int, default=10000)
    parser.add_argument("--since", type=int, help="segundos hacia atrás")
    parser.add_argument("--grep", help="regex (case-insensitive) a filtrar")
    parser.add_argument("--call", help="filtrar por la máscara de este call_id")
    parser.add_argument("--out", help="directorio donde volcar un fichero por pod")
    parser.add_argument("--context", type=int, default=0, help="líneas de contexto")
    args = parser.parse_args()

    pattern = re.compile(args.grep, re.I) if args.grep else None
    out_dir = Path(args.out) if args.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    total = hits = 0
    span = []
    for pod, lines in fetch(args.app, tail=args.tail, since=args.since):
        total += len(lines)
        if lines:
            span += [lines[0][:19], lines[-1][:19]]
        if out_dir:
            (out_dir / f"{pod}.log").write_text("\n".join(lines))
        selected = []
        for i, line in enumerate(lines):
            keep = (pattern.search(line) if pattern else False) or (
                match_call(line, args.call) if args.call else False
            )
            if keep:
                lo, hi = max(0, i - args.context), min(len(lines), i + args.context + 1)
                selected.extend(range(lo, hi))
        if selected:
            print(f"\n### {pod} ({len(set(selected))} líneas)")
            for i in sorted(set(selected)):
                print("   ", lines[i][:2000])
            hits += len(set(selected))
        elif not pattern and not args.call:
            print(f"    {pod}: {len(lines)} líneas "
                  f"({lines[0][:19] if lines else '-'} → {lines[-1][:19] if lines else '-'})")

    print(f"\n  total {total} líneas" + (f", {hits} coincidencias" if pattern or args.call else ""))
    if span:
        print(f"  ventana disponible: {min(span)} → {max(span)}")
    if out_dir:
        print(f"  volcado en: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
