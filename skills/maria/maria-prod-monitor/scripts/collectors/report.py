#!/usr/bin/env python3
"""The per-run report (workplan §2.4), rendered from what the pipeline produced.

Sections, in the order an operator reads them:

  1. Cabecera de versiones — what was deployed while these calls happened.
  2. Embudo por capas — the metric of the design: semantic quality is only
     reported over the universe of calls that passed L1 and L2.
  3. Fallos — the summary table, then **one line per case**, grouped by client,
     each linking to the call in the one-stop-shop console. That list is a
     worksheet: every case ends either as a Jira task or as an entry in
     `known_errors.yaml`.
  3b. Casos ya decididos — what the KEDB hides, and why. A counter, never a
     silent omission; `--show-hidden` lists them one by one.
  4. Latencia — from the logs, when they are reachable.
  5. Alertas del motor existente — read, never rebuilt.
  6. Residuo y cola del juez — what grows the catalog, what costs tokens.
  7. Parte de bugs — frequency × severity, with a call to open.

**A section with no data says why.** A report that silently omits the L3 gate
reads as "semantics are fine", and an empty latency table reads as "latency is
fine"; both are lies of omission, and this pipeline has real gaps today (no
judge yet, no access to prod's Log Analytics). Every one of them is printed.

Usage:
    python3 fetch_bundles.py --env prod --hours 24 --out /tmp/b.json
    python3 run_detectors.py /tmp/b.json --out /tmp/f.json
    python3 report.py /tmp/f.json --bundles /tmp/b.json [--latency /tmp/l.json] \
        [--out reports/2026-08-31_1800_prod.md]
"""
from __future__ import annotations

import argparse
import json
import re
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from _findings import catalog, finding as make_finding, is_actionable, severity_rank
from _supabase import AccessError, Supabase, load_keys

from _paths import KEDB, STATE, TASKS  # noqa: E402
STATE_PATH = str(STATE)

SEVERITY_ICON = {"critical": "🟥", "high": "🟧", "medium": "🟨", "low": "⬜"}

#: The one-stop-shop console, where a call can actually be read. Same host per
#: environment as core's own deep links (`/apps/maria/calls/...`).
CONSOLE_SUFFIX = {"prod": "", "stg": "-stg", "dev": "-dev"}

#: Evidence keys worth putting on a triage line, in the order they help decide.
#: Generic on purpose: a per-code table of "interesting fields" would drift from
#: the detectors the moment one of them changes its evidence.
NOTE_KEYS = (
    # Lo que dijo el proveedor va primero: solo se pintan 3 datos por caso, y
    # «SINA dice que la fecha no coincide» decide el triaje mucho mejor que el
    # número de intentos. Detrás, lo inferido.
    "significado",
    "cadena",
    "documentos_rechazados",
    "veredicto_del_log",
    "causa_sip",
    "verificacion",
    "colgo_paciente",
    "colgo_tras_transferir_s",
    "audio_de_agente_s",
    # De la comparación manual con la ficha de SINA: otra fuente, otro nombre.
    "desajustes_de_la_ficha",
    "desajustes",
    "evidencia",
    "intentos",
    "documentos",
    "tool",
    "consecutive_calls",
    "login_attempts",
    "transfer_failure_reason",
    "worst_gap_s",
    "gap_count",
    "error",
    "recovered_later",
    "cause_recorded",
    "idle_before_end_s",
    "turns",
    "agent_turns",
)


def call_url(env: str, call_id: str) -> str:
    """Deep link to the call in the console, so a case can be read in one click."""
    return f"https://onestopshop{CONSOLE_SUFFIX.get(env, '')}.api.omniloy.com/apps/maria/calls/{call_id}"


def call_link(env: str, call_id: str) -> str:
    return f"[`{call_id[:8]}`]({call_url(env, call_id)})"


