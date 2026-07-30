#!/usr/bin/env python3
"""prd_issues.py — turn a PRD manifest into (a) a visual HTML plan and
(b) real GitHub Epics + sub-issues + labels + milestones via the `gh` CLI.

Subcommands:
  render  --manifest m.json --out plan.html
  create  --manifest m.json [--repo owner/name] [--dry-run | --apply]
          [--limit-epic KEY]

Stdlib only. `create` requires an authenticated `gh`. See the SKILL.md and
references/manifest.schema.json for the manifest contract.
"""
from __future__ import annotations
import argparse, base64, html, json, mimetypes, os, re, subprocess, sys
from pathlib import Path

# --------------------------------------------------------------------------- #
# Tiny, dependency-free Markdown → HTML (enough for issue bodies / narratives)
# --------------------------------------------------------------------------- #
def _inline(text: str) -> str:
    out = html.escape(text, quote=False)
    out = re.sub(r"`([^`]+)`", lambda m: f"<code>{m.group(1)}</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", out)
    out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
                 lambda m: f'<a href="{html.escape(m.group(2))}" target="_blank" rel="noopener">{m.group(1)}</a>',
                 out)
    out = re.sub(r"(?<![\"\w/=])(https?://[^\s<)]+)",
                 r'<a href="\1" target="_blank" rel="noopener">\1</a>', out)
    return out

def md(text: str | None) -> str:
    if not text:
        return ""
    lines = text.strip("\n").split("\n")
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1; continue
        m = re.match(r"^(#{1,4})\s+(.*)", ln)
        if m:
            lvl = len(m.group(1)); out.append(f"<h{lvl+2}>{_inline(m.group(2))}</h{lvl+2}>"); i += 1; continue
        if re.match(r"^\s*[-*]\s+", ln):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(f"<li>{_inline(re.sub(r'^\\s*[-*]\\s+', '', lines[i]))}</li>"); i += 1
            out.append("<ul>" + "".join(items) + "</ul>"); continue
        if re.match(r"^\s*\d+\.\s+", ln):
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(f"<li>{_inline(re.sub(r'^\\s*\\d+\\.\\s+', '', lines[i]))}</li>"); i += 1
            out.append("<ol>" + "".join(items) + "</ol>"); continue
        para = [ln]; i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,4}\s|\s*[-*]\s|\s*\d+\.\s)", lines[i]):
            para.append(lines[i]); i += 1
        out.append("<p>" + _inline(" ".join(para)) + "</p>")
    return "\n".join(out)

# --------------------------------------------------------------------------- #
# Markdown issue body (deterministic — drives BOTH the HTML and the gh body)
# --------------------------------------------------------------------------- #
def issue_body_md(issue: dict, epic_title: str, epic_number: int | None) -> str:
    parts = []
    if issue.get("functional_md"):
        parts.append("## 🧭 Functional\n\n" + issue["functional_md"].strip())
    if issue.get("acceptance"):
        parts.append("**Acceptance criteria**\n\n" +
                     "\n".join(f"- [ ] {a}" for a in issue["acceptance"]))
    if issue.get("technical_md"):
        parts.append("## 🛠️ Technical\n\n" + issue["technical_md"].strip())
    if issue.get("design_refs"):
        lines = []
        for d in issue["design_refs"]:
            cap = (" — " + d["caption"]) if d.get("caption") else ""
            label = d.get("label") or d.get("src", "design")
            lines.append(f"- [{label}]({d['src']}){cap}")
        note = issue.get("design_note") or "Build to the mockup(s) below."
        parts.append("## 🎨 Design reference\n\n" + note + "\n\n" + "\n".join(lines))
    meta = []
    if issue.get("prd_refs"): meta.append("PRD: " + ", ".join(issue["prd_refs"]))
    if epic_number:           meta.append(f"Epic: #{epic_number}")
    else:                     meta.append(f"Epic: {epic_title}")
    if issue.get("estimate"): meta.append("Estimate: " + str(issue["estimate"]))
    parts.append("---\n" + " · ".join(meta))
    return "\n\n".join(parts)

