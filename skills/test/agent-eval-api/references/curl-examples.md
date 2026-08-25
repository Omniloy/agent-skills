# curl examples

Copy-paste-friendly examples for the Omniloy Agent Testing Platform API. Replace
placeholders (`<...>`) with real values. Every example assumes:

```bash
BASE="https://mariaevals.api.omniloy.com"   # or http://localhost:8000 for local
# NOTE: mariaevals-dev.api.omniloy.com is a SEPARATE deployment with its own
# database and credentials. Tokens are not interchangeable between the two.
```

> The same host serves the web app (SPA) at `/` and the REST API under `/api/...`.
> Always call the `/api/...` paths.

---

## Auth

The token endpoint takes a JSON body with `email` and `password` and returns an
`access_token` you pass as a Bearer token on every other call.

```bash
TOKEN=$(curl -s -X POST "$BASE/api/auth/token" \
  -H 'Content-Type: application/json' \
  -d '{"email":"<you@omniloy.com>","password":"<password>"}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

echo "${TOKEN:0:12}…"   # sanity check it's non-empty
```

Use it on every authenticated request:

```bash
curl -s "$BASE/api/agents" -H "Authorization: Bearer $TOKEN"
```

If a call returns `401`, the token expired or is missing — re-run the auth step
above before continuing. Don't try to repair a request other ways; just
re-authenticate.

> Tip: keep the credentials in your shell environment rather than inline, e.g.
> `EVALS_EMAIL` / `EVALS_PASSWORD`, and reference them in the `-d` payload.

---

## Resolve resources (always GET before you assume an ID exists)

```bash
# Agents — match by exact name, case-insensitive
curl -s "$BASE/api/agents"   -H "Authorization: Bearer $TOKEN"

# Personas / evaluators / test configs
curl -s "$BASE/api/personas"     -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/api/evaluators"   -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/api/test-configs" -H "Authorization: Bearer $TOKEN"
```

Filter client-side (e.g. with `python3 -c` / `jq`) by `name`, `client_tag`, or
`tags` to decide whether to reuse or create.

---

## Create a persona

```bash
curl -s -X POST "$BASE/api/personas" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "AI_generated-impatient-caller",
    "objective": "Book an appointment for next Tuesday and hang up if put on hold twice",
    "persona_type": "llm_conversational",
    "llm_config": {
      "stopping_criteria_rules": [
        "Stop when the appointment is confirmed",
        "Stop after the caller hangs up",
        "Stop if transferred to a human",
        "Stop after 3 failed authentication attempts"
      ]
    },
    "client_tag": "client:hcb",
    "tags": ["feature:appointments"]
  }'
```

## Create an evaluator

`tool_called` — binary check that a specific tool was invoked:

```bash
curl -s -X POST "$BASE/api/evaluators" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "AI_generated-called-book-appointment",
    "evaluator_type": "tool_called",
    "config": { "tool_name": "book_appointment" },
    "client_tag": "client:hcb",
    "tags": ["feature:appointments"]
  }'
```

`llm` — contextual / quality judgement:

```bash
curl -s -X POST "$BASE/api/evaluators" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "AI_generated-polite-and-on-topic",
    "evaluator_type": "llm",
    "config": { "criteria": "The agent stayed polite and never disclosed internal system details" },
    "client_tag": "client:hcb",
    "tags": ["feature:appointments"]
  }'
```

## Create a test config

```bash
curl -s -X POST "$BASE/api/test-configs" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "AI_generated-booking-smoke",
    "agent_id": "<agent_id>",
    "persona_ids": ["<persona_id>"],
    "evaluator_ids": ["<evaluator_id>"],
    "runs_per_persona": 1,
    "max_concurrency": 2,
    "timeout_seconds": 300
  }'
```

---

## Pre-flight: how busy is the platform?

```bash
# Fewer than 2 of these should be active before you launch
curl -s "$BASE/api/test-runs?status=running" -H "Authorization: Bearer $TOKEN"

# Best-effort phone-in-use check (no total/offset on this endpoint — use a high limit)
curl -s "$BASE/api/test-executions?status=running&limit=200" -H "Authorization: Bearer $TOKEN"
```

---

## Launch a run (JSON body, NOT a query param)

```bash
RUN_ID=$(curl -s -X POST "$BASE/api/test-runs" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"test_config_id": <test_config_id>}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')

echo "run: $RUN_ID"
```

