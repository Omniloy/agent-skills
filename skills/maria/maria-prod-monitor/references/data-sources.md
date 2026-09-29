# Data sources, access and their traps

Everything here is read-only. The only writes this skill may do are:
- a Jira task or comment, when the user asks for it;
- a PR to `docs/monitoring/` (catalog, KEDB, tasks);
- a Slack post, only with explicit approval.

## 1. Which code is running where

| environment | branch | Supabase ref |
|---|---|---|
| production | **`main`** | `yavtkqacpiwaahzrejhk` |
| staging | `stg` | `kvjdhbpkucvmnoczuoyy` |
| development | `dev` | `dfllbgtwfbfyiiayioeu` |

**To reason about production, read `main`.** A conclusion drawn from `dev` misclassified real
calls: `dev` carried a fix production did not have. `scripts/collectors/deployed.py --env prod
--repo voice|core|mcp [--path … | --grep …]` fetches the branch and prints the deployed head
before showing code. Check whether production still runs the old MCP server or the merged
worker code: the MCP removal reached `dev` first.

## 2. Database (Supabase, PostgREST GET)

Keys come from maria-core-service's `.env`; prod's block is commented out and is read anyway.
`_supabase.py`:
- never prints keys;
- sends timestamps as `…Z`, because `+00:00` becomes a space and PostgREST returns 400;
- paginates.

| table | what | traps |
|---|---|---|
| `calls` | status, `user_intent`, `call_result`, transfer reasons, `transcription` `[{speaker, content, time}]`, `room_name` | `speaker` is `patient`/`operator`. Prod has **no `call_source`**: filter evals by `room_name` (`test-ai_generated-…`). `acceptance=false` deletes the transcript |
| `call_timeline` | append-only: `utterance` (`patient`/`agent`), `tool_call` (status, error, jsonb output), `system`, `inference` | order by `occurred_at`. `tool_output`/`tool_error` are sometimes object, sometimes string |
| `call_workflow_runs`, `call_workflow_transitions` | the flow snapshot used, and the exact node path | **only where the tenant runs a workflow AND the trace is deployed.** Empty in prod whenever no tenant is in `conversation_flow` mode |
| `conversation_flows(_versions)` | flow documents and their history | the active version stamps the analysis |
| `call_alert_rules/events` | core-service's alert engine | never fired in prod as of 2026-08-31 (0 events): do not rely on it there |

Known data traps:
- The `finish-stuck-calls` cron turns hung calls into `failure` after 5 min, so some failures
  are janitorial.
- A silence timeout counts as `success`, like a normal hang-up.
- The view `voice_metrics` excludes failures: do not use it.
- `call_status = TRANSFERRED` is set BEFORE the transfer is requested, so "transferred"
  overcounts.
- `turns.agent = 0` does not mean the agent did not speak (incomplete records exist).
- A hang-up after a failed transfer is always a consequence, never a cause.
- **Never measure latency from `calls.transcription` timestamps.** Turns in the same node come
  out 0.0–0.6 s apart: that `time` is not when the voice started.

## 3. Logs

| route | coverage | notes |
|---|---|---|
| **Argo API** (`scripts/argo/argo_logs.py --app maria{voice,core}-{prd,stg,dev}`) | only the **live** pods. Core ~25 h, voice ~19 h (it autoscales; minutes on a busy day), cronjob pods several days | credentials `ARGO_USR`/`ARGO_PASS` in the private dir (`_paths.py`). GET only: never sync, restart or touch a deployment |
| Azure Log Analytics (`az monitor log-analytics query`, `ContainerLogV2`) | history | **PRD is not accessible** with the current account (`InsufficientAccessError`); dev/stg are. `az login --tenant <tenant>` without `--scope` |

**Download logs as soon as a call matters.** A pod replaced 20 minutes later takes the
evidence with it: pilot 3 lost colleagues' calls that way.

What the logs carry:
- **maria-voice lines are JSON** with `room`, `job_id`, `pid` in the envelope, plus
  `call_id` once `bind_log_context` is deployed. The room name contains the caller's phone:
  correlate by `call_id`, and never copy room names into reports.
- `E2E time: <s>` per turn is THE latency metric: user stops speaking → agent starts. Tools
  are logged **twice** (`TOOL EXECUTED` by maria_voice and `Tool executed:` by speech_logger,
  same millisecond), so deduplicate before computing `e2e − tools`.
- `Pre-generated greeting for <node> (… first chunk in <N>s)` is a transition's TTFT.
- **maria-core** carries only a `requestId` per use-case (a `callId` binding is in progress).
  Provider codes (`EXT-PAP-*`) are free text in its warnings. Validation responses look like
  `{error:"Validation error", details:"<code>"}`.
- The old MCP server (while production runs it) logs the full identification diagnosis
  (`login_and_onboard: restrictive validation failed (<reason>)`, …).

## 4. Room naming

The name is not a contract:
- `hcb-es-_+34…` (prod, `<customer>-<lang>-_<phone>`);
- `dev-sanroque-_+34…` (dev);
- `test-ai_generated-…` (evals).

The customer extractor covers all three. Prefer `call_id` wherever it exists.

## 5. PII

- Nothing that identifies a patient goes into the repo, Slack, Jira or a published report:
  phone numbers, documents, names, emails, transcripts.
- Name the call (`d7085960`), never the caller.
- Show a document by its shape (`########L`).
- SINA review files and the trend `state.json` live in the private dir, never in git.