def epic_body_md(epic: dict, child_lines: list[str]) -> str:
    parts = [epic.get("summary_md", "").strip()]
    if epic.get("prd_refs"):
        parts.append("**PRD:** " + ", ".join(epic["prd_refs"]))
    parts.append("### Sub-issues\n\n" + ("\n".join(child_lines) if child_lines else "_(none yet)_"))
    return "\n\n".join(p for p in parts if p)

# --------------------------------------------------------------------------- #
# HTML rendering
# --------------------------------------------------------------------------- #
def _text_on(hex_color: str) -> str:
    c = hex_color.lstrip("#")
    try:
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except Exception:
        return "#fff"
    return "#000" if (0.299*r + 0.587*g + 0.114*b) > 150 else "#fff"

def chip(label: str, color_map: dict) -> str:
    color = color_map.get(label, "8b949e").lstrip("#")
    return (f'<span class="chip" style="background:#{color};color:{_text_on(color)}">'
            f'{html.escape(label)}</span>')

def render_block(b: dict, manifest_dir: Path) -> str:
    t = b.get("type")
    title = f'<div class="block-title">{html.escape(b["title"])}</div>' if b.get("title") else ""
    cap = f'<div class="cap">{_inline(b["caption"])}</div>' if b.get("caption") else ""
    if t == "callout":
        tone = b.get("tone", "info")
        return f'<div class="callout {tone}">{title}{md(b.get("body_md",""))}</div>'
    if t == "diagram":
        return f'<div class="block">{title}<div class="diagram">{b.get("html","")}</div>{cap}</div>'
    if t == "mermaid":
        src = html.escape(b.get("source", ""))
        return f'<div class="block">{title}<pre class="mermaid">{src}</pre>{cap}</div>'
    if t == "image":
        src = b.get("src", ""); p = (manifest_dir / src) if not src.startswith(("http", "data:")) else None
        if p and p.exists():
            mime = mimetypes.guess_type(str(p))[0] or "image/png"
            data = base64.b64encode(p.read_bytes()).decode()
            src = f"data:{mime};base64,{data}"
        return f'<div class="block">{title}<img class="img" src="{html.escape(src)}" alt="{html.escape(b.get("title",""))}">{cap}</div>'
    if t == "table":
        cols = "".join(f"<th>{html.escape(c)}</th>" for c in b.get("columns", []))
        rows = "".join("<tr>" + "".join(f"<td>{_inline(str(c))}</td>" for c in r) + "</tr>"
                       for r in b.get("rows", []))
        return f'<div class="block">{title}<table class="grid"><thead><tr>{cols}</tr></thead><tbody>{rows}</tbody></table>{cap}</div>'
    if t == "code":
        fn = f'<div class="code-fn">{html.escape(b.get("filename",""))}</div>' if b.get("filename") else ""
        return f'<div class="block">{title}{fn}<pre class="code">{html.escape(b.get("code",""))}</pre>{cap}</div>'
    if t == "file-tree":
        rows = []
        for e in b.get("entries", []):
            ch = e.get("change", "")
            badge = f'<span class="ft-badge ft-{ch}">{ch}</span>' if ch else ""
            note = f'<span class="ft-note">{_inline(e.get("note",""))}</span>' if e.get("note") else ""
            rows.append(f'<div class="ft-row"><code>{html.escape(e["path"])}</code> {badge}{note}</div>')
        return f'<div class="block">{title}<div class="ftree">{"".join(rows)}</div>{cap}</div>'
    if t == "data-model":
        cards = []
        for ent in b.get("entities", []):
            frows = []
            for f in ent.get("fields", []):
                flags = []
                if f.get("pk"): flags.append('<span class="dm-pk">PK</span>')
                if f.get("fk"): flags.append(f'<span class="dm-fk">FK→{html.escape(f["fk"])}</span>')
                note = f' <span class="dm-note">{html.escape(f["note"])}</span>' if f.get("note") else ""
                frows.append(f'<div class="dm-field"><code>{html.escape(f["name"])}</code>'
                             f'<span class="dm-type">{html.escape(f.get("type",""))}</span>{"".join(flags)}{note}</div>')
            cards.append(f'<div class="dm-entity"><div class="dm-name">{html.escape(ent["name"])}</div>{"".join(frows)}</div>')
        return f'<div class="block">{title}<div class="dmodel">{"".join(cards)}</div>{cap}</div>'
    return f'<div class="block">{title}<pre class="code">{html.escape(json.dumps(b, indent=2))}</pre></div>'

