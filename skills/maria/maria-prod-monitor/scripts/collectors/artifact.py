#!/usr/bin/env python3
"""Genera el parte de producción como página HTML para publicar como artifact.

El informe en Markdown sirve para leerlo en una terminal; esto sirve para
mandarle un enlace a alguien. Misma información y mismas reservas —de dónde sale
cada veredicto, qué queda sin dueño— pero ordenada para escanearse: lo urgente
arriba, el detalle debajo, y la severidad codificada en forma además de en
número.

Los colores de severidad son los del **triaje clínico** (rojo / naranja / ámbar
/ verde), que es como se clasifica la urgencia en un hospital español. No es
decoración: es el vocabulario que ya usa quien va a leer esto.

Uso:
    python3 artifact.py findings.json --bundles b.json --review auth.json \
        --l3 l3.json --out /tmp/parte.html
"""
from __future__ import annotations

import argparse
import html
import json
from collections import Counter, defaultdict
from typing import Dict, List, Optional

from _findings import catalog, is_actionable, severity_rank
from report import apply_reviews, kedb_match, load_kedb, load_reviews, load_tasks, task_for

SEV_CLASS = {"critical": "crit", "high": "high", "medium": "med", "low": "low"}
SEV_LABEL = {"critical": "crítica", "high": "alta", "medium": "media", "low": "baja"}


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def call_url(env: str, call_id: str) -> str:
    suffix = {"prod": "", "stg": "-stg", "dev": "-dev"}.get(env, "")
    return f"https://onestopshop{suffix}.api.omniloy.com/apps/maria/calls/{call_id}"


