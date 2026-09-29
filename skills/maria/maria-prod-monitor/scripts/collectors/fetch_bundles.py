#!/usr/bin/env python3
"""One "call bundle" per call in a window: everything the DB knows about it.

This is step 1 of the pipeline (workplan §2.3): a deterministic, read-only
gather that every later stage reads instead of hitting Supabase again. A bundle
is deliberately *derived* — durations, turn counts, tool outcomes, silence gaps,
the node path — so the detectors are assertions over small facts rather than
re-implementations of this parsing.

Privacy: transcript text is NOT included unless `--with-transcript` is passed.
Everything else here is metadata. The judge stage is what needs the words.

Usage:
    python3 fetch_bundles.py --env prod --hours 24
    python3 fetch_bundles.py --env prod --hours 24 --tenant HCB --out bundles.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from _supabase import AccessError, Supabase, load_keys, parse_ts, window

# `PREMIUM EXTERNAL ROUTING` is a routing line, not an assistant: every call is
# `service_off` by design and it would swamp every rate we compute (workplan
# §3.1). Excluded unless explicitly asked for.
ROUTING_TENANTS = {"PREMIUM EXTERNAL ROUTING"}

# A conversation is not a call to the assistant if it is one of ours. In stg/dev
# `calls.call_source` says so; prod has no such column (checked 2026-08-31), so
# the room name is the only signal there.
EVAL_ROOM_MARKERS = ("ai_generated", "playground", "test-", "evals")

# `finish-stuck-calls` (cron, every 2 min) flips any `in_progress` call whose
# row has not been touched for 5 minutes to `failure` and stamps `ended_at =
# now()`. So a janitorial failure ends *at least* five minutes after the last
# thing that happened in it, while a real failure ends right on top of it.
JANITOR_SILENCE_S = 270.0

# Silence between two consecutive utterances that is long enough to be a
# problem rather than a pause. Deliberately generous: this is an L1 candidate
# signal, not a verdict. Only gaps *after the patient spoke* count — a gap
# after the agent is the caller thinking, which is not a defect.
SILENCE_GAP_S = 12.0

# `call_timeline.speaker` (checked against prod 2026-08-31). Not "user".
PATIENT_SPEAKER = "patient"
AGENT_SPEAKER = "agent"

TERMINAL_SYSTEM_EVENTS = {"patient_hangup", "disconnect_finalize"}

CALL_COLUMNS = (
    "id,api_key_id,room_name,call_direction,status,user_intent,call_result,"
    "transfer_reason,transfer_failure_reason,acceptance,summary,"
    "created_at,started_at,ended_at,updated_at,questionnaire_instance_id"
)

TIMELINE_COLUMNS = (
    "call_id,type,occurred_at,speaker,content,system_event,system_metadata,"
    "tool_name,tool_status,tool_error,tool_output,"
    "model,prompt_tokens,completion_tokens"
)


def looks_like_eval(room_name: Optional[str]) -> bool:
    room = (room_name or "").lower()
    return any(marker in room for marker in EVAL_ROOM_MARKERS)


def _seconds(start: Optional[str], end: Optional[str]) -> Optional[float]:
    a, b = parse_ts(start), parse_ts(end)
    if not a or not b:
        return None
    return round((b - a).total_seconds(), 3)


def _has_column(rows: List[dict], column: str) -> bool:
    return bool(rows) and column in rows[0]


def _as_text(value: Any, limit: int = 2000) -> Optional[str]:
    """A jsonb column as text a regex can be run over.

    `tool_output` / `tool_error` are jsonb: sometimes a string, sometimes an
    object (`{"errors": [{"errorCode": "EXT-PAP-00007"}]}`). The detectors match
    on codes, so both shapes become one truncated string here rather than in
    every detector.
    """
    if value is None or value == "":
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text[:limit]


def build_bundle(
    call: dict,
    timeline: List[dict],
    runs: List[dict],
    transitions: List[dict],
    tenant: Optional[str],
    *,
    with_transcript: bool = False,
    has_transcript: Optional[bool] = None,
) -> dict:
    """A call plus what its timeline says happened, as flat derived facts."""
    timeline = sorted(timeline, key=lambda r: r.get("occurred_at") or "")

    utterances = [r for r in timeline if r.get("type") == "utterance"]
    tool_calls = [r for r in timeline if r.get("type") == "tool_call"]
    system = [r for r in timeline if r.get("type") == "system"]
    inference = [r for r in timeline if r.get("type") == "inference"]

    tools = [
        {
            "name": r.get("tool_name"),
            "status": r.get("tool_status"),
            "error": _as_text(r.get("tool_error"), 500),
            # The output is where SINA error codes live today (workplan §5-P1);
            # kept, truncated, because a detector has to regex it.
            "output": _as_text(r.get("tool_output")),
            "at": r.get("occurred_at"),
        }
        for r in tool_calls
    ]

    system_events = [
        {"event": r.get("system_event"), "at": r.get("occurred_at")} for r in system
    ]

    # El hueco entre el turno del paciente y el siguiente NO es la latencia del
    # agente: el `occurred_at` de una intervención del agente se escribe cuando
    # **acaba** de hablar, así que el hueco = nuestra latencia + lo que María
    # tarda en decir su respuesta. Medido contra el log el 2026-09-03: 17 de 26
    # huecos coinciden (±2 s) con `E2E time + TTS audio_duration`, y las 17
    # llamadas marcadas tenían un turno real máximo de 11,4 s.
    #
    # Por eso cada hueco lleva `tools_inside` (para poder descontar los que son
    # un audio reproduciéndose) y una advertencia explícita: quien quiera medir
    # latencia de verdad tiene que usar `E2E time` del log
    # (`logs_from_argo.py` + `latency_report.py`), no esto.
    tools_by_time = sorted(
        ((t["at"], t["name"]) for t in tools if t.get("at")), key=lambda x: x[0]
    )
    gaps = []
    for previous, current in zip(utterances, utterances[1:]):
        if previous.get("speaker") != PATIENT_SPEAKER:
            continue
        start, end = previous.get("occurred_at"), current.get("occurred_at")
        seconds = _seconds(start, end)
        if seconds is not None and seconds >= SILENCE_GAP_S:
            gaps.append(
                {
                    "after_speaker": previous.get("speaker"),
                    "at": start,
                    "seconds": seconds,
                    "tools_inside": [
                        name for at, name in tools_by_time
                        if start and end and start <= at <= end
                    ],
                    "note": "incluye el tiempo que habla el agente; no es latencia",
                }
            )

    # The node path: from the workflow tables when the tenant runs a flow AND
    # the trace instrumentation is deployed; otherwise from `node_entered`
    # timeline events; otherwise unknown — which is the case in prod today, so
    # every consumer has to check `path_source` before reasoning about nodes.
    path_source = "none"
    node_path: List[Dict[str, Any]] = []
    if transitions:
        path_source = "transitions"
        node_path = [
            {
                "from": t.get("from_node_id"),
                "to": t.get("to_node_id"),
                "name": t.get("to_node_name"),
                "type": t.get("to_node_type"),
                "at": t.get("occurred_at"),
            }
            for t in sorted(transitions, key=lambda t: t.get("occurred_at") or "")
        ]
    else:
        entered = [r for r in system if r.get("system_event") == "node_entered"]
        if entered:
            path_source = "timeline"
            node_path = [
                {
                    "to": (r.get("system_metadata") or {}).get("node_id"),
                    "name": (r.get("system_metadata") or {}).get("node_name"),
                    "at": r.get("occurred_at"),
                }
                for r in entered
            ]

    run = runs[0] if runs else None

    last_activity = (
        timeline[-1].get("occurred_at") if timeline else call.get("started_at")
    )
    idle_before_end = _seconds(last_activity, call.get("ended_at"))
    janitorial = bool(
        call.get("status") == "failure"
        and not any(e["event"] in TERMINAL_SYSTEM_EVENTS for e in system_events)
        and idle_before_end is not None
        and idle_before_end >= JANITOR_SILENCE_S
    )

    bundle = {
        "call_id": call["id"],
        "tenant": tenant,
        "api_key_id": call.get("api_key_id"),
        "room_name": call.get("room_name"),
        "call_direction": call.get("call_direction"),
        "status": call.get("status"),
        "user_intent": call.get("user_intent"),
        "call_result": call.get("call_result"),
        "transfer_reason": call.get("transfer_reason"),
        "transfer_failure_reason": call.get("transfer_failure_reason"),
        "acceptance": call.get("acceptance"),
        "created_at": call.get("created_at"),
        "started_at": call.get("started_at"),
        "ended_at": call.get("ended_at"),
        "duration_s": _seconds(call.get("started_at"), call.get("ended_at")),
        "is_eval": looks_like_eval(call.get("room_name")),
        # Solo si la columna existe de verdad. En prod NO existe, y exponerla
        # como `None` invita a leerle un significado que no tiene: el origen de
        # la llamada no está aquí, está en `room_name` (y en `end_user_id`).
        **({"call_source": call["call_source"]} if "call_source" in call else {}),
        "janitorial_failure": janitorial,
        "idle_before_end_s": idle_before_end,
        "turns": {
            "patient": sum(1 for u in utterances if u.get("speaker") == PATIENT_SPEAKER),
            "agent": sum(1 for u in utterances if u.get("speaker") == AGENT_SPEAKER),
            "other": sum(
                1
                for u in utterances
                if u.get("speaker") not in (PATIENT_SPEAKER, AGENT_SPEAKER)
            ),
        },
        "timeline_rows": len(timeline),
        "tools": tools,
        "tool_errors": sum(1 for t in tools if t["status"] == "error"),
        "system_events": system_events,
        "silence_gaps": gaps,
        "inference_rows": len(inference),
        "tokens": {
            "prompt": sum(r.get("prompt_tokens") or 0 for r in inference),
            "completion": sum(r.get("completion_tokens") or 0 for r in inference),
        },
        "node_path": node_path,
        "path_source": path_source,
        "flow_id": (run or {}).get("flow_id"),
        "flow_instance_id": (run or {}).get("instance_id"),
        "start_node_id": (run or {}).get("start_node_id"),
        # `acceptance=false` means the transcript was deleted, so a call can be
        # analysable at L1/L2 and unjudgeable at L3. Resolved with a separate
        # id-only query rather than pulling every transcript (see `collect`).
        "has_transcript": (
            bool(call.get("transcription")) if has_transcript is None else has_transcript
        ),
    }

    if with_transcript:
        bundle["transcription"] = call.get("transcription")
        bundle["utterances"] = [
            {
                "speaker": u.get("speaker"),
                "content": u.get("content"),
                "at": u.get("occurred_at"),
            }
            for u in utterances
        ]
    return bundle


def collect(
    env: str,
    *,
    hours: float,
    until: Optional[str] = None,
    tenant: Optional[str] = None,
    include_routing: bool = False,
    include_evals: bool = False,
    with_transcript: bool = False,
    limit: Optional[int] = None,
) -> dict:
    db = Supabase(env, load_keys())
    # `--until` permite revisar una ventana PASADA (un incidente concreto), no
    # solo las últimas N horas: sin eso no se puede comprobar si el monitor
    # habría detectado algo que ya pasó.
    end = None
    if until:
        from datetime import datetime, timezone as _tz

        end = datetime.fromisoformat(until.replace("Z", "+00:00")).astimezone(_tz.utc)
    since, until = window(hours=hours, until=end)

    tenants = {
        row["id"]: row.get("company_name")
        for row in db.select("api_keys", columns="id,company_name", limit=500)
    }

    filters = [f"created_at=gte.{since}", f"created_at=lt.{until}"]
    if tenant:
        matching = [k for k, name in tenants.items() if name == tenant]
        if not matching:
            raise AccessError(
                f"no tenant named {tenant!r} in {env}; have: "
                + ", ".join(sorted(n for n in tenants.values() if n))
            )
        filters.append(db.in_list("api_key_id", matching))

    calls = db.select(
        "calls",
        columns=CALL_COLUMNS if not with_transcript else CALL_COLUMNS + ",transcription",
        filters=filters,
        order="created_at.asc",
        limit=limit,
    )

    skipped = Counter()
    kept: List[dict] = []
    for call in calls:
        name = tenants.get(call.get("api_key_id"))
        if not include_routing and name in ROUTING_TENANTS:
            skipped["routing_line"] += 1
            continue
        # `call_source` only exists where the migration landed; treat its
        # absence as "unknown", never as "real".
        source = call.get("call_source")
        is_eval = looks_like_eval(call.get("room_name")) or (
            source is not None and source != "real"
        )
        if is_eval and not include_evals:
            skipped["eval_or_test"] += 1
            continue
        kept.append(call)

    call_ids = [c["id"] for c in kept]

    timeline_rows: List[dict] = []
    runs: List[dict] = []
    transitions: List[dict] = []
    if call_ids:
        timeline_rows = db.select_by_chunks(
            "call_timeline",
            column="call_id",
            values=call_ids,
            columns=TIMELINE_COLUMNS,
            order="occurred_at.asc",
        )
        runs = db.select_by_chunks(
            "call_workflow_runs",
            column="call_id",
            values=call_ids,
            columns="call_id,flow_id,instance_id,start_node_id,created_at",
        )
        transitions = db.select_by_chunks(
            "call_workflow_transitions",
            column="call_id",
            values=call_ids,
            columns="call_id,from_node_id,to_node_id,to_node_name,to_node_type,occurred_at",
            order="occurred_at.asc",
        )

    # Which calls still have their transcript, without downloading any of them.
    with_words: set[str] = set()
    if call_ids and not with_transcript:
        with_words = {
            row["id"]
            for row in db.select_by_chunks(
                "calls",
                column="id",
                values=call_ids,
                columns="id",
                filters=["transcription=not.is.null"],
            )
        }

    by_call: Dict[str, List[dict]] = {}
    for row in timeline_rows:
        by_call.setdefault(row["call_id"], []).append(row)
    runs_by_call: Dict[str, List[dict]] = {}
    for row in runs:
        runs_by_call.setdefault(row["call_id"], []).append(row)
    transitions_by_call: Dict[str, List[dict]] = {}
    for row in transitions:
        transitions_by_call.setdefault(row["call_id"], []).append(row)

    bundles = [
        build_bundle(
            call,
            by_call.get(call["id"], []),
            runs_by_call.get(call["id"], []),
            transitions_by_call.get(call["id"], []),
            tenants.get(call.get("api_key_id")),
            with_transcript=with_transcript,
            has_transcript=None if with_transcript else call["id"] in with_words,
        )
        for call in kept
    ]

    return {
        "meta": {
            "env": env,
            "since": since,
            "until": until,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "calls_seen": len(calls),
            "calls_kept": len(bundles),
            "skipped": dict(skipped),
            "has_call_source_column": _has_column(calls, "call_source"),
            "timeline_rows": len(timeline_rows),
            "workflow_runs": len(runs),
            "workflow_transitions": len(transitions),
            "with_transcript": with_transcript,
        },
        "bundles": bundles,
    }


def summarize(payload: dict) -> None:
    meta = payload["meta"]
    bundles = payload["bundles"]
    print(f"== {meta['env']} {meta['since']} → {meta['until']} ==")
    print(
        f"  calls: {meta['calls_kept']} kept of {meta['calls_seen']} "
        f"(skipped {meta['skipped'] or '{}'}); timeline rows {meta['timeline_rows']}"
    )
    if not meta["has_call_source_column"]:
        print("  note: no `call_source` column here — evals filtered by room_name only")
    if not meta["workflow_transitions"]:
        print("  note: no workflow transitions in this window — node paths unavailable")

    per_tenant = Counter(b["tenant"] for b in bundles)
    print("  per tenant:", dict(per_tenant.most_common()))
    print("  status:", dict(Counter(b["status"] for b in bundles).most_common()))
    print("  result:", dict(Counter(b["call_result"] for b in bundles).most_common(8)))
    print("  intent:", dict(Counter(b["user_intent"] for b in bundles).most_common(8)))
    print("  path_source:", dict(Counter(b["path_source"] for b in bundles)))
    janitorial = [b for b in bundles if b["janitorial_failure"]]
    failures = [b for b in bundles if b["status"] == "failure"]
    print(f"  failures: {len(failures)} ({len(janitorial)} look janitorial)")
    with_tool_errors = [b for b in bundles if b["tool_errors"]]
    print(f"  calls with a tool error: {len(with_tool_errors)}")
    print(f"  calls with a silence gap >= {SILENCE_GAP_S:.0f}s: "
          f"{sum(1 for b in bundles if b['silence_gaps'])}")
    print(
        f"  calls with no patient utterance: "
        f"{sum(1 for b in bundles if b['turns']['patient'] == 0)}"
    )
    print(
        f"  transcript kept: {sum(1 for b in bundles if b['has_transcript'])}"
        f" of {len(bundles)}"
    )
    durations = sorted(b["duration_s"] for b in bundles if b["duration_s"] is not None)
    if durations:
        p = lambda q: durations[min(len(durations) - 1, int(len(durations) * q))]  # noqa: E731
        print(
            f"  duration p50/p90/max: {p(0.5):.0f}s / {p(0.9):.0f}s / {durations[-1]:.0f}s"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default="prod", choices=["prod", "stg", "dev"])
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--until", help="fin de la ventana en ISO (p.ej. 2026-08-26T00:00:00Z); por defecto, ahora")
    parser.add_argument("--tenant", help="exact company_name, e.g. 'HCB'")
    parser.add_argument("--include-routing", action="store_true")
    parser.add_argument("--include-evals", action="store_true")
    parser.add_argument(
        "--with-transcript",
        action="store_true",
        help="include patient words (needed by the judge stage only)",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--out", help="write the bundles as JSON here")
    args = parser.parse_args()

    try:
        payload = collect(
            args.env,
            hours=args.hours,
            until=args.until,
            tenant=args.tenant,
            include_routing=args.include_routing,
            include_evals=args.include_evals,
            with_transcript=args.with_transcript,
            limit=args.limit,
        )
    except AccessError as exc:
        print(f"access error: {exc}", file=sys.stderr)
        return 2

    summarize(payload)
    if args.out:
        with open(args.out, "w") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)
        print(f"  written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