def _local(iso: str) -> str:
    """`HH:MM:SS` en hora de Madrid, que es la que se ve en la consola."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    try:
        moment = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except ValueError:
        return "?"
    return moment.astimezone(ZoneInfo("Europe/Madrid")).strftime("%H:%M:%S")


def _format_gaps(gaps: list) -> str:
    """Cada silencio como `inicio→fin (Ns)`, para poder localizarlo en la llamada.

    El inicio es la marca de la utterance del paciente y el fin la de la
    siguiente: es exactamente el tramo que hay que escuchar.
    """
    from datetime import datetime, timedelta

    rendered = []
    for gap in gaps[:4]:
        start_iso = gap.get("at") or ""
        seconds = gap.get("seconds") or 0
        try:
            start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
            end = (start + timedelta(seconds=seconds)).isoformat()
        except ValueError:
            end = ""
        rendered.append(f"{_local(start_iso)}→{_local(end)} ({seconds:.0f}s)")
    return ", ".join(rendered)


def _caller_format(room_name: Optional[str]) -> str:
    """Formato del número que **presenta el trunk**, sacado del `room_name`.

    Cuidado con leerlo como «quien llama»: es el caller ID que llega por SIP, y
    no siempre es el del paciente. En Hospital Memorial Publio Cordón es
    constante (`+34 96• ••• •••`, su centralita) en las 187 llamadas de la ventana,
    mientras los pacientes reales son 158 `end_users` distintos con teléfonos
    distintos. El teléfono del paciente está en `end_users.phone`, no aquí.

    `calls` no guarda teléfono, y `call_source` **no existe** en prod pese a lo
    que sugiere el nombre. El formato varía por cliente (San Roque a pelo con 9
    dígitos, el resto con prefijo), y conviene medirlo porque es el tipo de
    detalle al que se le echa la culpa sin comprobarlo.
    """
    # `anonymous` literal es como llega un número oculto: el log lo avisa con
    # `Inbound SIP call with hidden/unknown caller number (raw='anonymous')` y
    # esas llamadas van por una vía distinta —contestar y transferir SIN
    # validar al paciente—, así que se quedan sin `end_user_id` y cualquier tool
    # que necesite el id de usuario falla (`L2-TOOL-003`, MAR-1529).
    #
    # Ojo: el room NO siempre lo delata. De las 3 llamadas con número oculto del
    # 2026-09-03, solo una quedó como `phs-_anonymous_…`; las otras dos
    # conservaron el número que presenta el trunk del cliente. El log es la
    # única fuente fiable.
    match = re.match(r"^.+?-_(?P<phone>anonymous|\+?[0-9]*)_", room_name or "")
    if not match:
        return "sin room reconocible"
    phone = match.group("phone")
    if phone == "anonymous":
        return "oculto (anonymous)"
    if not phone:
        return "oculto o ausente"
    return f"con prefijo ({len(phone)})" if phone.startswith("+") else f"sin prefijo ({len(phone)} dígitos)"


def _finding_note(finding: dict) -> str:
    """The two or three facts that decide whether this case needs a task."""
    evidence = finding.get("evidence") or {}
    if isinstance(evidence.get("gaps"), list) and evidence["gaps"]:
        # El silencio se revisa escuchando: sin las horas hay que buscarlo a mano.
        note = f"silencios (hora de Madrid): {_format_gaps(evidence['gaps'])}"
        if len(evidence["gaps"]) > 4:
            note += f" …y {len(evidence['gaps']) - 4} más"
        return note
    parts = []
    for key in NOTE_KEYS:
        if key not in evidence or evidence[key] in (None, "", [], {}):
            continue
        value = evidence[key]
        if isinstance(value, dict):
            value = ", ".join(f"{k} {v}" for k, v in value.items() if v)
        text = str(value)
        if len(text) > 60:
            text = text[:60] + "…"
        parts.append(f"{key}={text}")
        if len(parts) == 3:
            break
    return " · ".join(parts)


# -- state ----------------------------------------------------------------


def load_state() -> dict:
    try:
        with open(STATE_PATH) as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {"runs": {}}


def save_state(state: dict) -> None:
    with open(STATE_PATH, "w") as handle:
        json.dump(state, handle, indent=1, ensure_ascii=False)


def remember(state: dict, env: str, until: str, counts: Dict[str, int], calls: int) -> Optional[dict]:
    """Store this run's per-code counts; hand back the previous one for a trend.

    Only counts, never calls: the whole point of the state file is that a trend
    costs nothing to compute and re-reading a window of calls is what it avoids.
    """
    runs = state.setdefault("runs", {}).setdefault(env, [])
    previous = runs[-1] if runs else None
    runs.append({"until": until, "calls": calls, "codes": counts})
    del runs[:-30]
    return previous


# -- KEDB (known_errors.yaml) ---------------------------------------------


TASKS_PATH = str(TASKS)
KEDB_PATH = str(KEDB)


def load_tasks(path: str = TASKS_PATH) -> Dict[str, List[dict]]:
    """`{código: [tareas abiertas]}` desde `tasks.yaml`.

    Sirve para una sola pregunta, la que de verdad se hace al cerrar el triaje:
    ¿queda algún accionable sin tarea y sin decisión? Sin esto hay que repasar el
    informe a mano y fiarse de la memoria.
    """
    import monitor_store

    if path == TASKS_PATH and monitor_store.exists():
        con = monitor_store.connect()
        try:
            raw = {"tasks": monitor_store.load_tasks(con)}
        finally:
            con.close()
    else:
        try:
            import yaml

            with open(path) as handle:
                raw = yaml.safe_load(handle) or {}
        except (OSError, ImportError):
            return {}
    by_code: Dict[str, List[dict]] = {}
    for task in raw.get("tasks") or []:
        if task.get("status") == "done":
            continue
        for code in task.get("codes") or []:
            by_code.setdefault(code, []).append(task)
    return by_code


def task_for(finding: dict, tasks: Dict[str, List[dict]]) -> Optional[dict]:
    """La tarea que ya cubre este hallazgo, si la hay.

    Una tarea puede acotarse a un cliente (`tenant`) o a llamadas concretas
    (`calls`), porque el mismo código puede tener dueños distintos: los
    `L2-ESCALATION-001` de Publio Cordón son el trunk de MAR-1518, y los de San
    Roque son otra cosa. Sin ese acotado, mapear el código entero dar\u00eda por
    cubierto lo que no lo est\u00e1.
    """
    for task in tasks.get(finding["code"], []):
        if task.get("tenant") and task["tenant"] != finding.get("tenant"):
            continue
        if task.get("calls") and finding["conversation_id"] not in task["calls"]:
            continue
        return task
    return None


def load_kedb(path: str = KEDB_PATH) -> List[dict]:
    """Decisions already taken about recurring cases.

    Absent file = no decisions yet, which is a normal state and not an error:
    the KEDB fills up as cases get reviewed one by one.

    The shared store (monitor.sqlite) wins over the YAML when it exists.
    """
    import monitor_store

    if path == KEDB_PATH and monitor_store.exists():
        con = monitor_store.connect()
        try:
            return monitor_store.load_kedb(con)
        finally:
            con.close()
    try:
        import yaml

        with open(path) as handle:
            raw = yaml.safe_load(handle) or {}
    except (OSError, ImportError):
        return []
    entries = raw.get("entries") or []
    return [e for e in entries if isinstance(e, dict) and e.get("code")]


def kedb_match(finding: dict, entries: List[dict]) -> Optional[dict]:
    """The entry that already decided this case, if any.

    Exact matching only, on the finding's own evidence. A matcher with an
    expression in it is a matcher nobody can audit, and this file decides what
    the monitor stops showing.
    """
    evidence = finding.get("evidence") or {}
    for entry in entries:
        if entry["code"] != finding["code"]:
            continue
        if entry.get("tenant") and entry["tenant"] != finding.get("tenant"):
            continue
        # Una decisión puede ser sobre llamadas concretas y no sobre un patrón:
        # «revisé estas dos y son correctas» es una decisión legítima, y no
        # generaliza a la ventana siguiente a propósito.
        if entry.get("calls") and finding["conversation_id"] not in entry["calls"]:
            continue
        conditions = entry.get("when") or {}
        if any(str(evidence.get(key)) != str(value) for key, value in conditions.items()):
            continue
        # `unless`: exact exceptions to the decision. A pattern decided as normal
        # ("retrying with changing data") must not hide the case that looks like it
        # and is not (the same loop ending in auth_failed).
        exceptions = entry.get("unless") or {}
        if any(
            str(evidence.get(key)) in {str(v) for v in (values if isinstance(values, list) else [values])}
            for key, values in exceptions.items()
        ):
            continue
        return entry
    return None


# -- revisión contra SINA (F2.5) ------------------------------------------


def load_reviews(paths: List[str]) -> Dict[str, dict]:
    """Los códigos refinados de la revisión de identificación, por llamada.

    `run_detectors` solo puede decir `L2-AUTH-001` («no se identificó»), porque
    desde la base de datos no se ve POR QUÉ. La revisión contra SINA sí lo sabe,
    y este informe la aprovecha: donde haya veredicto refinado, sustituye al
    paraguas. Sin esto habría 28 llamadas en una sola fila y ninguna tarea que
    abrir.
    """
    reviews: Dict[str, dict] = {}
    for path in paths:
        try:
            payload = json.load(open(path))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  aviso: no pude leer la revisión {path}: {exc}")
            continue
        for case in payload.get("cases", []):
            if case.get("codes"):
                reviews[case["call_id"]] = case
    return reviews


def apply_reviews(findings: List[dict], reviews: Dict[str, dict]) -> tuple:
    """Cambia el `L2-AUTH-001` de una llamada por lo que dijo la revisión.

    La revisión ya no es solo «lo que vi en la ficha de SINA»: tras la
    reclasificación (`sina/reclassify_auth.py`) cada caso trae de dónde sale su
    veredicto —`proveedor` si lo dijo SINA en el log, `local` si lo paró nuestro
    validador, `inferida` si se dedujo del transcript—, y eso se propaga a la
    evidencia. Que un código sea igual de rojo no significa que se sepa igual
    de bien, y el informe tiene que dejarlo ver.
    """
    out: List[dict] = []
    refined = 0
    for f in findings:
        case = reviews.get(f["conversation_id"])
        if f["code"] != "L2-AUTH-001" or not case:
            out.append(f)
            continue
        refined += 1
        for code in case["codes"]:
            evidence = {
                "revisado_en_sina": True,
                "evidencia": case.get("evidencia", "inferida"),
                "documentos": case.get("documents_tried"),
                "intentos": len(case.get("attempts", [])),
            }
            if case.get("codigos_retirados"):
                evidence["codigos_retirados"] = case["codigos_retirados"]
            for sf in case.get("sina_findings", []):
                if sf.get("code") != code:
                    continue
                for key in ("codigo_del_proveedor", "significado", "cadena",
                            "documentos_rechazados", "llego_al_his",
                            "veredicto_de_la_ficha", "desajustes_de_la_ficha",
                            # `colgo_paciente` decide si el caso es accionable y
                            # es la condición de una entrada de la KEDB: si no
                            # llega hasta aquí, la decisión no se aplica.
                            "colgo_paciente", "turnos"):
                    if sf.get(key) is not None:
                        evidence[key] = sf[key]
                if sf.get("sina"):
                    evidence["ficha"] = sf["sina"]
                if sf.get("agent_sent"):
                    evidence["enviado"] = sf["agent_sent"]
            out.append(
                make_finding(
                    {"call_id": f["conversation_id"], "tenant": f.get("tenant")},
                    code,
                    evidence=evidence,
                    detected_by={
                        "proveedor": "core_log_provider_code",
                        "local": "tool_output_local_validation",
                    }.get(case.get("evidencia"), "sina_record_compare"),
                )
            )
    return out, refined


# -- context --------------------------------------------------------------


def deployment_context(env: str) -> Dict[str, Any]:
    """What was running while these calls happened.

    The deployed image sha per service lives in KubePodInventory (Log
    Analytics), so it shares the access gap with the latency section; the flow
    version per client comes from Supabase and is available. Both report their
    own absence rather than being left out.
    """
    context: Dict[str, Any] = {"env": env, "catalog_version": catalog().version}
    try:
        db = Supabase(env, load_keys())
        tenants = db.select(
            "api_keys",
            columns="id,company_name,voice_mode,default_conversation_flow_id",
            limit=500,
        )
        flows = {}
        flow_ids = [t["default_conversation_flow_id"] for t in tenants if t.get("default_conversation_flow_id")]
        if flow_ids:
            for row in db.select_by_chunks(
                "conversation_flows", column="id", values=flow_ids, columns="id,name,version"
            ):
                flows[row["id"]] = row
        context["tenants"] = [
            {
                "name": t.get("company_name"),
                "voice_mode": t.get("voice_mode"),
                "flow": flows.get(t.get("default_conversation_flow_id")),
            }
            for t in tenants
        ]
    except AccessError as exc:
        context["tenants_error"] = str(exc)
    context["deployed_sha"] = None
    context["deployed_sha_why"] = (
        "KubePodInventory (Log Analytics) — mismo hueco de acceso que la latencia"
    )
    return context


def alert_events(env: str, since: str, until: str) -> Dict[str, Any]:
    """What the existing alert engine fired in the window (§1.4: read, not rebuild)."""
    try:
        db = Supabase(env, load_keys())
        events = db.select(
            "call_alert_events",
            columns="id,metric,match_value,severity,status,observed_count,threshold,window_start,notified_at",
            filters=[f"window_start=gte.{since}", f"window_start=lt.{until}"],
        )
        rules = db.select("call_alert_rules", columns="id,name,metric,is_active", limit=200)
        return {"events": events, "rules": len(rules), "active_rules": sum(1 for r in rules if r.get("is_active"))}
    except AccessError as exc:
        return {"error": str(exc)}


# -- estadísticas por objetivo --------------------------------------------

#: A qué venía la llamada, deducido del `user_intent` que el runtime ya clasifica.
GOALS = {
    "Reserva de cita": ("schedule_appt", "scheduling_for_other_people"),
    "Modificación / cancelación": ("reschedule_appt", "cancel_appt"),
    "Consulta de citas": ("view_appts",),
    "FAQ / información": ("service_inquiry", "doctor_inquiry", "clinic_info",
                          "coverage_inquiry", "clinical_results", "price_inquiry"),
    "Quiere hablar con una persona": ("human_preference",),
}

#: Resultados que cuentan como objetivo cumplido.
SUCCESS_RESULTS = {
    "appt_confirmed", "appt_rescheduled", "appt_canceled", "appts_provided",
    "faq_answer_provided", "auth_success", "info_provided",
}

#: Un `escalation_success` es un éxito del ESCALADO, no del objetivo: la gestión
#: la acabó una persona. Se cuenta en su propia columna para no inflar el éxito.
ESCALATED_RESULTS = {"escalation_success"}


def attribution_of(finding: dict) -> str:
    """De quién es el fallo, según lo que ya dice el catálogo del código."""
    if finding.get("verdict") == "EXPECTED":
        return "usuario"
    if finding.get("verdict") == "INVALID":
        return "ninguno"
    owner = finding.get("owner")
    if owner == "provider":
        return "sina"
    if owner in ("backend", "prompt", "flow", "infra"):
        return "nuestro"
    return "sin_atribuir"


def goal_of(bundle: dict) -> str:
    intent = bundle.get("user_intent")
    for goal, intents in GOALS.items():
        if intent in intents:
            return goal
    return "Otros / sin intent" if intent else "Sin intent registrado"


def call_outcome(bundle: dict, findings: List[dict]) -> str:
    """Éxito, escalado, o fallo con su atribución."""
    result = bundle.get("call_result")
    if result in SUCCESS_RESULTS:
        return "éxito"
    real = [f for f in findings if f.get("verdict") == "FAIL"]
    if real:
        # El peor de la pila decide a quién se le atribuye la llamada.
        worst = sorted(real, key=lambda f: -severity_rank(f))[0]
        return f"fallo:{attribution_of(worst)}"
    if result in ESCALATED_RESULTS:
        return "escalado"
    expected = [f for f in findings if f.get("verdict") == "EXPECTED"]
    if expected:
        return "fallo:usuario"
    return "sin clasificar"


#: Transferencia por hastío: ni la pidió el usuario ni hubo fallo detectado, y la
#: llamada fue larga. Es una HEURÍSTICA declarada, no una medición — la pregunta
#: «¿se aburrió?» es semántica y la contesta el juez de F4. Los umbrales salen del
#: p75 de la ventana, no de un número inventado.
TEDIUM_MIN_TURNS = 8


def transfer_reasons(bundles: List[dict], findings_by_call: Dict[str, List[dict]]) -> Dict[str, List[dict]]:
    transferred = [b for b in bundles if b["status"] == "transferred"]
    durations = sorted(b["duration_s"] or 0 for b in transferred)
    p75 = durations[int(len(durations) * 0.75)] if durations else 0
    buckets: Dict[str, List[dict]] = defaultdict(list)
    for bundle in transferred:
        findings = findings_by_call.get(bundle["call_id"], [])
        failed = [f for f in findings if f.get("verdict") == "FAIL"]
        if bundle.get("user_intent") == "human_preference":
            buckets["la pidió el usuario"].append(bundle)
        elif failed:
            worst = sorted(failed, key=lambda f: -severity_rank(f))[0]
            buckets[f"consecuencia de un fallo ({attribution_of(worst)})"].append(bundle)
        elif (bundle["turns"]["patient"] >= TEDIUM_MIN_TURNS
              and (bundle["duration_s"] or 0) >= p75):
            buckets["candidata a hastío (heurística)"].append(bundle)
        else:
            buckets["sin causa detectada"].append(bundle)
    return buckets


# -- incidentes sistémicos ------------------------------------------------

#: Cuántas llamadas del mismo (código, tool, cliente) hacen falta para llamarlo
#: incidente en vez de N casos. Con el volumen de un día (~600 llamadas) diez es
#: mucho más que ruido, y por debajo de eso el triaje caso por caso vale.
INCIDENT_MIN_CALLS = 10

#: …o que se lleve esta parte de las llamadas del cliente en la ventana. Es la
#: regla de alarma del workplan (§2.4: «≥3 llamadas o ≥25% de la ventana con el
#: mismo código»), bajada a 20% para no perder una caída parcial.
INCIDENT_MIN_SHARE = 0.20


def incidents(findings: List[dict], bundles: Dict[str, dict]) -> List[dict]:
    """Grupos que son UN incidente, no muchos casos.

    La primera versión del informe habría contado el corte de SINA del 25 de
    agosto como «100 llamadas con error de tool», que es cierto y no sirve de
    nada: lo que hay que ver es que era **una** tool, **un** cliente y **todo el
    día**. Se agrupa por (código, tool, cliente) porque es la tripleta que
    identifica una causa común, y se reporta con su ventana horaria.
    """
    groups: Dict[tuple, List[dict]] = defaultdict(list)
    for f in findings:
        evidence = f.get("evidence") or {}
        tool = evidence.get("tool") or "—"
        groups[(f["code"], tool, f.get("tenant"))].append(f)

    # Cuántas llamadas atendió cada cliente en la ventana, para poder hablar de
    # proporción y no solo de volumen: 10 casos de latencia en un día son ruido,
    # 96 fallos de una tool sobre 261 llamadas son una caída.
    per_tenant_calls = Counter(b.get("tenant") for b in bundles.values())

    out = []
    for (code, tool, tenant), group in groups.items():
        calls = {f["conversation_id"] for f in group}
        if len(calls) < INCIDENT_MIN_CALLS:
            continue
        if not is_actionable(group[0]):
            continue  # que 15 pacientes cuelguen en el saludo no es un incidente
        messages = {" ".join(str((f.get("evidence") or {}).get("error") or "").split())
                    for f in group}
        messages.discard("")
        identical = len(messages) == 1
        share = len(calls) / max(1, per_tenant_calls.get(tenant, 0))
        if not identical and share < INCIDENT_MIN_SHARE:
            continue
        times = sorted(
            (bundles[c]["started_at"] for c in calls if c in bundles and bundles[c].get("started_at"))
        )
        sample = sorted(calls)[:3]
        out.append({
            "code": code, "tool": tool, "tenant": tenant,
            "calls": len(calls), "findings": len(group),
            "from": times[0] if times else None, "to": times[-1] if times else None,
            "share": share, "identical": identical,
            "sample": sample,
            "message": next((f["evidence"].get("error") for f in group
                             if (f.get("evidence") or {}).get("error")), None),
        })
    return sorted(out, key=lambda i: -i["calls"])


# -- rendering ------------------------------------------------------------


def _pct(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:.0f}%" if whole else "—"


def render(
    findings_payload: dict,
    bundles_payload: Optional[dict],
    latency_payload: Optional[dict],
    context: Dict[str, Any],
    alerts: Dict[str, Any],
    previous: Optional[dict],
    kedb: Optional[List[dict]] = None,
    show_hidden: bool = False,
    reviews: Optional[Dict[str, dict]] = None,
    tasks: Optional[Dict[str, List[dict]]] = None,
) -> str:
    kedb = kedb or []
    tasks = tasks if tasks is not None else load_tasks()
    meta = findings_payload["meta"]
    results = findings_payload["results"]
    funnel = findings_payload["funnel"]
    queue = findings_payload.get("judge_queue", [])
    residue = findings_payload.get("residue", [])
    every_finding = [f for r in results for f in r["findings"]]
    bundles = {b["call_id"]: b for b in (bundles_payload or {}).get("bundles", [])}

    # A decided case leaves the tables and goes to the counter in §3b. `watch`
    # decides nothing, so it keeps showing.
    refined = 0
    if reviews:
        every_finding, refined = apply_reviews(every_finding, reviews)

    hidden: List[tuple] = []
    all_findings = []
    # `watch` no oculta: anota. Se guarda qué llamadas caza cada entrada para
    # marcarlas en la hoja de trabajo y listar la anotación en §3c — si una
    # decisión no se ve en el informe, no existe.
    watched: Dict[str, dict] = {}
    watch_hits: Dict[str, List[str]] = {}
    for finding in every_finding:
        entry = kedb_match(finding, kedb)
        if entry and entry.get("decision") in ("normal", "known_bug", "deferred"):
            hidden.append((finding, entry))
            continue
        if entry and entry.get("decision") == "watch":
            watched[entry["id"]] = entry
            watch_hits.setdefault(entry["id"], []).append(finding["conversation_id"])
            finding["_watch"] = entry["id"]
        all_findings.append(finding)

    lines: List[str] = []
    add = lines.append

    # ── 1. cabecera ──────────────────────────────────────────────────────
    add(f"# Informe de producción — {meta['env']}")
    add("")
    add(f"*Ventana `{meta['since']}` → `{meta['until']}` (UTC, fin exclusivo). "
        f"Generado {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}Z.*")
    add("")
    add("## 1. Contexto de versiones")
    add("")
    add(f"- **Catálogo de códigos**: `{context['catalog_version']}`")
    add(f"- **Sha desplegado por servicio**: no disponible — {context['deployed_sha_why']}")
    if "tenants_error" in context:
        add(f"- **Clientes**: no legibles ({context['tenants_error'][:120]})")
    else:
        workflow = [t for t in context["tenants"] if t["voice_mode"] != "standard"]
        add(f"- **Clientes**: {len(context['tenants'])} tenants; "
            f"{len(workflow)} en modo workflow, {len(context['tenants']) - len(workflow)} legacy")
        for tenant in workflow:
            flow = tenant["flow"] or {}
            add(f"    - {tenant['name']}: flow `{flow.get('name', '?')}` v{flow.get('version', '?')}")
        if not workflow:
            add("    - *ningún cliente corre workflow aquí, así que no hay versión de flow que "
                "estampar ni camino de nodos que auditar (§1.1)*")
    add("")

    # ── 2. embudo ────────────────────────────────────────────────────────
    add("## 2. Embudo por capas")
    add("")
    add("> La calidad semántica solo se reporta sobre el universo válido: una llamada que falla "
        "en L1 no dice nada sobre el agente, y contarla como fallo semántico es el error de "
        "atribución que este diseño existe para evitar.")
    add("")
    add("| Cliente | Llamadas | Inválidas (L1) | Pasa L1 | Pasa L2 | Pasa L3 |")
    add("|---|---:|---:|---:|---:|---:|")
    for name in ["ALL"] + sorted(k for k in funnel if k != "ALL"):
        row = funnel[name]
        calls = row.get("calls", 0)
        invalid = row.get("invalid", 0)
        valid = calls - invalid
        l1, l2 = row.get("passed_l1", 0), row.get("passed_l2", 0)
        label = "**TOTAL**" if name == "ALL" else name
        add(f"| {label} | {calls} | {invalid} | {l1} ({_pct(l1, valid)}) | "
            f"{l2} ({_pct(l2, valid)}) | — |")
    add("")
    add("- **Pasa L3: no evaluado.** El juez semántico es F4 y todavía no existe; "
        + (f"la llamada que le tocaría está en §6." if len(queue) == 1
           else f"las {len(queue)} llamadas que le tocarían están en §6."))
    add("")

    # ── 2b. estadísticas por objetivo ────────────────────────────────────
    add("## 2b. Estadísticas por objetivo y atribución")
    add("")
    add("> Para qué llamaba la gente y cómo acabó, con el fallo atribuido a quien le "
        "corresponde. **La columna «usuario» no es accionable pero sí informa**: si crece, "
        "puede que haya algo que ofrecerles (avisar antes, pedir otro dato, o hablar con el "
        "cliente de los teléfonos de sus fichas).")
    add("")
    if not bundles:
        add("*Hace falta `--bundles` para esta sección.*")
    else:
        findings_by_call: Dict[str, List[dict]] = defaultdict(list)
        for f in all_findings + [h[0] for h in hidden]:
            findings_by_call[f["conversation_id"]].append(f)

        rows: Dict[str, Counter] = defaultdict(Counter)
        for bundle in bundles.values():
            rows[goal_of(bundle)][call_outcome(bundle, findings_by_call.get(bundle["call_id"], []))] += 1

        columns = ["éxito", "escalado", "fallo:nuestro", "fallo:sina", "fallo:usuario",
                   "fallo:sin_atribuir", "sin clasificar"]
        labels = {"éxito": "✅ éxito", "escalado": "↪ lo acabó una persona",
                  "fallo:nuestro": "🔧 fallo nuestro", "fallo:sina": "🏥 fallo de SINA",
                  "fallo:usuario": "👤 dato del usuario", "fallo:sin_atribuir": "❓ sin atribuir",
                  "sin clasificar": "· sin clasificar"}
        add("| Objetivo | n | " + " | ".join(labels[c] for c in columns) + " |")
        add("|---|---:|" + "---:|" * len(columns))
        totals = Counter()
        for goal in sorted(rows, key=lambda g: -sum(rows[g].values())):
            counts = rows[goal]
            totals.update(counts)
            add(f"| {goal} | {sum(counts.values())} | "
                + " | ".join(str(counts.get(c, 0) or "·") for c in columns) + " |")
        add(f"| **TOTAL** | **{sum(totals.values())}** | "
            + " | ".join(f"**{totals.get(c, 0)}**" for c in columns) + " |")
        add("")
        add("*«Lo acabó una persona» (`escalation_success`) es éxito del ESCALADO, no del "
            "objetivo: se cuenta aparte para no inflar la tasa de éxito. Un `auth_failed` que "
            "acaba en transferencia cuenta como fallo, con su atribución.*")
        add("")

        add("### Transferencias, por causa")
        add("")
        buckets = transfer_reasons(list(bundles.values()), findings_by_call)
        total_t = sum(len(v) for v in buckets.values())
        add(f"De {total_t} llamadas transferidas:")
        add("")
        for reason in sorted(buckets, key=lambda r: -len(buckets[r])):
            calls = buckets[reason]
            tenants = Counter(b["tenant"] for b in calls)
            add(f"- **{reason}: {len(calls)}** ({_pct(len(calls), total_t)}) — "
                + ", ".join(f"{t} {n}" for t, n in tenants.most_common(3)))
        add("")
        # El origen de la llamada, medido en cada pasada: un número oculto
        # llegaría con el hueco del teléfono vacío en `room_name`, y la hipótesis
        # de que eso explique los fallos de transferencia solo se puede sostener
        # o descartar con el contador delante.
        formats = Counter(_caller_format(b.get("room_name")) for b in bundles.values())
        anon = formats.get("oculto o ausente", 0) + formats.get("oculto (anonymous)", 0)
        add(f"- **formato del número que presenta el trunk**: "
            + ", ".join(f"{fmt} {n}" for fmt, n in formats.most_common())
            + f" · **oculto o ausente: {anon}**")
        add("")
        if not anon:
            add("*Ningún room delata un número oculto en esta ventana. **Cuidado con leer eso "
                "como que no hubo ninguna**: el room solo conserva `anonymous` a veces, y el "
                "aviso fiable está en el log de voice "
                "(`Inbound SIP call with hidden/unknown caller number`). Esas llamadas van por "
                "una vía que NO valida al paciente y se quedan sin `end_user_id`.*")
            add("")
            add("*Dos avisos sobre este dato: `calls` no tiene columna de teléfono ni de "
                "`call_source` (no existe en prod), así que esto sale del `room_name`; y ese "
                "número es el que **presenta el trunk**, no necesariamente el del paciente — en "
                "Publio Cordón es constante porque es su centralita, mientras los pacientes son "
                "distintos (`end_users.phone`).*")
            add("")
        add(f"*«Candidata a hastío» es una HEURÍSTICA declarada, no una medición: ni la pidió el "
            f"usuario ni se detectó fallo, y la llamada pasó de {TEDIUM_MIN_TURNS} turnos del "
            "paciente estando por encima del p75 de duración. La pregunta «¿se aburrió?» es "
            "semántica y la contesta el juez de F4; hasta entonces esto solo dice dónde mirar.*")
        add("")

    # ── 3. tabla de fallos ───────────────────────────────────────────────
    found_incidents = incidents(all_findings, bundles) if bundles else []
    if found_incidents:
        add("## 2c. 🚨 Incidentes sistémicos")
        add("")
        add("> Un mismo código, en una misma tool y un mismo cliente, en "
            f"**{INCIDENT_MIN_CALLS}+ llamadas** de la ventana. Eso no son N casos que "
            "triar: es UN incidente, y se mira antes que nada.")
        add("")
        for inc in found_incidents:
            span = ""
            if inc["from"] and inc["to"]:
                span = f" · desde `{inc['from'][11:16]}` hasta `{inc['to'][11:16]}` UTC"
            add(f"- **`{inc['code']}` · {inc['tenant']} · tool `{inc['tool']}`: "
                f"{inc['calls']} llamadas** ({inc['share']:.0%} de las de ese cliente){span}")
            if inc["message"]:
                add(f"    - mensaje idéntico en todas: `{' '.join(str(inc['message']).split())[:160]}`")
            add("    - ejemplos: " + " ".join(call_link(meta["env"], c) for c in inc["sample"]))
        add("")
        add("*Una tool, un cliente y toda la franja horaria es la firma de una caída o una "
            "configuración mal puesta, no de 100 pacientes con mala suerte. Los casos siguen "
            "listados abajo, pero la tarea es una.*")
        add("")

    add("## 3. Fallos detectados")
    add("")
    actionable = [f for f in all_findings if is_actionable(f)]
    not_actionable = [f for f in all_findings if not is_actionable(f)]
    add(f"> ### 🔧 {len(actionable)} hallazgos ACCIONABLES "
        f"· ⚪ {len(not_actionable)} no accionables")
    add(">")
    add("> **Accionable** = hay algo que arreglar de nuestro lado, así que puede convertirse en "
        "tarea. **No accionable** = el resultado es el correcto dado el dato que había "
        "(`EXPECTED`) o la llamada no dice nada del agente (`INVALID`); se cuentan aparte pero "
        "**no se ocultan**, porque su volumen sí informa.")
    if refined:
        add(">")
        add(f"> {refined} llamada(s) que la base de datos solo podía clasificar como "
            "«no se identificó» llevan aquí el veredicto de la revisión contra SINA, que sí dice "
            "de quién es el problema.")
    add("")
    if not all_findings:
        add("*Ninguno. Con los detectores deterministas actuales — ver §7 para lo que NO se "
            "evalúa todavía.*")
    else:
        by_code: Dict[str, List[dict]] = defaultdict(list)
        for finding in all_findings:
            by_code[finding["code"]].append(finding)
        add("| | Acción | Código | Capa | n | Llamadas | Clientes | Dueño | Ejemplos |")
        add("|---|---|---|---|---:|---:|---|---|---|")
        for code, group in sorted(
            by_code.items(), key=lambda kv: (-max(severity_rank(f) for f in kv[1]), -len(kv[1]))
        ):
            worst = max(group, key=severity_rank)
            calls = {f["conversation_id"] for f in group}
            tenants = Counter(f["tenant"] for f in group)
            examples = " ".join(call_link(meta["env"], c) for c in sorted(calls)[:3])
            mark = "**🔧 SÍ**" if is_actionable(worst) else "⚪ no"
            add(f"| {SEVERITY_ICON[worst['severity']]} | {mark} | `{code}` | {worst['layer']} | "
                f"{len(group)} | {len(calls)} | {', '.join(f'{t} {n}' for t, n in tenants.most_common(3))} | "
                f"{worst['owner']} | {examples} |")
        add("")
        add(f"*Severidad del peor caso de cada código: {' '.join(f'{v} {k}' for k, v in SEVERITY_ICON.items())}. "
            "La severidad es por ocurrencia (impacto × recuperabilidad), no por capa.*")
        add("")
        add("### Casos, uno por línea")
        add("")
        add("> Hoja de triaje: cada caso enlaza a la llamada en la consola. La decisión de cada "
            "uno es **o abrir tarea, o una entrada en `known_errors.yaml`** que lo dé por normal "
            "o conocido — y entonces deja de aparecer aquí y pasa al contador de ocultos (§3b).")
        add("")
        for code, group in sorted(
            by_code.items(),
            key=lambda kv: (not is_actionable(kv[1][0]),
                            -max(severity_rank(f) for f in kv[1]), -len(kv[1])),
        ):
            entry = catalog().entry(code)
            mark = "🔧 ACCIONABLE" if is_actionable(group[0]) else "⚪ NO ACCIONABLE"
            add(f"#### {mark} · `{code}` — {entry['title']} ({len(group)})")
            add("")
            add(f"{entry['definition'].strip()}")
            add("")
            add(f"*Capa {entry['layer']} · dueño por defecto `{entry['owner']}` · "
                f"veredicto `{entry.get('verdict', 'FAIL')}`"
                + (f" · reemplaza a `{entry['replaced_by']}`" if entry.get("replaced_by") else "")
                + "*")
            # Lo que convierte la ficha en algo verificable: contra qué rama se
            # comprobó el razonamiento, y qué línea de log lo confirma. Sin esto,
            # el informe pide creer en el análisis en vez de poder repetirlo.
            detection = entry.get("detection") or {}
            if detection.get("verified_in"):
                add("")
                add(f"- **Verificado en**: {' '.join(detection['verified_in'].split())}")
            if detection.get("confirm_in_log"):
                add("")
                add(f"- **Para confirmarlo en el log**: {' '.join(detection['confirm_in_log'].split())}")
            add("")
            per_tenant: Dict[str, List[dict]] = defaultdict(list)
            for finding in group:
                per_tenant[finding["tenant"] or "sin cliente"].append(finding)
            for tenant in sorted(per_tenant, key=lambda t: (-len(per_tenant[t]), t)):
                findings = sorted(per_tenant[tenant], key=lambda f: f["conversation_id"])
                add(f"**{tenant}** ({len(findings)})")
                add("")
                for finding in findings:
                    call_id = finding["conversation_id"]
                    bundle = bundles.get(call_id)
                    facts = []
                    if bundle:
                        duration = bundle.get("duration_s")
                        facts.append(f"{duration:.0f}s" if isinstance(duration, (int, float)) else "?s")
                        facts.append(f"{bundle['status']}/{bundle['call_result']}")
                        facts.append(f"{bundle['turns']['patient']} turnos")
                    note = _finding_note(finding)
                    if note:
                        facts.append(note)
                    icon = SEVERITY_ICON[finding["severity"]]
                    mark = "🔧" if is_actionable(finding) else "⚪"
                    if finding.get("_watch"):
                        mark += "👁"
                    add(f"- [ ] {mark} {icon} {call_link(meta['env'], call_id)} · " + " · ".join(facts))
                add("")

    # ── 3b. casos ocultos por la KEDB ────────────────────────────────────
    add("## 3b. Casos ya decididos (ocultos)")
    add("")
    if not kedb:
        add("*`known_errors.yaml` no tiene entradas todavía: no se está ocultando nada. "
            "Cada caso de §3 que NO merezca tarea debería acabar aquí — es lo que hace que "
            "el informe del día siguiente sea más corto y no el mismo ruido.*")
    elif not hidden:
        add(f"*{len(kedb)} entrada(s) en la KEDB, ninguna coincide con esta ventana.*")
    else:
        by_entry: Dict[str, List[dict]] = defaultdict(list)
        entries_by_id = {}
        for finding, entry in hidden:
            by_entry[entry["id"]].append(finding)
            entries_by_id[entry["id"]] = entry
        add(f"**{len(hidden)} hallazgos ocultos** por {len(by_entry)} decisión(es). "
            "No son casos resueltos: `normal` dice que contarlos era ruido, `known_bug` que "
            "ya están en Jira.")
        add("")
        for entry_id, group in sorted(by_entry.items()):
            entry = entries_by_id[entry_id]
            jira = f" · {entry['jira']}" if entry.get("jira") else ""
            add(f"- `{entry_id}` ({entry.get('decision')}{jira}) — {len(group)} casos de "
                f"`{entry['code']}`: {entry.get('why', '').strip()}")
            if show_hidden:
                for finding in sorted(group, key=lambda f: f["conversation_id"]):
                    add(f"    - {call_link(meta['env'], finding['conversation_id'])} "
                        f"({finding['tenant']}) {_finding_note(finding)}")
        if not show_hidden:
            add("")
            add("*`report.py --show-hidden` los lista uno por uno.*")

    # ── 3d. accionables sin tarea y sin decisión ──────────────────────────
    # La pregunta que cierra el triaje. Un hallazgo accionable sin tarea abierta
    # y sin decisión en la KEDB es exactamente lo que se cae entre las grietas:
    # sale rojo en el informe cada día y nadie se hace cargo.
    add("")
    add("## 3d. Accionables sin tarea ni decisión")
    add("")
    orphans: Dict[str, List[dict]] = {}
    for finding in all_findings:
        if not is_actionable(finding):
            continue
        if task_for(finding, tasks) or finding.get("_watch"):
            continue
        orphans.setdefault(finding["code"], []).append(finding)
    if not orphans:
        add("*Ninguno: todo lo accionable tiene tarea abierta o decisión registrada.*")
        add("")
    else:
        n_findings = sum(len(v) for v in orphans.values())
        add(f"**{n_findings} hallazgo{'s' if n_findings != 1 else ''} "
            f"en {len(orphans)} código{'s' if len(orphans) != 1 else ''}** "
            "sin tarea en `tasks.yaml` ni entrada en `known_errors.yaml`. "
            "O se abre tarea, o se escribe la decisión de por qué no.")
        add("")
        add("| código | hallazgos | llamadas | dueño | qué es |")
        add("|---|---:|---:|---|---|")
        for code, group in sorted(orphans.items(), key=lambda kv: -len(kv[1])):
            entry = catalog().entry(code)
            calls = len({f["conversation_id"] for f in group})
            add(f"| `{code}` | {len(group)} | {calls} | {entry['owner']} | {entry['title']} |")
        add("")
    covered = []
    for code, entries in sorted(tasks.items()):
        for task in entries:
            scope = ""
            if task.get("tenant"):
                scope = f" ({task['tenant']})"
            elif task.get("calls"):
                scope = f" ({len(task['calls'])} llamada(s))"
            covered.append(f"`{code}`{scope} → {task['jira']}")
    add("*Con tarea abierta: " + ", ".join(covered) + ".*")
    add("")

    # ── 3c. anotaciones: deciden cómo leer un caso, no si se ve ───────────
    add("")
    add("## 3c. Anotaciones (no ocultan nada)")
    add("")
    if not watched:
        add("*Ninguna entrada `watch` en `known_errors.yaml`.*")
    else:
        add("Decisiones que **no** son «abrir tarea» ni «es ruido»: contexto que "
            "cambia cómo se lee un caso. Los casos afectados van marcados con 👁 "
            "en la hoja de trabajo.")
        add("")
        for entry_id, entry in sorted(watched.items()):
            hits = watch_hits.get(entry_id, [])
            add(f"### 👁 `{entry_id}` — `{entry['code']}` ({len(hits)} casos)")
            add("")
            add(entry.get("why", "").strip())
            if entry.get("action"):
                add("")
                add(f"**Acción que se propone**: {entry['action'].strip()}")
            add("")
    add("")

    # ── 4. latencia ──────────────────────────────────────────────────────
    add("## 4. Latencia por turno")
    add("")
    if not latency_payload:
        add("**No disponible.** `E2E time` solo existe en los logs, y el workspace de Log "
            "Analytics de este entorno no es accesible (F0-2: `InsufficientAccessError`). "
            "El resto del informe no depende de ello.")
    else:
        turns = latency_payload["turns"]
        add(f"*{len(turns)} turnos, de {latency_payload['meta']['rows']} líneas de log "
            f"({latency_payload['meta']['env']}).* Percentiles, nunca medias: la cola es larga.")
        add("")
        add("| Corte | n | p50 | p90 | p95 | >1,5 s | e2e−tools p50 |")
        add("|---|---:|---:|---:|---:|---:|---:|")
        groups: Dict[str, List[dict]] = {"todos": turns}
        for kind in ("response", "transition"):
            groups[f"turnos de {kind}"] = [t for t in turns if t["type"] == kind]
        for client in sorted({t["client"] for t in turns if t["attribution"] != "none"}):
            groups[client] = [t for t in turns if t["client"] == client]
        for label, group in groups.items():
            if not group:
                continue
            e2e = sorted(t["e2e_s"] for t in group)
            net = sorted(t["e2e_minus_tools_s"] for t in group)
            pick = lambda values, q: values[min(len(values) - 1, int(round(q * (len(values) - 1))))]  # noqa: E731
            slow = sum(1 for v in e2e if v > 1.5)
            add(f"| {label} | {len(group)} | {pick(e2e, 0.5):.2f} | {pick(e2e, 0.9):.2f} | "
                f"{pick(e2e, 0.95):.2f} | {_pct(slow, len(group))} | {pick(net, 0.5):.2f} |")
        add("")
        worst = sorted(turns, key=lambda t: -t["e2e_s"])[:3]
        add("Peores turnos: " + " · ".join(
            f"{t['e2e_s']:.1f}s ({t['client']}, tools {t['tool_s']:.1f}s)" for t in worst
        ))
        add("")
        add("*Sin umbrales todavía: el baseline propio es p50 1,8 s / p95 4,3 s, así que un SLA "
            "de 800 ms saldría todo rojo. Dos semanas de datos y luego un SLA versionado.*")
    add("")

    # ── 5. motor de alertas ──────────────────────────────────────────────
    add("## 5. Alertas del motor existente")
    add("")
    if "error" in alerts:
        add(f"*No legible: {alerts['error'][:150]}*")
    else:
        events = alerts["events"]
        add(f"- Reglas cargadas: {alerts['rules']} ({alerts['active_rules']} activas)")
        add(f"- Eventos disparados en la ventana: **{len(events)}**")
        for event in events[:10]:
            add(f"    - `{event['metric']}` {event.get('match_value')} — "
                f"{event.get('observed_count')}/{event.get('threshold')} "
                f"({event.get('severity')}, {event.get('status')})")
        if not events:
            add("    - *cero. Si además `call_alert_events` está vacía en total, el scanner no "
                "está corriendo en este entorno (§1.4) — y entonces este informe es la única "
                "vigilancia que hay.*")
    add("")

    # ── 6. residuo y juez ────────────────────────────────────────────────
    add("## 6. Residuo y cola del juez")
    add("")
    add(f"**Residuo — resultado malo sin código que lo explique: {len(residue)} llamadas.** "
        "Es la cola que hace crecer el catálogo: agrupar y, con ≥3 casos en 7 días, proponer "
        "un código nuevo (aprobación humana).")
    if residue:
        add("")
        by_tenant: Dict[str, List[dict]] = defaultdict(list)
        for row in residue:
            by_tenant[row["tenant"] or "sin cliente"].append(row)
        for tenant in sorted(by_tenant, key=lambda t: (-len(by_tenant[t]), t)):
            add(f"**{tenant}** ({len(by_tenant[tenant])})")
            add("")
            for row in sorted(by_tenant[tenant], key=lambda r: r["call_result"] or ""):
                add(f"- [ ] {call_link(meta['env'], row['call_id'])} · "
                    f"`{row['call_result']}` · intent `{row['user_intent']}`")
            add("")
    add("")
    add(f"**Cola del juez: {len(queue)} llamadas** (presupuesto 60/ventana).")
    if queue:
        add("")
        for reason, n in Counter(r for q in queue for r in q["reasons"]).most_common():
            add(f"- `{reason}` ×{n}")
        add("")
        add("*Con el juez de F4 esta cola produciría los códigos L3 y la columna «pasa L3» de §2.*")
    add("")

    # ── 7. parte de bugs ─────────────────────────────────────────────────
    add("## 7. Parte de bugs (frecuencia × severidad)")
    add("")
    weights = {"critical": 8, "high": 4, "medium": 2, "low": 1}
    scored = []
    by_code = defaultdict(list)
    for finding in all_findings:
        by_code[finding["code"]].append(finding)
    for code, group in by_code.items():
        entry = catalog().entry(code)
        if not is_actionable({"verdict": entry.get("verdict", "FAIL")}):
            continue  # EXPECTED/INVALID no generan tarea (§3 los cuenta aparte)
        calls = len({f["conversation_id"] for f in group})
        score = calls * max(weights[f["severity"]] for f in group)
        scored.append((score, code, calls, entry, group))
    if not scored:
        add("*Nada que abrir.*")
    else:
        for score, code, calls, entry, group in sorted(scored, reverse=True):
            tenants = Counter(f["tenant"] for f in group)
            add(f"1. **`{code}`** ({entry['owner']}) — {calls} llamadas, prioridad {score}. "
                f"{entry['title']}. Concentrado en: "
                f"{', '.join(f'{t} ({n})' for t, n in tenants.most_common(2))}.")
    add("")
    if kedb:
        add(f"**KEDB**: {len(kedb)} entrada(s) — lo ya decidido está contado en §3b.")
    else:
        add("**KEDB**: `known_errors.yaml` existe pero sin entradas. Hasta que las tenga, el "
            "informe no puede separar «nuevo» de «conocido con workaround», y todo lo de §3 "
            "sigue pidiendo decisión.")
    add("")

    # ── 8. tendencia ─────────────────────────────────────────────────────
    add("## 8. Tendencia")
    add("")
    if not previous:
        add("*Primera ejecución para este entorno: no hay con qué comparar. `state.json` "
            "guarda ya los contadores por código para la siguiente.*")
    else:
        add(f"Frente a la ejecución anterior (hasta `{previous['until']}`, {previous['calls']} llamadas):")
        add("")
        current_counts = Counter(f["code"] for f in all_findings)
        for code in sorted(set(current_counts) | set(previous["codes"])):
            now, before = current_counts.get(code, 0), previous["codes"].get(code, 0)
            arrow = "→" if now == before else ("↑" if now > before else "↓")
            add(f"- `{code}`: {before} {arrow} {now}")
    add("")

    # ── 9. lo que no se evalúa ───────────────────────────────────────────
    add("## 9. Lo que este informe NO evalúa")
    add("")
    import detect_l1
    import detect_l2

    for module in (detect_l1, detect_l2):
        for name, why in module.UNAVAILABLE.items():
            add(f"- **{name}** — {why}")
    add("- **capa L3 completa** — el juez semántico es F4; sin él, «pasa L2» NO significa "
        "«llamada correcta», solo «nada determinista falló».")
    add("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("findings", help="output of run_detectors.py")
    parser.add_argument("--bundles", help="output of fetch_bundles.py (for call detail)")
    parser.add_argument("--latency", help="output of latency_report.py --out")
    parser.add_argument("--out", help="write the report here (default: stdout)")
    parser.add_argument("--no-state", action="store_true", help="do not touch state.json")
    parser.add_argument("--kedb", default=KEDB_PATH, help="path to known_errors.yaml")
    parser.add_argument("--review", action="append", default=[],
                        help="salida de sina/review_auth.py (repetible, uno por cliente)")
    parser.add_argument(
        "--show-hidden",
        action="store_true",
        help="list the cases the KEDB hides, one by one, with the decision that hid them",
    )
    args = parser.parse_args()

    with open(args.findings) as handle:
        findings_payload = json.load(handle)
    bundles_payload = None
    if args.bundles:
        with open(args.bundles) as handle:
            bundles_payload = json.load(handle)
    latency_payload = None
    if args.latency:
        with open(args.latency) as handle:
            latency_payload = json.load(handle)

    meta = findings_payload["meta"]
    context = deployment_context(meta["env"])
    alerts = alert_events(meta["env"], meta["since"], meta["until"])

    state = load_state()
    counts = Counter(f["code"] for r in findings_payload["results"] for f in r["findings"])
    previous = remember(state, meta["env"], meta["until"], dict(counts), len(findings_payload["results"]))
    if not args.no_state:
        save_state(state)

    text = render(
        findings_payload,
        bundles_payload,
        latency_payload,
        context,
        alerts,
        previous,
        kedb=load_kedb(args.kedb),
        show_hidden=args.show_hidden,
        reviews=load_reviews(args.review),
        tasks=load_tasks(),
    )
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w") as handle:
            handle.write(text)
        print(f"written: {args.out} ({len(text.splitlines())} lines)")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