CSS = """
:root{
  --paper:#eef1f4; --surface:#fbfcfd; --sunk:#e4e9ee;
  --ink:#16212e; --ink-soft:#4a5b6e; --ink-faint:#7c8b9c;
  --rule:#cfd7df; --rule-soft:#e1e7ec;
  --signal:#0a6a6a; --signal-soft:#d8ecec;
  --crit:#b3202b; --high:#c2621a; --med:#94720a; --low:#5b6b7c; --ok:#1c7148;
  --crit-bg:#f7e2e3; --high-bg:#f9e8db; --med-bg:#f6eed6; --low-bg:#e8ecf0; --ok-bg:#dcefe4;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --paper:#101821; --surface:#17212c; --sunk:#0c131a;
    --ink:#e6ecf2; --ink-soft:#a9b8c6; --ink-faint:#7a8896;
    --rule:#2a3743; --rule-soft:#202b36;
    --signal:#4fbdbd; --signal-soft:#123030;
    --crit:#f18c93; --high:#e8a469; --med:#d7bc63; --low:#9aa8b6; --ok:#6fc79a;
    --crit-bg:#2e1518; --high-bg:#2c1e12; --med-bg:#292410; --low-bg:#1b232c; --ok-bg:#12271c;
  }
}
:root[data-theme="dark"]{
  --paper:#101821; --surface:#17212c; --sunk:#0c131a;
  --ink:#e6ecf2; --ink-soft:#a9b8c6; --ink-faint:#7a8896;
  --rule:#2a3743; --rule-soft:#202b36;
  --signal:#4fbdbd; --signal-soft:#123030;
  --crit:#f18c93; --high:#e8a469; --med:#d7bc63; --low:#9aa8b6; --ok:#6fc79a;
  --crit-bg:#2e1518; --high-bg:#2c1e12; --med-bg:#292410; --low-bg:#1b232c; --ok-bg:#12271c;
}
*{box-sizing:border-box}
body{
  margin:0; background:var(--paper); color:var(--ink);
  font-family:"Source Serif 4",Georgia,serif; font-size:16.5px; line-height:1.6;
  -webkit-font-smoothing:antialiased;
}
.wrap{display:grid; grid-template-columns:minmax(230px,270px) minmax(0,1fr); gap:0; min-height:100vh}
@media(max-width:900px){.wrap{grid-template-columns:minmax(0,1fr)}}

/* raíl */
.rail{
  background:var(--sunk); border-right:1px solid var(--rule);
  padding:30px 24px 40px; align-self:start; position:sticky; top:0;
}
@media(max-width:900px){.rail{position:static; border-right:0; border-bottom:1px solid var(--rule)}}
.brand{font-family:Archivo,system-ui,sans-serif; font-weight:700; font-size:20px; letter-spacing:-.015em; line-height:1.15; margin:0 0 2px}
.brand span{display:block; color:var(--signal)}
.rail dl{margin:26px 0 0; display:grid; gap:14px}
.rail dt{font-family:Archivo,system-ui,sans-serif; font-size:10.5px; font-weight:600; letter-spacing:.1em; text-transform:uppercase; color:var(--ink-faint)}
.rail dd{margin:2px 0 0; font-family:"JetBrains Mono",ui-monospace,monospace; font-size:12.5px; color:var(--ink-soft); word-break:break-word}
.big{display:flex; gap:20px; margin:24px 0 0; padding:16px 0 0; border-top:1px solid var(--rule)}
.big div{flex:1}
.big b{display:block; font-family:Archivo,system-ui,sans-serif; font-size:30px; font-weight:700; line-height:1; font-variant-numeric:tabular-nums}
.big small{font-family:Archivo,system-ui,sans-serif; font-size:10.5px; letter-spacing:.08em; text-transform:uppercase; color:var(--ink-faint)}

/* cuerpo */
main{padding:44px clamp(20px,4vw,60px) 90px; max-width:1080px}
h1{font-family:Archivo,system-ui,sans-serif; font-size:clamp(28px,3.4vw,40px); font-weight:700; letter-spacing:-.022em; line-height:1.08; margin:0 0 10px; text-wrap:balance}
.sub{color:var(--ink-soft); margin:0 0 40px; max-width:64ch}
h2{font-family:Archivo,system-ui,sans-serif; font-size:13px; font-weight:600; letter-spacing:.13em; text-transform:uppercase; color:var(--signal); margin:52px 0 4px; padding-bottom:8px; border-bottom:1px solid var(--rule)}
h2 .n{color:var(--ink-faint); margin-right:10px; font-variant-numeric:tabular-nums}
h3{font-family:Archivo,system-ui,sans-serif; font-size:17px; font-weight:600; letter-spacing:-.01em; margin:30px 0 6px}
p{margin:0 0 14px; max-width:66ch}
.note{color:var(--ink-soft); font-size:15px}
code,.mono{font-family:"JetBrains Mono",ui-monospace,monospace; font-size:.88em}
code{background:var(--sunk); padding:1px 5px; border-radius:3px}
a{color:var(--signal); text-decoration-thickness:1px; text-underline-offset:2px}

/* alertas: lo único con relieve en la página */
.alert{background:var(--surface); border:1px solid var(--rule); border-left:5px solid var(--crit); padding:18px 22px; margin:0 0 12px; box-shadow:0 1px 2px rgba(22,33,46,.06)}
.alert.high{border-left-color:var(--high)}
.alert h3{margin:0 0 4px}
.alert .who{font-family:Archivo,system-ui,sans-serif; font-size:11px; letter-spacing:.09em; text-transform:uppercase; color:var(--ink-faint)}
.alert p{margin:8px 0 0; font-size:15.5px}

/* tablas */
.scroll{overflow-x:auto; margin:14px 0 0; border:1px solid var(--rule); background:var(--surface)}
table{border-collapse:collapse; width:100%; font-size:14.5px}
th,td{text-align:left; padding:9px 13px; border-bottom:1px solid var(--rule-soft); vertical-align:top}
th{font-family:Archivo,system-ui,sans-serif; font-size:10.5px; font-weight:600; letter-spacing:.09em; text-transform:uppercase; color:var(--ink-faint); background:var(--sunk); white-space:nowrap}
td.num,th.num{text-align:right; font-family:"JetBrains Mono",ui-monospace,monospace; font-variant-numeric:tabular-nums; white-space:nowrap}
tr:last-child td{border-bottom:0}
td .mono{font-size:13px}

/* pastillas */
.pill{display:inline-block; font-family:Archivo,system-ui,sans-serif; font-size:10.5px; font-weight:600; letter-spacing:.06em; text-transform:uppercase; padding:2px 7px; border-radius:2px; white-space:nowrap}
.pill.crit{background:var(--crit-bg); color:var(--crit)}
.pill.high{background:var(--high-bg); color:var(--high)}
.pill.med{background:var(--med-bg); color:var(--med)}
.pill.low{background:var(--low-bg); color:var(--low)}
.pill.ok{background:var(--ok-bg); color:var(--ok)}
.pill.flat{background:var(--sunk); color:var(--ink-soft)}

/* fichas de caso */
.case{border-left:4px solid var(--rule); padding:0 0 0 16px; margin:0 0 20px}
.case.crit{border-left-color:var(--crit)} .case.high{border-left-color:var(--high)}
.case.med{border-left-color:var(--med)} .case.low{border-left-color:var(--low)}
.case .hd{display:flex; flex-wrap:wrap; gap:9px; align-items:baseline}
.case .hd a{font-family:"JetBrains Mono",ui-monospace,monospace; font-size:14px; font-weight:600}
.case .meta{font-family:"JetBrains Mono",ui-monospace,monospace; font-size:12.5px; color:var(--ink-faint)}
.case .why{margin:5px 0 0; font-size:15px; color:var(--ink-soft)}
blockquote{margin:9px 0 0; padding:8px 0 8px 14px; border-left:2px solid var(--signal); color:var(--ink); font-size:15px}
blockquote .sp{font-family:Archivo,system-ui,sans-serif; font-size:10.5px; letter-spacing:.08em; text-transform:uppercase; color:var(--ink-faint); display:block}
.empty{color:var(--ink-soft); font-style:italic}
footer{margin:70px 0 0; padding:18px 0 0; border-top:1px solid var(--rule); color:var(--ink-faint); font-size:13.5px}
:focus-visible{outline:2px solid var(--signal); outline-offset:2px}
@media(prefers-reduced-motion:reduce){*{animation:none!important; transition:none!important}}
"""