def render_html(manifest: dict, manifest_dir: Path) -> str:
    meta = manifest.get("meta", {})
    labels = manifest.get("labels", [])
    color_map = {l["name"]: l.get("color", "8b949e") for l in labels}
    milestones = manifest.get("milestones", [])
    epics = manifest.get("epics", [])
    n_issues = sum(len(e.get("issues", [])) for e in epics)

    def labels_html(names):
        return '<div class="chips">' + "".join(chip(n, color_map) for n in (names or [])) + "</div>"

    # Header / stats
    head = f"""<header class="head">
      <h1>{html.escape(meta.get('title','Implementation plan'))}</h1>
      <div class="sub">{_inline(meta.get('subtitle',''))}</div>
      <div class="metaline">
        {('<span>Source: <code>'+html.escape(meta['source'])+'</code></span>') if meta.get('source') else ''}
        {('<span>Repo: <code>'+html.escape(meta['repo'])+'</code></span>') if meta.get('repo') else ''}
        {('<span>Generated: '+html.escape(str(meta['generated']))+'</span>') if meta.get('generated') else ''}
      </div>
      <div class="stats">
        <div class="stat"><b>{len(epics)}</b> epics</div>
        <div class="stat"><b>{n_issues}</b> sub-issues</div>
        <div class="stat"><b>{len(labels)}</b> labels</div>
        <div class="stat"><b>{len(milestones)}</b> milestones</div>
      </div>
    </header>"""

    # Label legend + milestones
    legend = ""
    if labels:
        items = "".join(f'<div class="legend-item">{chip(l["name"], color_map)}'
                        f'<span class="legend-desc">{_inline(l.get("description",""))}</span></div>' for l in labels)
        legend = f'<section class="card"><h2>Labels</h2><div class="legend">{items}</div></section>'
    miles = ""
    if milestones:
        items = "".join(f'<div class="milestone"><div class="m-title">{html.escape(m["title"])}</div>'
                        f'<div class="m-desc">{_inline(m.get("description",""))}</div></div>' for m in milestones)
        miles = f'<section class="card"><h2>Milestones / phases</h2><div class="milestones">{items}</div></section>'

    overview = ""
    ob = manifest.get("overview_blocks", [])
    if ob:
        overview = '<section class="card"><h2>Overview</h2>' + "".join(render_block(b, manifest_dir) for b in ob) + "</section>"

    # Epics
    epic_html = []
    for ei, epic in enumerate(epics, 1):
        issues_html = []
        for ii, issue in enumerate(epic.get("issues", []), 1):
            blocks = "".join(render_block(b, manifest_dir) for b in issue.get("blocks", []))
            acc = ""
            if issue.get("acceptance"):
                acc = '<div class="acc"><div class="acc-h">Acceptance criteria</div><ul>' + \
                      "".join(f'<li>{_inline(a)}</li>' for a in issue["acceptance"]) + "</ul></div>"
            refs = (' · PRD ' + ", ".join(issue["prd_refs"])) if issue.get("prd_refs") else ""
            est = f'<span class="est">{html.escape(str(issue["estimate"]))}</span>' if issue.get("estimate") else ""
            issues_html.append(f"""
              <div class="issue">
                <div class="issue-head"><span class="inum">{ei}.{ii}</span>
                  <span class="ititle">{html.escape(issue['title'])}</span>{est}</div>
                {labels_html(issue.get('labels'))}
                <div class="sec functional"><div class="sec-h">🧭 Functional</div>{md(issue.get('functional_md',''))}{acc}</div>
                <div class="sec technical"><div class="sec-h">🛠️ Technical</div>{md(issue.get('technical_md',''))}</div>
                {blocks}
                <div class="issue-foot">{refs}</div>
              </div>""")
        epic_blocks = "".join(render_block(b, manifest_dir) for b in epic.get("blocks", []))
        ms = f'<span class="ms">◇ {html.escape(epic["milestone"])}</span>' if epic.get("milestone") else ""
        refs = (' · PRD ' + ", ".join(epic["prd_refs"])) if epic.get("prd_refs") else ""
        epic_html.append(f"""
          <details class="epic" open>
            <summary class="epic-sum">
              <span class="ekey">EPIC {ei}</span>
              <span class="etitle">{html.escape(epic['title'])}</span>{ms}
              <span class="ecount">{len(epic.get('issues', []))} sub-issues</span>
            </summary>
            <div class="epic-body">
              {labels_html(epic.get('labels'))}
              <div class="epic-summary">{md(epic.get('summary_md',''))}</div>
              <div class="epic-refs">{refs}</div>
              {epic_blocks}
              <div class="issues">{''.join(issues_html)}</div>
            </div>
          </details>""")

    oq = ""
    if manifest.get("open_questions"):
        items = "".join(f"<li>{_inline(q)}</li>" for q in manifest["open_questions"])
        oq = f'<section class="card oq"><h2>Open questions / decisions</h2><ul>{items}</ul></section>'

    return PAGE.replace("{{TITLE}}", html.escape(meta.get("title", "Plan"))) \
               .replace("{{BODY}}", head + legend + miles + overview +
                        '<section class="card"><h2>Epics &amp; sub-issues</h2>' + "".join(epic_html) + "</section>" + oq)

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{{TITLE}}</title>
<style>
:root{--bg:#0d1117;--surface:#161b22;--surface2:#1c2330;--border:#30363d;--text:#e6edf3;--muted:#8b949e;--accent:#6f42c1;--green:#2ea043;--blue:#388bfd;--amber:#d29922}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:28px 20px 80px}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;background:#ffffff14;padding:1px 5px;border-radius:5px;font-size:.88em}
h1{font-size:28px;margin:0 0 4px}h2{font-size:18px;margin:0 0 14px;padding-bottom:8px;border-bottom:1px solid var(--border)}
a{color:var(--blue)}
.head{margin-bottom:22px}.sub{color:var(--muted);font-size:16px}.metaline{margin:10px 0;color:var(--muted);font-size:13px;display:flex;gap:16px;flex-wrap:wrap}
.stats{display:flex;gap:10px;margin-top:14px;flex-wrap:wrap}.stat{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:8px 16px}.stat b{font-size:20px;color:var(--accent)}
.card{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:20px;margin:18px 0}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0}.chip{font-size:11.5px;font-weight:600;padding:2px 9px;border-radius:999px;letter-spacing:.01em}
.legend{display:grid;grid-template-columns:1fr 1fr;gap:8px 18px}.legend-item{display:flex;gap:8px;align-items:center}.legend-desc{color:var(--muted);font-size:13px}
.milestones{display:flex;gap:10px;flex-wrap:wrap}.milestone{flex:1;min-width:170px;background:var(--surface2);border:1px solid var(--border);border-radius:10px;padding:10px 12px}.m-title{font-weight:700}.m-desc{color:var(--muted);font-size:13px}
.epic{background:var(--surface2);border:1px solid var(--border);border-radius:12px;margin:14px 0;overflow:hidden}
.epic-sum{cursor:pointer;padding:14px 16px;display:flex;align-items:center;gap:12px;list-style:none}.epic-sum::-webkit-details-marker{display:none}
.ekey{font-size:11px;font-weight:800;color:#fff;background:var(--accent);padding:3px 9px;border-radius:6px;letter-spacing:.04em}
.etitle{font-weight:700;font-size:16px}.ms{color:var(--amber);font-size:12.5px}.ecount{margin-left:auto;color:var(--muted);font-size:13px}
.epic-body{padding:0 16px 16px}.epic-summary{color:#d6dee8}.epic-refs{color:var(--muted);font-size:12px;margin:4px 0 8px}
.issues{display:flex;flex-direction:column;gap:12px;margin-top:10px}
.issue{background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:14px}
.issue-head{display:flex;align-items:baseline;gap:10px}.inum{color:var(--accent);font-weight:800;font-size:13px}.ititle{font-weight:700}.est{margin-left:auto;font-size:11px;color:var(--muted);border:1px solid var(--border);border-radius:6px;padding:1px 7px}
.sec{margin-top:10px;border-left:3px solid var(--border);padding:2px 0 2px 12px}.sec-h{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin-bottom:2px}
.functional{border-left-color:var(--green)}.technical{border-left-color:var(--blue)}
.sec p{margin:.4em 0}.acc{margin-top:8px}.acc-h{font-size:12px;font-weight:700;color:var(--muted)}.acc ul,.sec ul,.sec ol{margin:.3em 0;padding-left:20px}
.issue-foot{color:var(--muted);font-size:12px;margin-top:8px}
.block{margin:14px 0}.block-title{font-weight:700;font-size:14px;margin-bottom:6px}.cap{color:var(--muted);font-size:12.5px;margin-top:6px}
.callout{border:1px solid var(--border);border-left-width:4px;border-radius:8px;padding:12px 14px;margin:12px 0;background:var(--surface2)}
.callout.info{border-left-color:var(--blue)}.callout.decision{border-left-color:var(--accent)}.callout.risk,.callout.warning{border-left-color:var(--amber)}.callout.success{border-left-color:var(--green)}
.diagram{background:var(--surface2);border:1px solid var(--border);border-radius:10px;padding:14px;overflow:auto}
.diagram .diagram-panel{display:flex;flex-direction:column;gap:8px;padding:10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);margin:6px 0}
.diagram .diagram-row{display:flex;gap:8px;flex-wrap:wrap}.diagram .diagram-node{flex:1;min-width:160px;padding:8px;border:1px solid var(--border);border-radius:8px;background:#6f42c130}
.diagram .diagram-box{flex:1;min-width:140px;padding:8px;border:1px dashed var(--border);border-radius:8px}.diagram .diagram-pill{padding:3px 9px;border:1px solid var(--border);border-radius:999px;background:#6f42c130;font-size:12px}
.diagram .diagram-muted{color:var(--muted);font-size:12px}.diagram .diagram-arrow{color:var(--muted);font-size:12px;text-align:center}.diagram .diagram-label{font-size:11px;text-transform:uppercase;color:var(--muted)}
.img{max-width:100%;border:1px solid var(--border);border-radius:10px}
.grid{width:100%;border-collapse:collapse;font-size:13.5px}.grid th,.grid td{border:1px solid var(--border);padding:6px 10px;text-align:left;vertical-align:top}.grid th{background:var(--surface2)}
.code{background:#010409;border:1px solid var(--border);border-radius:8px;padding:12px;overflow:auto;font-family:ui-monospace,Menlo,monospace;font-size:12.5px}.code-fn{font-family:ui-monospace,monospace;font-size:12px;color:var(--muted);margin-bottom:4px}
.ftree{font-family:ui-monospace,Menlo,monospace;font-size:12.5px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:10px}.ft-row{padding:3px 0;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.ft-badge{font-size:10px;font-weight:700;padding:1px 6px;border-radius:4px}.ft-added{background:#2ea04333;color:#3fb950}.ft-modified{background:#d2992233;color:#d29922}.ft-removed{background:#f8514933;color:#f85149}.ft-renamed{background:#388bfd33;color:#58a6ff}.ft-note{color:var(--muted);font-family:-apple-system,sans-serif}
.dmodel{display:flex;gap:10px;flex-wrap:wrap}.dm-entity{flex:1;min-width:220px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;overflow:hidden}.dm-name{background:var(--accent);color:#fff;font-weight:700;padding:6px 10px}
.dm-field{display:flex;gap:6px;align-items:center;padding:4px 10px;border-top:1px solid var(--border);font-size:12.5px;flex-wrap:wrap}.dm-type{color:var(--muted)}.dm-pk{background:#d2992233;color:#d29922;font-size:10px;padding:0 5px;border-radius:4px;font-weight:700}.dm-fk{background:#388bfd33;color:#58a6ff;font-size:10px;padding:0 5px;border-radius:4px}.dm-note{color:var(--muted);font-size:11.5px}
.oq ul{padding-left:20px}.mermaid{background:var(--surface2);border:1px solid var(--border);border-radius:10px;padding:14px}
</style></head><body><div class="wrap">{{BODY}}</div>
<script type="module">try{const m=await import('https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs');m.default.initialize({startOnLoad:true,theme:'dark'});}catch(e){}</script>
</body></html>"""

# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def validate(manifest: dict) -> list[str]:
    errs = []
    if "meta" not in manifest: errs.append("missing 'meta'")
    for i, l in enumerate(manifest.get("labels", [])):
        if "name" not in l: errs.append(f"labels[{i}] missing 'name'")
    for ei, e in enumerate(manifest.get("epics", [])):
        if "title" not in e: errs.append(f"epics[{ei}] missing 'title'")
        if not e.get("issues"): errs.append(f"epics[{ei}] ('{e.get('title','?')}') has no issues")
        for ii, iss in enumerate(e.get("issues", [])):
            if "title" not in iss: errs.append(f"epics[{ei}].issues[{ii}] missing 'title'")
            if not iss.get("functional_md"): errs.append(f"issue '{iss.get('title','?')}' missing functional_md")
            if not iss.get("technical_md"): errs.append(f"issue '{iss.get('title','?')}' missing technical_md")
    return errs

# --------------------------------------------------------------------------- #
# gh helpers
# --------------------------------------------------------------------------- #
def gh(args: list[str], check=True, capture=True) -> str:
    r = subprocess.run(["gh"] + args, capture_output=capture, text=True)
    if check and r.returncode != 0:
        sys.stderr.write(f"gh {' '.join(args)}\n{r.stderr}\n")
        raise SystemExit(1)
    return (r.stdout or "").strip()

def resolve_repo(arg: str | None) -> str:
    if arg: return arg
    return gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])

# --------------------------------------------------------------------------- #
# create
# --------------------------------------------------------------------------- #
def cmd_create(manifest: dict, repo: str, apply: bool, limit_epic: str | None, manifest_dir: Path):
    errs = validate(manifest)
    if errs:
        print("Manifest invalid:\n  - " + "\n  - ".join(errs)); raise SystemExit(1)
    repo = resolve_repo(repo)
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"== prd-to-issues {mode} → {repo} ==\n")

    # 1) labels
    for l in manifest.get("labels", []):
        color = l.get("color", "8b949e").lstrip("#"); desc = l.get("description", "")
        if apply:
            gh(["label", "create", l["name"], "--repo", repo, "--color", color,
                "--description", desc, "--force"], check=False)
        print(f"  label  {'upsert' if apply else 'would upsert'}: {l['name']} (#{color})")

    # 2) milestones (via REST; map title -> number)
    ms_map = {}
    existing_ms = {}
    raw = gh(["api", f"repos/{repo}/milestones", "--paginate", "-q",
              ".[] | [.title,.number] | @tsv"], check=False)
    for line in filter(None, raw.split("\n")):
        title, num = line.split("\t"); existing_ms[title] = int(num)
    for m in manifest.get("milestones", []):
        if m["title"] in existing_ms:
            ms_map[m["title"]] = existing_ms[m["title"]]
            print(f"  milestone exists: {m['title']} (#{existing_ms[m['title']]})")
        elif apply:
            out = gh(["api", f"repos/{repo}/milestones", "-f", f"title={m['title']}",
                      "-f", f"description={m.get('description','')}", "-q", ".number"], check=False)
            if out.isdigit(): ms_map[m["title"]] = int(out); print(f"  milestone created: {m['title']} (#{out})")
        else:
            print(f"  milestone would create: {m['title']}")

    # 3) existing issues (idempotency by title)
    existing = {}
    raw = gh(["issue", "list", "--repo", repo, "--state", "all", "--limit", "800",
              "--json", "number,title"], check=False)
    try:
        for it in json.loads(raw or "[]"): existing[it["title"].strip()] = it["number"]
    except Exception: pass

    created_path = manifest_dir / "created.json"
    created = json.loads(created_path.read_text()) if created_path.exists() else {}

    def ensure_issue(title, body, labels, milestone) -> int | None:
        if title.strip() in existing:
            print(f"    · exists: #{existing[title.strip()]}  {title}"); return existing[title.strip()]
        if not apply:
            print(f"    + would create: {title}  [{', '.join(labels)}]"); return None
        args = ["issue", "create", "--repo", repo, "--title", title, "--body", body]
        for lb in labels: args += ["--label", lb]
        if milestone and milestone in ms_map: args += ["--milestone", milestone]
        url = gh(args, check=False)
        num = int(url.rstrip("/").split("/")[-1]) if "/issues/" in url else None
        if num:
            existing[title.strip()] = num
            created[title] = {"number": num, "url": url}
            print(f"    + created #{num}: {title}")
        return num

    epics = manifest.get("epics", [])
    if limit_epic:
        epics = [e for e in epics if e.get("key") == limit_epic or e.get("title") == limit_epic]

    for epic in epics:
        print(f"\n  EPIC: {epic['title']}")
        epic_labels = epic.get("labels", ["epic"])
        epic_num = ensure_issue(epic["title"], epic_body_md(epic, []), epic_labels, epic.get("milestone"))
        child_lines = []
        for issue in epic.get("issues", []):
            body = issue_body_md(issue, epic["title"], epic_num)
            num = ensure_issue(issue["title"], body, issue.get("labels", []), issue.get("milestone") or epic.get("milestone"))
            child_lines.append(f"- [ ] #{num} {issue['title']}" if num else f"- [ ] {issue['title']}")
            # native sub-issue link (best effort)
            if apply and epic_num and num:
                cid = gh(["api", f"repos/{repo}/issues/{num}", "-q", ".id"], check=False)
                if cid.isdigit():
                    gh(["api", "-X", "POST", f"repos/{repo}/issues/{epic_num}/sub_issues",
                        "-F", f"sub_issue_id={cid}"], check=False)
        # rewrite epic body with the task list (hierarchy always visible)
        if apply and epic_num:
            gh(["issue", "edit", str(epic_num), "--repo", repo,
                "--body", epic_body_md(epic, child_lines)], check=False)

    if apply:
        created_path.write_text(json.dumps(created, indent=2))
        print(f"\nWrote {created_path}")
    print(f"\n== {mode} complete ==")

# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="PRD manifest → visual HTML plan + GitHub issues")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render"); r.add_argument("--manifest", required=True); r.add_argument("--out", required=True)
    c = sub.add_parser("create"); c.add_argument("--manifest", required=True); c.add_argument("--repo", default=None)
    g = c.add_mutually_exclusive_group(); g.add_argument("--dry-run", action="store_true"); g.add_argument("--apply", action="store_true")
    c.add_argument("--limit-epic", default=None)
    a = ap.parse_args()

    mpath = Path(a.manifest); manifest = json.loads(mpath.read_text()); mdir = mpath.parent
    if a.cmd == "render":
        errs = validate(manifest)
        if errs: print("WARNING — manifest issues:\n  - " + "\n  - ".join(errs))
        Path(a.out).write_text(render_html(manifest, mdir))
        print(f"Wrote {a.out}  ({len(manifest.get('epics',[]))} epics, "
              f"{sum(len(e.get('issues',[])) for e in manifest.get('epics',[]))} sub-issues)")
    elif a.cmd == "create":
        cmd_create(manifest, a.repo, apply=a.apply, limit_epic=a.limit_epic, manifest_dir=mdir)

if __name__ == "__main__":
    main()
