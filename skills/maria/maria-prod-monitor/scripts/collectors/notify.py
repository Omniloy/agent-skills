#!/usr/bin/env python3
"""El mensaje de Slack de la pasada: resumen, alertas urgentes y el enlace.

Un informe de 600 líneas no se lee todos los días; un mensaje de doce líneas
sí. Esto compone ese mensaje y —mientras no haya acceso a Slack— lo deja en un
fichero con la cabecera del destino, para poder revisar el texto antes de que
lo lea alguien.

Reglas de contenido, que son lo que hace que un aviso diario no se ignore:

* **Solo lo urgente va en el mensaje.** Crítico y alto, agrupado por código y
  no por llamada. El resto vive en el artifact.
* **Se dice lo que NO se sabe.** Si una ventana no tiene log que la cubra, o si
  quedan hallazgos sin dueño, va en el mensaje: un resumen que solo cuenta lo
  que salió bien enseña a no leerlo.
* **Nada de emoji por sección ni de exclamaciones.** El aviso se manda a diario
  y tiene que envejecer bien.

Uso:
    python3 notify.py findings.json --artifact https://... --out /tmp/slack.txt
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from typing import Dict, List, Optional

from _findings import catalog, is_actionable, severity_rank
from report import apply_reviews, kedb_match, load_kedb, load_reviews, load_tasks, task_for

JIRA = "https://omniloy.atlassian.net/browse/"


def compose(findings_payload: dict, reviews: Dict[str, dict], kedb: List[dict],
            tasks: Dict[str, List[dict]], artifact_url: Optional[str],
            l3: Optional[dict] = None) -> str:
    meta = findings_payload["meta"]
    results = findings_payload["results"]
    funnel = findings_payload["funnel"]
    every = [f for r in results for f in r["findings"]]
    if reviews:
        every, _ = apply_reviews(every, reviews)

    shown, hidden = [], 0
    for finding in every:
        entry = kedb_match(finding, kedb)
        if entry and entry.get("decision") in ("normal", "known_bug", "deferred"):
            hidden += 1
            continue
        if entry and entry.get("decision") == "watch":
            finding["_watch"] = entry["id"]
        shown.append(finding)

    actionable = [f for f in shown if is_actionable(f)]
    urgent: Dict[str, List[dict]] = defaultdict(list)
    for finding in actionable:
        if finding["severity"] in ("critical", "high"):
            urgent[finding["code"]].append(finding)

    orphans: Dict[str, List[dict]] = defaultdict(list)
    for finding in actionable:
        if not task_for(finding, tasks) and not finding.get("_watch"):
            orphans[finding["code"]].append(finding)

    total = funnel.get("ALL", {})
    calls = total.get("calls", len(results))
    valid = calls - total.get("invalid", 0)
    l1 = total.get("passed_l1", 0)
    l2 = total.get("passed_l2", 0)
    pct = lambda n: f"{100.0 * n / valid:.0f}%" if valid else "—"  # noqa: E731

    lines: List[str] = []
    add = lines.append

    add(f"*Parte de producción · {meta['env']}*")
    add(f"{meta['since'][:16].replace('T', ' ')} → {meta['until'][:16].replace('T', ' ')} UTC · "
        f"{calls} llamadas · pasa L1 {pct(l1)} · pasa L2 {pct(l2)}")
    add("")

    if urgent:
        add(f"*Urgente* — {sum(len(v) for v in urgent.values())} hallazgos en {len(urgent)} códigos")
        for code, group in sorted(urgent.items(), key=lambda kv: (-severity_rank(kv[1][0]), -len(kv[1]))):
            entry = catalog().entry(code)
            task = task_for(group[0], tasks)
            tenants = Counter(f.get("tenant") or "?" for f in group)
            where = ", ".join(f"{t} {n}" for t, n in tenants.most_common(2))
            ref = f"<{JIRA}{task['jira']}|{task['jira']}>" if task else "*sin tarea*"
            add(f"• `{code}` {entry['title']} — {len(group)} en {where} · {ref}")
        add("")
    else:
        add("*Urgente* — nada crítico ni alto en esta ventana.")
        add("")

    add(f"*Resumen* — {len(actionable)} accionables, {hidden} ya decididos")
    by_code = Counter(f["code"] for f in actionable)
    for code, n in by_code.most_common(5):
        add(f"  {n:>3}  `{code}`  {catalog().entry(code)['title']}")
    if len(by_code) > 5:
        add(f"       …y {len(by_code) - 5} códigos más en el parte")
    add("")

    if l3 and l3.get("verdicts"):
        verdicts = l3["verdicts"]
        resolved = Counter(v.get("resolvio") for v in verdicts)
        ours = sum(1 for v in verdicts
                   if v.get("resolvio") == "no" and v.get("de_quien") in ("agente", "diseno_del_flujo"))
        add(f"*Semántica* — {len(verdicts)} llamadas leídas por el juez: "
            f"{resolved.get('si', 0)} resueltas, {resolved.get('parcial', 0)} a medias, "
            f"{resolved.get('no', 0)} sin resolver · {ours} sin resolver y nuestras")
        add("")

    # Lo que no se sabe va en el mensaje, no escondido en el informe.
    if orphans:
        add(f"*Sin dueño* — {sum(len(v) for v in orphans.values())} hallazgos en "
            f"{len(orphans)} códigos sin tarea ni decisión: "
            + ", ".join(f"`{c}`" for c in list(orphans)[:6]))
        add("")

    if artifact_url:
        add(f"<{artifact_url}|Ver el parte completo>")
    else:
        add("_(sin enlace: el parte no se ha publicado todavía)_")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("findings")
    parser.add_argument("--review", action="append", default=[])
    parser.add_argument("--l3")
    parser.add_argument("--artifact")
    parser.add_argument("--to", default="DM de Néstor",
                        help="destino; en pruebas, el chat directo y NUNCA un canal común")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    payload = json.load(open(args.findings))
    l3 = json.load(open(args.l3)) if args.l3 else None
    body = compose(payload, load_reviews(args.review), load_kedb(), load_tasks(),
                   args.artifact, l3)

    text = (
        "=" * 72 + "\n"
        f"MENSAJE DE SLACK — SIN ENVIAR (no hay acceso a Slack todavía)\n"
        f"destino: {args.to}\n"
        + "=" * 72 + "\n\n" + body + "\n"
    )
    with open(args.out, "w") as handle:
        handle.write(text)
    print(body)
    print(f"\n  escrito: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