def build(findings_payload: dict, bundles_payload: Optional[dict], reviews: Dict[str, dict],
          l3: Optional[dict], kedb: List[dict], tasks: Dict[str, List[dict]]) -> str:
    meta = findings_payload["meta"]
    env = meta["env"]
    results = findings_payload["results"]
    funnel = findings_payload["funnel"]
    bundles = {b["call_id"]: b for b in (bundles_payload or {}).get("bundles", [])}
    every = [f for r in results for f in r["findings"]]
    if reviews:
        every, _ = apply_reviews(every, reviews)

    hidden, shown, watched = [], [], {}
    for finding in every:
        entry = kedb_match(finding, kedb)
        if entry and entry.get("decision") in ("normal", "known_bug", "deferred"):
            hidden.append(finding)
            continue
        if entry and entry.get("decision") == "watch":
            finding["_watch"] = entry["id"]
            watched[entry["id"]] = entry
        shown.append(finding)

    actionable = [f for f in shown if is_actionable(f)]
    orphans: Dict[str, List[dict]] = {}
    for finding in actionable:
        if not task_for(finding, tasks) and not finding.get("_watch"):
            orphans.setdefault(finding["code"], []).append(finding)

    out: List[str] = []
    add = out.append
    add('<title>Parte de producción de María</title>')
    add('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
        'family=Archivo:wght@400;600;700&family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;1,8..60,400'
        '&family=JetBrains+Mono:wght@400;600&display=swap">')
    add(f"<style>{CSS}</style>")
    add('<div class="wrap">')

    # ── raíl ────────────────────────────────────────────────────────────
    per_tenant = funnel.get("ALL", {})
    calls = per_tenant.get("calls", len(results))
    add('<aside class="rail">')
    add('<p class="brand">Parte de<span>producción</span></p>')
    add('<dl>')
    add(f'<dt>Entorno</dt><dd>{esc(env)}</dd>')
    add(f'<dt>Ventana</dt><dd>{esc(meta["since"])}<br>→ {esc(meta["until"])}</dd>')
    add(f'<dt>Catálogo</dt><dd>v{esc(catalog().version)}</dd>')
    add('</dl>')
    add('<div class="big">')
    add(f'<div><b>{calls}</b><small>llamadas</small></div>')
    add(f'<div><b>{len(actionable)}</b><small>accionables</small></div>')
    add('</div>')
    add('<div class="big">')
    add(f'<div><b>{len(hidden)}</b><small>ya decididos</small></div>')
    add(f'<div><b>{sum(len(v) for v in orphans.values())}</b><small>sin dueño</small></div>')
    add('</div>')
    add('</aside>')

    add('<main>')
    add('<h1>Qué le pasó a María en producción</h1>')
    add(f'<p class="sub">Cada llamada de la ventana pasa por tres capas —transporte y voz, '
        f'estado y ejecución, semántica— y un fallo abajo invalida la medición de arriba. '
        f'Lo que sigue son los {len(actionable)} hallazgos que quedan por atender, con la '
        f'evidencia que los sostiene y de dónde sale.</p>')

    # ── 1. urgente ─────────────────────────────────────────────────────
    add('<h2><span class="n">01</span>Lo urgente</h2>')
    urgent = sorted(
        [f for f in actionable if f["severity"] in ("critical", "high")],
        key=lambda f: (-severity_rank(f), f["code"]),
    )
    by_code_urgent: Dict[str, List[dict]] = defaultdict(list)
    for finding in urgent:
        by_code_urgent[finding["code"]].append(finding)
    if not by_code_urgent:
        add('<p class="empty">Nada crítico ni alto en esta ventana.</p>')
    for code, group in sorted(by_code_urgent.items(), key=lambda kv: -len(kv[1])):
        entry = catalog().entry(code)
        sev = SEV_CLASS[group[0]["severity"]]
        task = task_for(group[0], tasks)
        tenants = Counter(f.get("tenant") or "?" for f in group)
        add(f'<div class="alert {esc(sev)}">')
        add(f'<p class="who"><span class="pill {esc(sev)}">{esc(SEV_LABEL[group[0]["severity"]])}</span> '
            f'<code>{esc(code)}</code> · {len(group)} en {len({f["conversation_id"] for f in group})} llamadas · '
            f'dueño {esc(entry["owner"])}'
            + (f' · <a href="https://omniloy.atlassian.net/browse/{esc(task["jira"])}">{esc(task["jira"])}</a>'
               if task else ' · <b>sin tarea</b>') + '</p>')
        add(f'<h3>{esc(entry["title"])}</h3>')
        add(f'<p>{esc(", ".join(f"{t} {n}" for t, n in tenants.most_common(4)))}</p>')
        add('</div>')

    # ── 2. embudo ──────────────────────────────────────────────────────
    add('<h2><span class="n">02</span>Embudo por capas</h2>')
    add('<p class="note">La calidad semántica solo se mide sobre el universo válido. '
        'Una llamada que falla en L1 no dice nada del agente, y contarla como fallo '
        'semántico es el error de atribución que este orden existe para evitar.</p>')
    add('<div class="scroll"><table><thead><tr><th>Cliente</th>'
        '<th class="num">Llamadas</th><th class="num">Pasa L1</th><th class="num">Pasa L2</th>'
        '<th class="num">Inválidas</th></tr></thead><tbody>')
    for name in ["ALL"] + sorted(k for k in funnel if k != "ALL"):
        row = funnel[name]
        total = row.get("calls", 0)
        valid = total - row.get("invalid", 0)
        pct = lambda n: f"{100.0 * n / valid:.0f}%" if valid else "—"  # noqa: E731
        label = "<b>Todos</b>" if name == "ALL" else esc(name)
        add(f'<tr><td>{label}</td><td class="num">{total}</td>'
            f'<td class="num">{row.get("passed_l1", 0)} · {pct(row.get("passed_l1", 0))}</td>'
            f'<td class="num">{row.get("passed_l2", 0)} · {pct(row.get("passed_l2", 0))}</td>'
            f'<td class="num">{row.get("invalid", 0)}</td></tr>')
    add('</tbody></table></div>')

    # ── 3. códigos ─────────────────────────────────────────────────────
    add('<h2><span class="n">03</span>Todo lo detectado</h2>')
    by_code: Dict[str, List[dict]] = defaultdict(list)
    for finding in shown:
        by_code[finding["code"]].append(finding)
    add('<div class="scroll"><table><thead><tr><th>Código</th><th>Qué es</th>'
        '<th class="num">N</th><th>Severidad</th><th>Dueño</th><th>Estado</th>'
        '</tr></thead><tbody>')
    for code, group in sorted(by_code.items(), key=lambda kv: (-severity_rank(kv[1][0]), -len(kv[1]))):
        entry = catalog().entry(code)
        sev = SEV_CLASS[group[0]["severity"]]
        task = task_for(group[0], tasks)
        if not is_actionable(group[0]):
            state = f'<span class="pill ok">{esc(group[0]["verdict"].lower())}</span>'
        elif task:
            state = f'<a href="https://omniloy.atlassian.net/browse/{esc(task["jira"])}">{esc(task["jira"])}</a>'
        elif group[0].get("_watch"):
            state = '<span class="pill flat">anotado</span>'
        else:
            state = '<span class="pill crit">sin dueño</span>'
        add(f'<tr><td><code>{esc(code)}</code></td><td>{esc(entry["title"])}</td>'
            f'<td class="num">{len(group)}</td>'
            f'<td><span class="pill {esc(sev)}">{esc(SEV_LABEL[group[0]["severity"]])}</span></td>'
            f'<td class="mono">{esc(entry["owner"])}</td><td>{state}</td></tr>')
    add('</tbody></table></div>')

    # ── 4. juez semántico ──────────────────────────────────────────────
    verdicts = (l3 or {}).get("verdicts", [])
    add('<h2><span class="n">04</span>La capa semántica</h2>')
    if not verdicts:
        add('<p class="empty">Sin veredictos del juez en esta pasada.</p>')
    else:
        resolved = Counter(v.get("resolvio") for v in verdicts)
        blame = Counter(v.get("de_quien") for v in verdicts)
        add(f'<p class="note">{len(verdicts)} llamadas leídas: las que el pipeline no pudo '
            'explicar y una muestra de las que consideró sanas — sin ese suelo, un punto '
            'ciego de los detectores se vuelve permanente.</p>')
        add('<div class="scroll"><table><thead><tr><th>¿Resolvió?</th><th class="num">N</th>'
            '<th>¿De quién fue?</th><th class="num">N</th></tr></thead><tbody>')
        rows = max(len(resolved), len(blame))
        r_items, b_items = resolved.most_common(), blame.most_common()
        for i in range(rows):
            r = f'<td>{esc(r_items[i][0])}</td><td class="num">{r_items[i][1]}</td>' if i < len(r_items) else '<td></td><td></td>'
            b = f'<td>{esc(b_items[i][0])}</td><td class="num">{b_items[i][1]}</td>' if i < len(b_items) else '<td></td><td></td>'
            add(f'<tr>{r}{b}</tr>')
        add('</tbody></table></div>')

        signals = Counter(s for v in verdicts for s in (v.get("senales") or []))
        if signals:
            add('<h3>Patrones que más se repiten</h3>')
            add('<p class="note">De aquí salen los códigos de L3: una señal que aparece en '
                'varias llamadas distintas es un candidato a código; una que aparece una vez, '
                'una anécdota.</p>')
            add('<div class="scroll"><table><thead><tr><th>Señal</th><th class="num">Llamadas</th>'
                '</tr></thead><tbody>')
            for tag, n in signals.most_common(18):
                add(f'<tr><td class="mono">{esc(tag)}</td><td class="num">{n}</td></tr>')
            add('</tbody></table></div>')

        worst = [v for v in verdicts if v.get("resolvio") == "no" and v.get("de_quien") in ("agente", "diseno_del_flujo")]
        if worst:
            add('<h3>Las que no resolvió y son nuestras</h3>')
            for verdict in worst[:12]:
                call_id = verdict.get("call_id", "")
                bundle = bundles.get(call_id, {})
                add('<div class="case high">')
                add('<p class="hd">'
                    f'<a href="{esc(call_url(env, call_id))}">{esc(call_id[:8])}</a>'
                    f'<span class="pill flat">{esc(verdict.get("de_quien"))}</span>'
                    f'<span class="meta">{esc(bundle.get("tenant") or "")} · '
                    f'{esc(bundle.get("user_intent") or "sin intent")} · '
                    f'confianza {esc(verdict.get("confianza"))}</span></p>')
                add(f'<p class="why">{esc(verdict.get("que_paso"))}</p>')
                if verdict.get("cita"):
                    add(f'<blockquote><span class="sp">se dijo</span>{esc(verdict["cita"])}</blockquote>')
                add('</div>')

    # ── 5. sin dueño ───────────────────────────────────────────────────
    add('<h2><span class="n">05</span>Sin tarea ni decisión</h2>')
    if not orphans:
        add('<p class="empty">Ninguno: todo lo accionable tiene tarea abierta o decisión escrita.</p>')
    else:
        add('<p class="note">La pregunta que cierra el triaje. Un hallazgo accionable sin tarea '
            'y sin decisión sale rojo cada día y nadie se hace cargo: o se abre tarea, o se '
            'escribe por qué no.</p>')
        for code, group in sorted(orphans.items(), key=lambda kv: -len(kv[1])):
            entry = catalog().entry(code)
            add('<div class="case crit">')
            add(f'<p class="hd"><code>{esc(code)}</code>'
                f'<span class="pill crit">sin dueño</span>'
                f'<span class="meta">{len(group)} en '
                f'{len({f["conversation_id"] for f in group})} llamadas · {esc(entry["owner"])}</span></p>')
            add(f'<p class="why">{esc(entry["title"])}</p>')
            add('<p class="why mono">' + " ".join(
                f'<a href="{esc(call_url(env, f["conversation_id"]))}">{esc(f["conversation_id"][:8])}</a>'
                for f in group[:8]) + '</p>')
            add('</div>')

    # ── 6. anotaciones y ocultos ───────────────────────────────────────
    add('<h2><span class="n">06</span>Ya decidido</h2>')
    add(f'<p class="note">{len(hidden)} hallazgos no aparecen arriba porque ya hay una decisión '
        'escrita sobre ellos. Ocultar no es resolver: el contador está aquí para que nada '
        'desaparezca en silencio.</p>')
    if watched:
        for entry_id, entry in sorted(watched.items()):
            add('<div class="case low">')
            add(f'<p class="hd"><span class="pill flat">anotado</span>'
                f'<code>{esc(entry["code"])}</code><span class="meta">{esc(entry_id)}</span></p>')
            if entry.get("action"):
                add(f'<p class="why">{esc(entry["action"].strip())}</p>')
            add('</div>')

    add('<footer>Generado por el monitor de producción con taxonomía jerárquica '
        f'(MAR-1504) · catálogo v{esc(catalog().version)} · '
        f'{len(results)} llamadas leídas de {esc(env)}. Los enlaces por llamada abren el '
        'one-stop-shop, que exige estar autenticado.</footer>')
    add('</main></div>')
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("findings")
    parser.add_argument("--bundles")
    parser.add_argument("--review", action="append", default=[])
    parser.add_argument("--l3")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    findings_payload = json.load(open(args.findings))
    bundles_payload = json.load(open(args.bundles)) if args.bundles else None
    l3 = json.load(open(args.l3)) if args.l3 else None
    text = build(findings_payload, bundles_payload, load_reviews(args.review),
                 l3, load_kedb(), load_tasks())
    with open(args.out, "w") as handle:
        handle.write(text)
    print(f"  escrito: {args.out} ({len(text.splitlines())} líneas, {len(text)//1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
