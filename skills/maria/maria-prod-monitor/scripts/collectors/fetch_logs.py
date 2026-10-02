#!/usr/bin/env python3
"""Read-only Log Analytics pull: the signals that exist ONLY in the logs.

Everything the database knows is in `fetch_bundles.py`. These are the ones it
does not: per-turn end-to-end latency, EOU / TTFT / TTFB, tool duration, the
SINA error codes in maria-core's warnings, and the disconnect reasons LiveKit
could not map.

Access rules (from `maria-asks-logs.md`): `az monitor log-analytics query` over
`ContainerLogV2`, never kubectl; a workspace is never mixed with another
environment's namespace; `TimeGenerated >= start and < end`.

Truncation is the trap: a `take` limit silently caps the answer, and 24 h of
prod is ~10k metric lines. So the window is split recursively — a slice that
comes back exactly full is split in two and re-queried, never reported as
complete.

Attribution (measured against dev logs 2026-08-31, correcting workplan §1.3):
**maria-voice's JSON lines already carry `room`, `job_id` and `pid`** — LiveKit
re-emits each job's records with those extras (`job_proc_executor
.logging_extra()`), so the envelope is parsed and attribution is `exact`
without waiting for any deploy. `calls.room_name` joins a room straight to its
call. `call_id` arrives on the line too once the P0 PR ships, which removes the
join. The plain `print()` lines (`[startup] room=…`) have no envelope, so they
are attributed by their own `room=` field. Anything else falls back to the
pod+time inference and is marked `inferred`, or `none` when the pod ran two
jobs at once — never guessed.

Usage:
    az login --tenant "1a04458f-6a53-4d0c-8734-8a0b5e1b8890" \
             --scope "https://api.loganalytics.io/.default"   # once, interactive
    python3 fetch_logs.py --env prod --hours 2 --out logs.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

WORKSPACES = {
    "prod": ("908ecb58-7d6e-473a-8716-a8aeb789363d", "omniloy-prd"),
    "dev": ("29aa169b-b6ca-4379-9655-7ca3326bf1b3", "omniloy-dev"),
    "stg": ("91daf838-5e4f-47fe-b74e-52d7b3accb88", "omniloy-stg"),
}

CONTAINERS = {
    "voice": "maria-voice-container",
    "core": "maria-core",
    "mcp": "mcp-server",
    "dispatcher": "mariadispatcher",
}

# One `take` per slice. Kept well under the service limit so a full slice is a
# clear signal to split rather than an ambiguous one.
SLICE_TAKE = 5000
MIN_SLICE = timedelta(minutes=1)

# How long a job may still be logging after its `[startup]`. Prod's longest
# call in 24 h was 11 min (p90 4 min), so 20 minutes is generous; it only
# affects the envelope-less lines, which are the `print()`ed startup traces.
JOB_LIVE_WINDOW = timedelta(minutes=20)

# What we ask for. Anything not matched by a parser below is still returned raw
# (`kind: "other"`), because a line nobody parsed yet is the interesting one.
VOICE_MARKERS = [
    "E2E time:",
    "LLM metrics:",
    "TTS metrics:",
    "EOU metrics:",
    "STT metrics:",
    "TOOL EXECUTED:",
    "[startup] room=",
    "Pre-generated greeting for",
    "unmapped livekit disconnect reason",
    "session_error",
]
CORE_MARKERS = [
    "validation failed with error code",
    "EXT-PAP-",
    "SINA-",
    "Operation failed",
]
MCP_MARKERS = [
    "custom tool timed out",
    "CALL_ID extracted",
    "status\": \"error",
]

# maria-voice logs JSON in a deployed worker; LiveKit puts the job's identifiers
# in the envelope, not in the message. Everything interesting is a field here,
# so the envelope is parsed before any regex runs on the message.
ENVELOPE_FIELDS = ("room", "job_id", "pid", "call_id")

PARSERS: List[Tuple[str, re.Pattern]] = [
    # `logger.info(f"E2E time: {seconds}")` — session.py
    ("e2e", re.compile(r"E2E time:\s*([0-9.]+)")),
    (
        "llm",
        re.compile(
            r"LLM metrics:\s*ttft=(?P<ttft>[0-9.]+),\s*duration=(?P<duration>[0-9.]+),"
            r"\s*tokens_per_second=(?P<tps>[0-9.]+)"
        ),
    ),
    (
        "tts",
        re.compile(
            r"TTS metrics:\s*ttfb=(?P<ttfb>[0-9.]+),\s*duration=(?P<duration>[0-9.]+),"
            r"\s*audio_duration=(?P<audio>[0-9.]+)"
        ),
    ),
    (
        "eou",
        re.compile(
            r"EOU metrics:\s*end_of_utterance_delay=(?P<eou>[0-9.]+),"
            r"\s*transcription_delay=(?P<transcription>[0-9.]+)"
        ),
    ),
    ("stt", re.compile(r"STT metrics:\s*audio_duration=(?P<audio>[0-9.]+)")),
    # `{'❌'|'🔧'} TOOL EXECUTED: {name} ({s}s) [agent: {Class}]` — session.py
    (
        "tool",
        re.compile(
            r"TOOL EXECUTED:\s*(?P<name>\S+)\s*\((?P<seconds>[0-9.]+)s\)"
            r"(?:\s*\[agent:\s*(?P<agent>[^\]]+)\])?"
        ),
    ),
    # The other tool line, from `speech_logger`: same event, different wording.
    # Both exist in the same run, so a parser for only one halves the tool time.
    (
        "tool",
        re.compile(
            r"Tool executed:\s*(?P<name>\S+)\s*\((?P<seconds>[0-9.]+) seconds taken\)"
        ),
    ),
    ("startup", re.compile(r"\[startup\] room=(?P<room>\S+)\s+(?P<label>\S+)\s+t=\+(?P<ms>[0-9.]+)ms")),
    # A turn that also moved the flow: the destination node's opening had to be
    # produced. It is what separates a transitioning turn from an answering one
    # (AGENTS.md §Measuring latency), and they have different fixes.
    (
        "greeting",
        re.compile(
            r"Pre-generated greeting for (?P<node>\S+) \(streaming, first chunk in (?P<seconds>[0-9.]+)s\)"
        ),
    ),
    (
        "disconnect_unmapped",
        re.compile(r"unmapped livekit disconnect reason\s*(?P<reason>\S+)"),
    ),
    # SINA / HIS business codes, wherever they appear.
    ("error_code", re.compile(r"\b(?P<code>EXT-[A-Z]+-\d+|SINA-[A-Z]+-[A-Z]+-[A-Z0-9]+)\b")),
    # The fields the P0 PRs add. Present => attribution is exact.
    ("call_id_field", re.compile(r'"call_id"\s*:\s*"(?P<call_id>[0-9a-f-]{36})"')),
]


class LogAccessError(RuntimeError):
    pass


def _run_kql(workspace: str, query: str, timeout: int = 300) -> List[dict]:
    result = subprocess.run(
        [
            "az",
            "monitor",
            "log-analytics",
            "query",
            "--workspace",
            workspace,
            "--analytics-query",
            query,
            "--output",
            "json",
            "--only-show-errors",
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        if "AADSTS" in message or "az login" in message:
            raise LogAccessError(
                "Log Analytics needs an interactive login (conditional access):\n"
                '  az logout && az login --tenant "1a04458f-6a53-4d0c-8734-8a0b5e1b8890" '
                '--scope "https://api.loganalytics.io/.default"'
            )
        raise LogAccessError(message[:800])
    try:
        return json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise LogAccessError(f"unparseable az output: {exc}") from exc


def _kql(
    namespace: str,
    container: str,
    start: datetime,
    end: datetime,
    markers: List[str],
) -> str:
    # KQL string literals: single quotes, doubled to escape.
    marker_list = ", ".join("'" + m.replace("'", "''") + "'" for m in markers)
    return f"""