> Passing only `?test_config_id=` returns
> `422 {"detail":[{"loc":["body","test_config_id"],"msg":"Field required"}]}`.

## Poll until terminal (completed / failed / cancelled)

```bash
while :; do
  STATUS=$(curl -s "$BASE/api/test-runs/$RUN_ID" -H "Authorization: Bearer $TOKEN" \
    | python3 -c 'import sys,json; print(json.load(sys.stdin)["status"])')
  echo "status: $STATUS"
  case "$STATUS" in completed|failed|cancelled) break;; esac
  sleep 10
done
```

## Cancel a run

```bash
curl -s -X POST "$BASE/api/test-runs/$RUN_ID/cancel" -H "Authorization: Bearer $TOKEN"
```

---

## Read results

```bash
# Overall run
curl -s "$BASE/api/test-runs/$RUN_ID" -H "Authorization: Bearer $TOKEN"

# Executions for the run
curl -s "$BASE/api/test-runs/$RUN_ID/executions" -H "Authorization: Bearer $TOKEN"

# Full detail of one execution (status, score, passed, transcript, evaluation_results, error_message)
curl -s "$BASE/api/test-executions/<execution_id>" -H "Authorization: Bearer $TOKEN"

# Audio for an execution
curl -s "$BASE/api/audio/<execution_id>" -H "Authorization: Bearer $TOKEN" -o execution.wav
```

---

## Re-score an execution without calling again

Fix an evaluator, then re-judge a conversation you already have. Weights are
required and must sum to exactly 100.

```bash
curl -s -X POST "$BASE/api/test-executions/<execution_id>/reevaluate" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"evaluator_assignments":[{"evaluator_id":617,"weight":40},
                                {"evaluator_id":620,"weight":60}]}'
```

Anything else answers
`422 {"detail":{"errors":["Weights must sum to exactly 100 (current sum: N)."]}}`.

---

## Read more than the last 100 executions

```bash
# Executions default to the most recent 100
curl -s "$BASE/api/test-executions?limit=500" -H "Authorization: Bearer $TOKEN"

# Runs are paginated: {items, total, page, page_size}
curl -s "$BASE/api/test-runs?page=1&page_size=100" -H "Authorization: Bearer $TOKEN"
```

---

## Was it the agent's fault? Read the metrics

```bash
curl -s "$BASE/api/test-executions/<execution_id>" -H "Authorization: Bearer $TOKEN" \
  | python3 -c '
import sys, json, ast, statistics as st
m = json.load(sys.stdin)["metrics"]
m = ast.literal_eval(m) if isinstance(m, str) else m
rt = m["response_times_ms"]["all_time_to_first_audio_ms"]
print(f"turns={m[\'conversation_turns\']} tools={m[\'tool_calls_count\']} cer={m[\'cer\']}%")
print(f"agent replies: p50={st.median(rt)/1000:.1f}s  peak={max(rt)/1000:.1f}s  over 10s={sum(1 for x in rt if x>10000)}")'
```

A peak of tens of seconds means the agent stalled — the persona had nothing to do
with it, and no amount of persona rewriting will fix that execution.

---

## Create a persona that can actually run

The API accepts a persona without `llm_config`; the runner then kills every
execution ~30s in with `Azure OpenAI rejected the request: invalid request.`
Copy this shape, or copy a persona that has completed a run recently.

```bash
curl -s -X POST "$BASE/api/personas" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "name": "AI_generated-<client>-<case>",
    "display_name": "<short label>",
    "objective": "<what the caller wants, and how they behave>",
    "persona_type": "llm_conversational",
    "preferred_language": "es",
    "client_tag": "client:<client>",
    "tags": ["feature:<area>"],
    "llm_config": {
      "model": "gpt-5.4-mini",
      "max_turns": 60,
      "temperature": 0.3,
      "expected_outcome": "<one sentence>",
      "stopping_criteria_mode": "any",
      "stopping_criteria_rules": [{"text": "hasta luego", "type": "phrase"}]
    },
    "conversation_script": [
      {"step": 1, "text": "<the caller opening line>", "action": "say"}
    ]
  }'
```

> `model` missing → the 30-second death. `stopping_criteria_rules` as plain
> strings → also wrong; each rule is an object.
