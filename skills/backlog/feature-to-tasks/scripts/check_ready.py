#!/usr/bin/env python3
"""Definition-of-Ready checker for SOC technical tasks.

Validates drafted or live tasks against the standard in ../references/tarea.md.
Pure stdlib, no network: the skill fetches from Jira and writes the JSON, this
script only judges it. That split keeps the rules testable and lets the same code
run over a dry-run batch and over the live board.

Usage
-----
    # validate a dry-run batch before anything is created
    python3 check_ready.py batch.json

    # re-validate tickets exported from the live board
    python3 check_ready.py board.json --audit --existing-packages api,contracts

Input schema
------------
    {
      "batch": {"started_at": "2026-07-31 09:00", "project": "SOC"},
      "tasks": [
        {
          "key": "SOC-201",              # optional; present when auditing
          "summary": "api: endpoint de resumen del paciente",
          "description": "### Requisito\\n...",
          "labels": ["RF-M1-001", "area:api", "phase-1"],
          "parent": "SOC-140",           # the Epic
          "relates": ["SOC-1"],
          "blocks": ["SOC-205"],
          "multi_package": false         # true only with a written justification
        }
      ]
    }

Exit codes: 0 all pass, 1 at least one ERROR, 2 bad input.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field

# --- the layout contract, mirrored from ../references/monorepo.md -------------

PACKAGE_ROOTS = {
    "api": "apps/api",
    "transcriber": "apps/transcriber",
    "assistant": "apps/assistant",
    "contracts": "packages/contracts",
    "sdk": "packages/sdk",
    "db": "db",
    "tests": "tests",
    "evals": "evals",
    "infra": "infra",
}
ROOT_TO_ALIAS = {v: k for k, v in PACKAGE_ROOTS.items()}

REQUIRED_SECTIONS = [
    "Requisito",
    "Funcional",
    "Enfoque técnico",
    "Alcance",
    "Puntos de entrada",
    "Criterios de aceptación",
    "Comprobación",
    "Riesgos mitigados (ISO 14971)",
]
OPTIONAL_SECTIONS = ["Contrato"]
KNOWN_SECTIONS = REQUIRED_SECTIONS + OPTIONAL_SECTIONS

MAX_SUMMARY = 70

RE_SECTION = re.compile(r"^###[ \t]+(.+?)[ \t]*$", re.MULTILINE)
RE_REQUISITO = re.compile(r"\bSOC-(\d+)\s*\(\s*([A-Z]{2,4}(?:-[A-Z0-9]+)+)\s*\)")
RE_RISK = re.compile(r"\bR-\d{2,}\b")
RE_BULLET = re.compile(r"^\s*[*-]\s+(.*\S)\s*$", re.MULTILINE)
RE_BACKTICK_PATH = re.compile(r"`([^`\n]+?)`")
RE_FENCE = re.compile(r"```([A-Za-z0-9_+-]*)\n(.*?)```", re.DOTALL)
RE_SOC = re.compile(r"\bSOC-\d+\b")
RE_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")

# Phrases that make an acceptance criterion unverifiable. Heuristic on purpose:
# it flags for human review, it does not block on its own.
VAGUE = [
    "correctamente", "adecuadamente", "de forma adecuada", "razonable",
    "que funcione", "sin problemas", "buena experiencia", "rápido",
    "eficiente", "fácil de usar", "mejorar", "optimizar", "robusto",
]

SEVERITY_ORDER = {"ERROR": 0, "WARN": 1}


@dataclass
class Finding:
    severity: str
    rule: str
    message: str


@dataclass
class TaskReport:
    ident: str
    findings: list[Finding] = field(default_factory=list)

    def err(self, rule: str, message: str) -> None:
        self.findings.append(Finding("ERROR", rule, message))

    def warn(self, rule: str, message: str) -> None:
        self.findings.append(Finding("WARN", rule, message))

    @property
    def errors(self) -> int:
        return sum(1 for f in self.findings if f.severity == "ERROR")

    @property
    def warnings(self) -> int:
        return sum(1 for f in self.findings if f.severity == "WARN")


def split_sections(description: str) -> tuple[dict[str, str], list[str]]:
    """Return {heading: body} plus headings in document order."""
    matches = list(RE_SECTION.finditer(description or ""))
    sections: dict[str, str] = {}
    order: list[str] = []
    for i, m in enumerate(matches):
        name = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(description)
        sections[name] = description[start:end].strip()
        order.append(name)
    return sections, order


def path_root(path: str) -> str | None:
    """Longest known package root that prefixes `path`."""
    clean = path.strip().lstrip("./")
    for root in sorted(ROOT_TO_ALIAS, key=len, reverse=True):
        if clean == root or clean.startswith(root + "/"):
            return root
    return None


# --- individual rules ---------------------------------------------------------


def check_summary(task: dict, rep: TaskReport) -> str | None:
    summary = (task.get("summary") or "").strip()
    if not summary:
        rep.err("summary", "summary vacío")
        return None
    truncated = summary.endswith("...") or summary.endswith("…")
    if truncated:
        rep.err("summary", "termina en '...' — no heredes el truncado de las Features")
    if ":" not in summary:
        rep.err("summary", "falta el prefijo '<alias>: '")
        return None
    alias, rest = summary.split(":", 1)
    alias = alias.strip()
    if alias not in PACKAGE_ROOTS:
        rep.err("summary", f"alias de paquete desconocido: {alias!r} "
                           f"(esperado uno de {', '.join(sorted(PACKAGE_ROOTS))})")
        return None
    if not rest.strip():
        rep.err("summary", "no hay descripción tras el prefijo")
    if len(summary) > MAX_SUMMARY:
        rep.warn("summary", f"{len(summary)} caracteres, máximo recomendado {MAX_SUMMARY}")
    if not truncated and rest.strip().endswith("."):
        rep.warn("summary", "no termines el summary en punto")
    return alias


def check_sections(sections: dict[str, str], order: list[str], rep: TaskReport) -> None:
    for name in REQUIRED_SECTIONS:
        if name not in sections:
            rep.err("secciones", f"falta la sección obligatoria '### {name}'")
        elif not sections[name].strip():
            rep.err("secciones", f"la sección '### {name}' está vacía")
    for name in order:
        if name not in KNOWN_SECTIONS:
            rep.err("secciones", f"sección no reconocida '### {name}' — "
                                 "la plantilla no admite secciones nuevas")
    known_in_order = [n for n in order if n in REQUIRED_SECTIONS]
    if known_in_order != [n for n in REQUIRED_SECTIONS if n in known_in_order]:
        rep.err("secciones", "las secciones están desordenadas respecto a la plantilla")
    body = sections.get("Contrato")
    if body is not None and body.strip().upper() in {"N/A", "NA", "-", "NINGUNO"}:
        rep.err("contrato", "'### Contrato' con 'N/A' — omite la sección entera")


def check_requisito(sections: dict[str, str], rep: TaskReport) -> list[tuple[str, str]]:
    body = sections.get("Requisito", "")
    refs = [(f"SOC-{n}", rid) for n, rid in RE_REQUISITO.findall(body)]
    if not refs:
        rep.err("requisito", "no cita ningún 'SOC-<n> (<REQ-ID>)' — "
                             "es el ancla de trazabilidad ISO 14971, no es opcional")
    return refs


def check_alcance(sections: dict[str, str], rep: TaskReport) -> None:
    body = sections.get("Alcance", "")
    if not re.search(r"^\s*IN\s*$", body, re.MULTILINE):
        rep.err("alcance", "falta el bloque 'IN'")
    m_out = re.search(r"^\s*OUT\s*$", body, re.MULTILINE)
    if not m_out:
        rep.err("alcance", "falta el bloque 'OUT'")
        return
    out_body = body[m_out.end():]
    out_bullets = RE_BULLET.findall(out_body)
    if not out_bullets:
        rep.err("alcance", "'OUT' está vacío — es la sección de mayor rendimiento "
                           "del ticket; si de verdad no hay nada fuera, escribe por qué")
        return
    if not any(RE_SOC.search(b) for b in out_bullets):
        rep.warn("alcance", "ningún bullet de 'OUT' nombra el SOC-n que sí lo cubre")


def check_entrypoints(sections: dict[str, str], task: dict, alias: str | None,
                      rep: TaskReport, existing: set[str]) -> None:
    body = sections.get("Puntos de entrada", "")
    candidates = [p for p in RE_BACKTICK_PATH.findall(body) if "/" in p or "." in p]
    if not candidates:
        rep.err("entrada", "no cita ninguna ruta entre backticks")
        return

    roots: set[str] = set()
    for raw in candidates:
        path = raw.replace("[previsto]", "").strip()
        root = path_root(path)
        if root is None:
            rep.err("entrada", f"ruta fuera del layout acordado: `{path}` "
                               "(ver references/monorepo.md)")
        else:
            roots.add(root)

    if not roots:
        return

    aliases = {ROOT_TO_ALIAS[r] for r in roots}
    if len(aliases) > 1 and not task.get("multi_package"):
        rep.err("entrada", "la tarea toca varios paquetes "
                           f"({', '.join(sorted(aliases))}) sin justificación — "
                           "pártela, o marca multi_package tras justificarlo en "
                           "'### Enfoque técnico'")
    if alias and alias not in aliases:
        rep.warn("entrada", f"el summary dice '{alias}' pero las rutas apuntan a "
                            f"{', '.join(sorted(aliases))}")

    # [previsto] markers: mandatory while the package does not exist, stale once it does
    lines = [ln for ln in body.splitlines() if RE_BACKTICK_PATH.search(ln)]
    for line in lines:
        paths = [p for p in RE_BACKTICK_PATH.findall(line) if "/" in p or "." in p]
        if not paths:
            continue
        root = path_root(paths[0].replace("[previsto]", "").strip())
        if root is None:
            continue
        pkg = ROOT_TO_ALIAS[root]
        has_marker = "[previsto]" in line
        if pkg in existing and has_marker:
            rep.err("entrada", f"`{paths[0]}` sigue marcada [previsto] pero el "
                               f"paquete '{pkg}' ya existe — refina el ticket")
        if pkg not in existing and not has_marker and existing:
            rep.warn("entrada", f"`{paths[0]}` sin [previsto] y el paquete '{pkg}' "
                                "aún no existe")


def check_acceptance(sections: dict[str, str], task: dict, rep: TaskReport) -> None:
    body = sections.get("Criterios de aceptación", "")
    bullets = RE_BULLET.findall(body)
    if not bullets:
        rep.err("aceptacion", "no hay criterios en formato lista")
        return
    for b in bullets:
        low = b.lower()
        hit = next((v for v in VAGUE if v in low), None)
        if hit:
            rep.warn("aceptacion", f"criterio poco verificable ({hit!r}): {b[:70]}")

    # thresholds inherited from the Feature must appear literally
    expected = task.get("feature_thresholds") or []
    if expected:
        present = set(RE_NUMBER.findall(body))
        for value in expected:
            if str(value) not in present:
                rep.err("aceptacion", f"el umbral {value!r} de la Feature no aparece "
                                      "literal en los criterios — no lo parafrasees")


def check_comprobacion(sections: dict[str, str], rep: TaskReport) -> None:
    body = sections.get("Comprobación", "")
    fences = RE_FENCE.findall(body)
    if not fences:
        rep.err("comprobacion", "falta el bloque de código con los comandos")
        return
    commands: list[str] = []
    for _lang, code in fences:
        for line in code.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                commands.append(line)
    if not commands:
        rep.err("comprobacion", "el bloque de comandos está vacío")
        return
    for cmd in commands:
        for part in re.split(r"&&|\|\||;", cmd):
            part = part.strip()
            if part and not part.startswith("just"):
                rep.err("comprobacion", f"comando que no usa la fachada 'just': {part!r} "
                                        "(el toolchain es mixto TS/Python)")


def check_riesgos(sections: dict[str, str], rep: TaskReport) -> None:
    body = sections.get("Riesgos mitigados (ISO 14971)", "")
    if not RE_RISK.search(body):
        rep.err("riesgos", "no cita ningún 'R-nn' — obligatorio en producto sanitario")


def check_metadata(task: dict, alias: str | None, refs: list[tuple[str, str]],
                   rep: TaskReport, known_keys: set[str]) -> None:
    labels = set(task.get("labels") or [])
    if alias and f"area:{alias}" not in labels:
        rep.err("labels", f"falta la etiqueta 'area:{alias}'")
    if not any(l.startswith("phase-") for l in labels):
        rep.err("labels", "falta la etiqueta 'phase-<n>'")
    for _key, req_id in refs:
        if req_id not in labels:
            rep.err("labels", f"falta la etiqueta de requisito '{req_id}'")
    if task.get("assignee"):
        rep.err("assignee", "la skill no asigna tareas — quita el assignee")
    if not task.get("parent"):
        rep.err("jerarquia", "sin 'parent': la tarea debe colgar de su Epic")

    relates = set(task.get("relates") or [])
    cited = {k for k, _ in refs}
    for key in cited - relates:
        rep.err("enlaces", f"'{key}' se cita en '### Requisito' pero no hay enlace Relates")
    for key in relates - cited:
        rep.warn("enlaces", f"enlace Relates a '{key}' que no se cita en '### Requisito'")

    for key in task.get("blocks") or []:
        if known_keys and key not in known_keys and not RE_SOC.fullmatch(key):
            rep.warn("enlaces", f"'Blocks' apunta a '{key}', que no está en el lote")


# --- driver -------------------------------------------------------------------


def check_task(task: dict, index: int, known_keys: set[str],
               existing: set[str]) -> TaskReport:
    ident = task.get("key") or task.get("summary") or f"#{index + 1}"
    rep = TaskReport(ident=ident)

    alias = check_summary(task, rep)
    description = task.get("description") or ""
    if not description.strip():
        rep.err("descripcion", "descripción vacía")
        return rep

    sections, order = split_sections(description)
    check_sections(sections, order, rep)
    refs = check_requisito(sections, rep)
    check_alcance(sections, rep)
    check_entrypoints(sections, task, alias, rep, existing)
    check_acceptance(sections, task, rep)
    check_comprobacion(sections, rep)
    check_riesgos(sections, rep)
    check_metadata(task, alias, refs, rep, known_keys)
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("batch", help="JSON file with {batch, tasks}")
    ap.add_argument("--audit", action="store_true",
                    help="auditing live tickets rather than validating drafts")
    ap.add_argument("--existing-packages", default="",
                    help="comma-separated aliases whose package already exists "
                         "(stale [previsto] markers become errors)")
    ap.add_argument("--warnings-as-errors", action="store_true")
    ap.add_argument("--quiet", action="store_true", help="only print failures")
    args = ap.parse_args(argv)

    try:
        with open(args.batch, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"no puedo leer {args.batch}: {exc}", file=sys.stderr)
        return 2

    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        print("el JSON no trae 'tasks' o está vacío", file=sys.stderr)
        return 2

    existing = {a.strip() for a in args.existing_packages.split(",") if a.strip()}
    unknown = existing - set(PACKAGE_ROOTS)
    if unknown:
        print(f"--existing-packages desconocidos: {', '.join(sorted(unknown))}",
              file=sys.stderr)
        return 2

    known_keys = {t.get("key") for t in tasks if t.get("key")}
    reports = [check_task(t, i, known_keys, existing) for i, t in enumerate(tasks)]

    failing = [r for r in reports if r.errors or (args.warnings_as_errors and r.warnings)]
    noisy = [r for r in reports if r.findings]

    for rep in (failing if args.quiet else noisy):
        print(f"\n{rep.ident}")
        for f in sorted(rep.findings, key=lambda f: SEVERITY_ORDER[f.severity]):
            print(f"  {f.severity:<5} [{f.rule}] {f.message}")

    total_err = sum(r.errors for r in reports)
    total_warn = sum(r.warnings for r in reports)
    mode = "audit" if args.audit else "dry-run"
    ready = len(tasks) - len(failing)
    plural = lambda n, s, p: f"{n} {s if n == 1 else p}"
    print(f"\n{mode}: {plural(len(tasks), 'tarea', 'tareas')} · "
          f"{plural(total_err, 'error', 'errores')} · "
          f"{plural(total_warn, 'aviso', 'avisos')} · "
          f"{plural(ready, 'lista', 'listas')}")

    if failing:
        n = plural(len(failing), "tarea", "tareas")
        if args.audit:
            print(f"\n{n} fuera del estándar. Pásalas por "
                  "'feature-to-tasks refine' — auditar no edita.")
        else:
            print(f"\n{n} no pasan el Definition of Ready. "
                  "No se crea nada: se corrige el lote entero y se vuelve a validar.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