let startTime = datetime({start.strftime('%Y-%m-%dT%H:%M:%SZ')});
let endTime = datetime({end.strftime('%Y-%m-%dT%H:%M:%SZ')});
ContainerLogV2
| where TimeGenerated >= startTime and TimeGenerated < endTime
| where PodNamespace == '{namespace}'
| where ContainerName == '{container}'
| where LogMessage has_any ({marker_list})
| project TimeGenerated, PodName, ContainerName, LogMessage
| order by TimeGenerated asc
| take {SLICE_TAKE}
""".strip()


def fetch_slice(
    workspace: str,
    namespace: str,
    container: str,
    start: datetime,
    end: datetime,
    markers: List[str],
    *,
    depth: int = 0,
) -> List[dict]:
    """Rows for one slice, splitting it while the answer comes back full."""
    rows = _run_kql(workspace, _kql(namespace, container, start, end, markers))
    if len(rows) < SLICE_TAKE:
        return rows
    span = end - start
    if span <= MIN_SLICE:
        # Cannot split further: say so rather than pretend the slice is whole.
        print(
            f"  WARNING: {start:%H:%M}–{end:%H:%M} returned a full page at the "
            f"minimum slice; {len(rows)} rows kept, more may exist",
            file=sys.stderr,
        )
        return rows
    middle = start + span / 2
    return fetch_slice(
        workspace, namespace, container, start, middle, markers, depth=depth + 1
    ) + fetch_slice(
        workspace, namespace, container, middle, end, markers, depth=depth + 1
    )


_TIMESTAMP = re.compile(
    r"^(?P<head>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})"
    r"(?:\.(?P<fraction>\d+))?"
    r"(?P<offset>Z|[+-]\d{2}:?\d{2})?$"
)


def _parse_at(value: Optional[str]) -> Optional[datetime]:
    """Log Analytics timestamps: `...Z` with SEVEN fractional digits.

    `datetime.fromisoformat` rejects more than six, so the fraction is trimmed —
    carefully, because the digits of a `+HH:MM` offset must not be mistaken for
    part of it.
    """
    if not value:
        return None
    match = _TIMESTAMP.match(value.strip())
    if not match:
        return None
    fraction = (match.group("fraction") or "")[:6]
    offset = match.group("offset") or "+00:00"
    text = match.group("head")
    if fraction:
        text += f".{fraction}"
    text += "+00:00" if offset == "Z" else offset
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def split_envelope(raw: str) -> Tuple[str, Dict[str, Any]]:
    """`(message, envelope)` for a JSON log line; `(raw, {})` for a plain one.

    A deployed worker logs JSON, and the identifiers live in the envelope
    (`room`, `job_id`, `pid`, and `call_id` once the P0 PR ships) rather than in
    the text. The `print()`-based `[startup]` lines are not JSON at all.
    """
    text = raw.strip()
    if not text.startswith("{"):
        return raw, {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return raw, {}
    if not isinstance(payload, dict):
        return raw, {}
    envelope = {k: payload[k] for k in ENVELOPE_FIELDS if payload.get(k) is not None}
    if payload.get("name"):
        envelope["logger"] = payload["name"]
    if payload.get("level"):
        envelope["level"] = payload["level"]
    message = payload.get("message")
    return (message if isinstance(message, str) else raw), envelope


def parse_line(row: dict) -> dict:
    raw = row.get("LogMessage") or ""
    message, envelope = split_envelope(raw)
    parsed: Dict[str, Any] = {
        "at": row.get("TimeGenerated"),
        "pod": row.get("PodName"),
        "container": row.get("ContainerName"),
        "kind": "other",
        "fields": {},
    }
    if envelope:
        parsed["envelope"] = envelope
        for key in ("room", "job_id", "call_id"):
            if key in envelope:
                parsed[key] = envelope[key]
    for kind, pattern in PARSERS:
        match = pattern.search(message)
        if not match:
            continue
        if kind == "call_id_field":
            parsed.setdefault("call_id", match.group("call_id"))
            continue
        if parsed["kind"] == "other":
            parsed["kind"] = kind
            parsed["fields"] = (
                match.groupdict()
                if match.groupdict()
                else {"value": match.group(1)}
            )
        elif kind == "error_code":
            parsed["fields"]["error_code"] = match.group("code")
    if parsed["kind"] == "startup" and "room" not in parsed:
        # The only lines without an envelope carry the room themselves.
        parsed["room"] = parsed["fields"]["room"]
    if parsed["kind"] == "other":
        # Truncated: a raw line can carry patient data in DEV-mode blocks.
        parsed["message"] = message[:400]
    return parsed


def attribute(records: List[dict]) -> None:
    """Tag each record `exact` / `inferred` / `none`, in place.

    `exact` when the line names the job itself — a `call_id` (after the P0 PR)
    or the `room` / `job_id` LiveKit already puts in the JSON envelope, which
    covers everything a deployed worker logs through `logging`.

    `inferred` is the fallback for the few lines with no envelope: they are
    assigned the room of the most recent `[startup]` on the same pod, and only
    while no *other* job started on that pod in between — a pod runs several job
    processes at once, so an overlapping start means the honest answer is
    `none`.
    """
    by_pod: Dict[str, List[dict]] = {}
    for record in records:
        by_pod.setdefault(record.get("pod") or "?", []).append(record)

    for pod_records in by_pod.values():
        pod_records.sort(key=lambda r: r.get("at") or "")
        current_room: Optional[str] = None
        started_at: Optional[datetime] = None
        ambiguous = False
        for record in pod_records:
            moment = _parse_at(record.get("at"))
            if record["kind"] == "startup":
                room = record["fields"]["room"]
                # Only an OVERLAPPING start is ambiguous. Two calls served one
                # after the other on the same pod are not: the earlier job is
                # long gone, and treating them as ambiguous would throw away
                # every line of a busy pod.
                ambiguous = (
                    current_room is not None
                    and room != current_room
                    and started_at is not None
                    and moment is not None
                    and (moment - started_at) < JOB_LIVE_WINDOW
                )
                current_room = room
                started_at = moment
            elif (
                started_at is not None
                and moment is not None
                and (moment - started_at) > JOB_LIVE_WINDOW
            ):
                # Too far from any start to claim it belongs to that job.
                current_room = None

            if record.get("call_id") or record.get("job_id") or record.get("room"):
                record["attribution"] = "exact"
                continue
            if current_room and not ambiguous:
                record["attribution"] = "inferred"
                record["room"] = current_room
            else:
                record["attribution"] = "none"


def collect(env: str, service: str, hours: float, until: Optional[datetime] = None) -> dict:
    if env not in WORKSPACES:
        raise LogAccessError(f"unknown env {env!r}")
    workspace, namespace = WORKSPACES[env]
    container = CONTAINERS[service]
    markers = {
        "voice": VOICE_MARKERS,
        "core": CORE_MARKERS,
        "mcp": MCP_MARKERS,
        "dispatcher": VOICE_MARKERS,
    }[service]

    end = until or datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    print(f"  querying {env} / {namespace} / {container}: {start:%Y-%m-%d %H:%M} → {end:%H:%M} UTC")

    rows = fetch_slice(workspace, namespace, container, start, end, markers)
    records = [parse_line(row) for row in rows]
    attribute(records)
    return {
        "meta": {
            "env": env,
            "service": service,
            "container": container,
            "namespace": namespace,
            "since": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "until": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "rows": len(records),
        },
        "records": records,
    }


def summarize(payload: dict) -> None:
    from collections import Counter

    records = payload["records"]
    print(f"  rows: {len(records)}")
    print("  kinds:", dict(Counter(r["kind"] for r in records).most_common()))
    print("  attribution:", dict(Counter(r["attribution"] for r in records)))
    rooms = {r.get("room") for r in records if r.get("room")}
    print(f"  rooms seen: {len(rooms)}")
    codes = Counter(
        r["fields"].get("error_code") or r["fields"].get("code")
        for r in records
        if r["kind"] == "error_code" or "error_code" in r["fields"]
    )
    if codes:
        print("  error codes:", dict(codes.most_common(10)))
    unparsed = [r for r in records if r["kind"] == "other"]
    if unparsed:
        print(f"  unparsed lines: {len(unparsed)} (first: {unparsed[0]['message'][:120]!r})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default="prod", choices=sorted(WORKSPACES))
    parser.add_argument("--service", default="voice", choices=sorted(CONTAINERS))
    parser.add_argument("--hours", type=float, default=2.0)
    parser.add_argument("--out")
    args = parser.parse_args()

    if args.env == "prod":
        print("  NOTE: querying PRODUCTION logs (read-only)")

    try:
        payload = collect(args.env, args.service, args.hours)
    except LogAccessError as exc:
        print(f"log access error: {exc}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print("log access error: az query timed out", file=sys.stderr)
        return 2

    summarize(payload)
    if args.out:
        with open(args.out, "w") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)
        print(f"  written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
