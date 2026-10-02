#!/usr/bin/env python3
"""Turn latency from the logs: percentiles, `e2e − tools`, worst offenders.

The number that matters is `E2E time` — the caller stops talking, the agent
starts talking — and it exists only in the worker log. This turns those lines
into the table the workplan asks for (§2.5), with two rules that decide whether
the table means anything:

* **Percentiles, never a mean.** Model and webhook latency have long tails; a
  mean describes a call that never happened. Reported p50/p90/p95/p99.
* **Answering turns and transitioning turns are aggregated apart.** The
  distribution is bimodal and nothing that fixes one touches the other
  (AGENTS.md §Measuring latency). A turn is "transitioning" when the flow moved
  and the destination node's opening had to be produced.

`e2e − tools` is the blame split: what the turn would have cost with an instant
backend. Tool seconds come from the `TOOL EXECUTED` lines between the previous
turn and this one, on the same pod.

No thresholds are enforced. Our own measured baseline is p50 1.8 s / p95 4.3 s,
so an 800 ms SLA would paint everything red; the workplan's plan is two weeks of
data first, then a versioned SLA per turn type.

Usage:
    python3 fetch_logs.py --env prod --hours 4 --out logs.json
    python3 latency_report.py logs.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from typing import Dict, List, Optional

SLOW_TURN_S = 1.5  # the "conversation feels broken" reference, for a % column

#: El manejador de inactividad habla cuando el paciente lleva 15 s callado. Su
#: `E2E time` mide ese silencio, no nuestra latencia, y con el peso suficiente
#: se lleva la cola entera: el peor turno de prod del 2026-09-03 era uno de
#: estos, 27,9 s con `tools=0`, `ttft=0,93` y `ttfb=0,11`. Se agregan aparte.
INACTIVITY_RE = re.compile(r"\[Inactivity handler\] agent is waiting for the user")


def percentile(values: List[float], q: float) -> float:
    """Nearest-rank percentile. No interpolation: with tens of turns per
    client, interpolating invents precision the sample does not have."""
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[index]


LANGUAGES = ("es", "en", "ca", "nl")
ENV_PREFIXES = ("dev", "stg", "test")


def client_of(room: Optional[str]) -> str:
    """Client from the room name.

    Prod names are `<client>-<lang>-_<phone>_<suffix>` (`hcb-es-_+34…`), and a
    client slug can carry its own dashes (`san-roque-es-_…`), so the language
    segment marks the boundary. Dev and stg name rooms differently
    (`dev-sanroque-_+34…`, `test-ai_generated-…`), hence the env-prefix branch —
    without it every dev room reads as one client called "dev".
    """
    if not room:
        return "unknown"
    parts = room.split("-")
    for index, part in enumerate(parts):
        if part in LANGUAGES:
            return "-".join(parts[:index]) or "unknown"
    if parts[0] in ENV_PREFIXES and len(parts) > 1:
        return parts[1] or "unknown"
    return parts[0] or "unknown"


def dedupe_tools(tool_records: List[dict]) -> List[dict]:
    """One entry per tool execution.

    Each execution is logged twice — `TOOL EXECUTED` by `maria_voice` and
    `Tool executed:` by `speech_logger`, same millisecond, same duration — so
    summing both doubles every tool second and halves `e2e − tools`.
    """
    seen: Dict[tuple, dict] = {}
    for record in tool_records:
        key = (record["fields"]["name"], (record.get("at") or "")[:19])
        logger = (record.get("envelope") or {}).get("logger")
        if key not in seen or logger == "maria_voice":
            seen[key] = record
    return sorted(seen.values(), key=lambda r: r.get("at") or "")


def build_turns(records: List[dict]) -> List[dict]:
    """One entry per `E2E time` line, with the tools that ran before it.

    Grouped by pod: a turn's tool lines are the ones emitted by the same
    process since its previous turn. When a pod served two rooms in the window
    the records are `attribution: none`, so the turn is kept for the aggregate
    and excluded from anything per-client.
    """
    by_pod: Dict[str, List[dict]] = defaultdict(list)
    for record in records:
        by_pod[record.get("pod") or "?"].append(record)

    turns: List[dict] = []
    for pod, pod_records in by_pod.items():
        pod_records.sort(key=lambda r: r.get("at") or "")
        pending_tools: List[dict] = []
        pending_inactivity = False
        pending_greeting: Optional[dict] = None
        latest: Dict[str, dict] = {}
        for record in pod_records:
            kind = record["kind"]
            if kind == "tool":
                pending_tools.append(record)
            elif INACTIVITY_RE.search(record.get("message") or ""):
                pending_inactivity = True
            elif kind == "greeting":
                pending_greeting = record
            elif kind in ("llm", "tts", "eou", "stt"):
                latest[kind] = record
            elif kind == "e2e":
                seconds = float(record["fields"]["value"])
                pending_tools = dedupe_tools(pending_tools)
                tool_seconds = sum(
                    float(t["fields"]["seconds"]) for t in pending_tools
                )
                turns.append(
                    {
                        "at": record["at"],
                        "pod": pod,
                        "call_id": record.get("call_id"),
                        "room": record.get("room"),
                        "client": client_of(record.get("room")),
                        "attribution": record.get("attribution", "none"),
                        "type": (
                            "inactivity" if pending_inactivity
                            else "transition" if pending_greeting
                            else "response"
                        ),
                        "e2e_s": seconds,
                        "tool_s": round(tool_seconds, 3),
                        "e2e_minus_tools_s": round(seconds - tool_seconds, 3),
                        "tools": [t["fields"]["name"] for t in pending_tools],
                        "greeting_s": float(pending_greeting["fields"]["seconds"])
                        if pending_greeting
                        else None,
                        "ttft_s": float(latest["llm"]["fields"]["ttft"])
                        if "llm" in latest
                        else None,
                        "ttfb_s": float(latest["tts"]["fields"]["ttfb"])
                        if "tts" in latest
                        else None,
                        "eou_s": float(latest["eou"]["fields"]["eou"])
                        if "eou" in latest
                        else None,
                        "transcription_s": float(latest["eou"]["fields"]["transcription"])
                        if "eou" in latest
                        else None,
                    }
                )
                pending_tools = []
                pending_inactivity = False
                pending_greeting = None
                latest = {}
    turns.sort(key=lambda t: t["at"] or "")
    return turns


def table(name: str, turns: List[dict]) -> None:
    if not turns:
        print(f"    {name:<28} (no turns)")
        return
    e2e = [t["e2e_s"] for t in turns]
    net = [t["e2e_minus_tools_s"] for t in turns]
    slow = sum(1 for value in e2e if value > SLOW_TURN_S)
    print(
        f"    {name:<28} n={len(turns):<5} "
        f"p50={percentile(e2e, 0.5):.2f} p90={percentile(e2e, 0.9):.2f} "
        f"p95={percentile(e2e, 0.95):.2f} p99={percentile(e2e, 0.99):.2f}  "
        f">{SLOW_TURN_S:.1f}s={100.0 * slow / len(turns):.0f}%  "
        f"e2e−tools p50={percentile(net, 0.5):.2f} p95={percentile(net, 0.95):.2f}"
    )


def report(payload: dict, turns: List[dict]) -> None:
    meta = payload["meta"]
    print(f"== latency {meta['env']} {meta['since']} → {meta['until']} ==")
    print(f"  turns: {len(turns)} from {meta['rows']} log rows")
    attributed = [t for t in turns if t["attribution"] in ("exact", "inferred")]
    print(
        f"  attributable to a room/call: {len(attributed)} "
        f"({'exact' if any(t['attribution'] == 'exact' for t in turns) else 'inferred only'})"
    )

    # Los turnos de inactividad salen de los agregados: su `E2E time` es el
    # paciente callado 15 s, no nuestra latencia, y meterlos mueve la cola sin
    # que haya cambiado nada nuestro. Se cuentan aparte para que su volumen se
    # vea (si sube, es que la gente se queda muda más a menudo).
    ours = [t for t in turns if t["type"] != "inactivity"]
    idle = [t for t in turns if t["type"] == "inactivity"]

    print("\n  overall (sin los turnos que dispara la inactividad):")
    table("all turns", ours)
    for turn_type in ("response", "transition"):
        table(f"  {turn_type} turns", [t for t in ours if t["type"] == turn_type])
    if idle:
        print()
        table("  inactivity (NO es latencia)", idle)

    print("\n  per client (attributable turns only, sin inactividad):")
    per_client: Dict[str, List[dict]] = defaultdict(list)
    for turn in attributed:
        if turn["type"] != "inactivity":
            per_client[turn["client"]].append(turn)
    for client in sorted(per_client):
        table(client, per_client[client])

    print("\n  worst 5 turns (sin inactividad):")
    for turn in sorted(ours, key=lambda t: -t["e2e_s"])[:5]:
        parts = [
            f"e2e={turn['e2e_s']:.2f}s",
            f"tools={turn['tool_s']:.2f}s{turn['tools'] or ''}",
        ]
        for label, key in (("eou", "eou_s"), ("ttft", "ttft_s"), ("ttfb", "ttfb_s")):
            if turn[key] is not None:
                parts.append(f"{label}={turn[key]:.2f}s")
        if turn["greeting_s"] is not None:
            parts.append(f"greeting={turn['greeting_s']:.2f}s")
        print(
            f"    {turn['at']} {turn['client']:<12} {turn['type']:<10} "
            + " ".join(parts)
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", help="output of fetch_logs.py")
    parser.add_argument("--out", help="write the per-turn rows as JSON here")
    args = parser.parse_args()

    with open(args.logs) as handle:
        payload = json.load(handle)
    if "records" not in payload:
        print(f"{args.logs} is not a fetch_logs.py output", file=sys.stderr)
        return 1

    turns = build_turns(payload["records"])
    report(payload, turns)
    if args.out:
        with open(args.out, "w") as handle:
            json.dump({"meta": payload["meta"], "turns": turns}, handle, indent=1)
        print(f"\n  written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
