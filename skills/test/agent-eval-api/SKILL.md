---
name: agent-eval-api
description: Operate the Omniloy Agent Evaluator (Agent Testing Platform) API end-to-end — authenticate, resolve or create agents / personas / evaluators / test configs, launch a test run, poll it to a terminal state, and read back transcripts, scores and pass/fail. Use this whenever the user wants to test or evaluate an AI agent, run an eval, create or reuse a persona / evaluator / test config, launch or cancel a test run, check evaluation results, or mentions "agent evaluator", "evals platform", "test run", "persona", "evaluator", or "omniloy evals". It is also the execution half that `/live-testing-plan` hands off to (it produces the spec, this skill runs it). Usage - "/agent-eval-api test the booking agent" or just describe the agent + criteria to evaluate.
user-invocable: true
---

# Agent Evaluator API — Runbook

Procedural guide for operating the Omniloy **Agent Testing Platform** API.
Follow this order without skipping steps.

> For complete curl examples, see [references/curl-examples.md](references/curl-examples.md).
> For a quick endpoint table, see [references/endpoint-reference.md](references/endpoint-reference.md).
> **Before writing any persona or evaluator prompt**, read
> [references/persona-and-judge-recipes.md](references/persona-and-judge-recipes.md).
> Launching a run is the easy half; most of the time on a real campaign goes into
> personas that misread the agent and evaluators that judge the wrong thing.

---

## 1. MANDATORY rules

1. **Never invent IDs** — always resolve with a GET first (`agent_id`, `persona_id`, `evaluator_id`, `test_config_id`, tool names).
2. **Fixed order** — auth → resolve agent → persona → evaluator → config → pre-flight → run → results → report.
3. **Reuse before creating** — if an existing resource covers the case, use it. When in doubt, create a new one.
4. **`AI_generated-` prefix** — every new resource created by AI must start with that prefix.
5. **Concurrency** — max 2 simultaneous runs. If you pin `caller_phone_number`, set `max_concurrency = 1`.
6. **If the user already named the agent**, don't ask again.
7. **A failing evaluator is not a failing agent.** Before reporting a defect,
   check in this order: did the persona misunderstand or mispronounce something,
   is the evaluator judging outside its scope, and only then, did the agent
   actually do it wrong. See
   [references/persona-and-judge-recipes.md](references/persona-and-judge-recipes.md).

---

## 2. Shared vs owned

| Situation | Action |
|---|---|
| Shared resource that already fits | use it |
| Shared but something needs to change | create a new owned one |
| `PUT`/`DELETE` returns `403` saying a shared resource can't be modified or deleted | stop retrying and create a new owned one |
| Owned resource | safe to edit |

**Short rule: shared = use, don't edit. owned = use and edit.**

### Tagging

- `client_tag`: starts with `client:` (e.g. `client:hcb`, `client:all`)
- `tags`: start with `feature:` (e.g. `feature:appointments`, `feature:insurance`)
- Don't put client tags inside `tags`.

---

## 3. Base URL and auth

- **Default: `https://mariaevals.api.omniloy.com`** — this is the platform the
  user sees, and the one to use unless they say otherwise.
- Local: `http://localhost:8000`

> `https://mariaevals-dev.api.omniloy.com` is a **separate deployment with its own
> database and its own credentials** — not an alias. A token issued by one host
> returns `401` on the other, and resources you create there won't appear in the
> platform the user is looking at. Only use it if the user asks for it by name.

> Each host serves both the web app (the "Agent Testing Platform" SPA at `/`) and
> the REST API under `/api/...`. Always hit the `/api/...` paths — the bare paths
> return the SPA's HTML, not JSON.

> **Quotes can trip a WAF.** There is a web application firewall in front of the
> API that answers a bare `403 Access Forbidden` (an HTML body, not the JSON
> `{"detail": ...}` the API returns) for some payloads containing quote
> characters. If a `PUT`/`POST` that looks correct gets that, rewrite the prompt
> or persona text without quotes rather than retrying.

Authenticate with `POST /api/auth/token`, sending a JSON body with `email` and
`password`, and use the returned `access_token` as a Bearer token on every
subsequent request. If you get a `401`, re-authenticate before continuing.

> Full auth flow (cookies, refresh token, env-var convention): see
> [curl-examples.md](references/curl-examples.md#auth).

---

## 4. Resolve the agent

1. `GET /api/agents` to list.
2. Match by exact `name` (case-insensitive).
3. 1 match → use it. 0 matches → look for a close one or report. >1 match → ask for clarification.

---

## 5. Resolve or create a persona

1. `GET /api/personas` to look for compatible ones.
2. Reuse only if it matches: objective, constraints, flow type.
3. If you create a new one:
   - `persona_type = "llm_conversational"` for conversational tests.
   - A clear, focused `objective`.
   - `llm_config.stopping_criteria_rules` is required — don't leave it only as free text.
   - Cover at minimum: success, hang up, transfer, authentication loop.

Key fields: `name`, `objective`, `persona_type`, `llm_config`, `client_tag`, `tags`.

---

## 6. Resolve or create evaluators

1. `GET /api/evaluators` to look for compatible ones.
2. Pick the type:
   - **`tool_called`** for binary criteria (did it call this tool?).
   - **`llm`** for contextual judgement or quality.
3. **One evaluator = one criterion.**
4. For `tool_called`, confirm the tool name from documentation or a previous transcript.

Key fields: `name`, `evaluator_type`, `config`, `client_tag`, `tags`.

---

## 7. Resolve or create the test config

1. `GET /api/test-configs` to look for compatible ones.
2. Reuse only if these match EXACTLY: `agent_id`, `persona_ids`, `evaluator_ids`, `runs_per_persona`, `max_concurrency`, `timeout_seconds`, `caller_phone_number`.
3. If even one differs, create a new one.

Key fields: `name`, `agent_id`, `persona_ids`, `evaluator_ids`, `runs_per_persona`, `max_concurrency`, `timeout_seconds`.

---

## 8. Pre-flight checks

Do NOT launch the run without verifying:

- [ ] I have a valid `TOKEN`
- [ ] I have `agent_id`
- [ ] I have `persona_ids`
- [ ] I have `evaluator_ids`
- [ ] I have `test_config_id`
- [ ] There are fewer than 2 active runs (`GET /api/test-runs?status=running`)
- [ ] If I'm pinning a phone number, it isn't in use by active executions (`GET /api/test-executions?status=running`) — best-effort check: this endpoint currently returns no `total` or `offset`, so use a high `limit` and, when in doubt, avoid pinning the phone number

---

## 9. Launch the run

`POST /api/test-runs` with a **JSON body**: `{"test_config_id": <id>}`.

A query param alone is rejected — the server answers
`422 {"detail":[{"loc":["body","test_config_id"],"msg":"Field required"}]}`.
An unknown id answers `404 Test configuration not found`.

Save the `run_id` from the response.

---

## 10. Wait for the result

Terminal states: `completed`, `failed`, `cancelled`.
`pending` and `running` are NOT terminal — keep polling every ~10s.

To cancel: `POST /api/test-runs/{run_id}/cancel`.

---

## 11. Read the results

1. `GET /api/test-runs/{run_id}` — overall status.
2. `GET /api/test-runs/{run_id}/executions` — list of executions.
3. `GET /api/test-executions/{execution_id}` — full detail.

Read at minimum: `status`, `score`, `passed`, `transcript`, `evaluation_results`, `error_message`.

Audio: `GET /api/audio/{execution_id}`.

**Listings are capped.** `GET /api/test-executions` returns the most recent 100
unless you pass `?limit=N` (500 works). `GET /api/test-runs` is paginated
differently — `?page=N&page_size=100`, and the response is
`{items, total, page, page_size}`. Assuming the default page is everything is how
you conclude a run never happened.

**`metrics` is where the diagnosis lives.** The execution detail carries a
`metrics` object that tells you whether a failure was the agent's fault:

| field | what it tells you |
|---|---|
| `response_times_ms.all_time_to_first_audio_ms` | per turn, how long the agent took to make a sound after the persona stopped talking. Long tail here = the agent stalled, not that the persona misbehaved |
| `llm_p50_ttft_s` / `llm_p95_ttft_s` | the agent's own LLM latency |
| `conversation_turns`, `tool_calls_count` | how far the conversation actually got |
| `cer` | character error rate of the transcription — a high value means the judges are reading a garbled transcript |
| `silence_timeout_count`, `overlap_rate` | the persona going quiet, or the two talking over each other |

---

## 11.b Fixing an evaluator without repeating the call

`POST /api/test-executions/{execution_id}/reevaluate` re-scores a **recorded**
execution against a different set of evaluators:

```json
{"evaluator_assignments": [{"evaluator_id": 617, "weight": 40},
                           {"evaluator_id": 620, "weight": 60}]}
```

The weights are required and **must sum to exactly 100** — the server rejects
anything else with `Weights must sum to exactly 100 (current sum: N)`.

**This should be the default loop once a conversation reaches the end.** A real
call costs minutes and can die for reasons that have nothing to do with the
criterion under test; re-evaluating takes seconds and isolates the evaluator from
the conversation. Get **one** conversation that finishes, then do all the
evaluator work on top of it.

Re-evaluate the same execution **twice** before trusting a verdict. An evaluator
whose scope is loose will happily give 95 and then 20 on identical text; if two
passes disagree, the fix is the prompt, not the retry.

---

## 12. What to do if something fails

| Problem | Action |
|---|---|
| `401` | Re-authenticate |
| `403` saying a shared resource can't be modified or deleted | Create a new owned resource |
| Run `failed` | Read `error_message` + the execution detail |
| Timeout | Raise `timeout_seconds` and retry. Check `metrics.response_times_ms` first: if the agent stalled for tens of seconds, more time only buys a longer failure |
| `403 Access Forbidden` (HTML, not JSON) | A WAF rejected the payload — rewrite the text without quote characters |
| An evaluator's verdict contradicts the transcript | Re-evaluate the same execution again; if it flips, tighten the evaluator's scope |
| Still `pending`/`running` | Keep polling or report |

**Don't close the case without inspecting the detail if there was a failure.**

---

## 13. Report to the user

ALWAYS include:

- `agent_id` and name
- `persona_ids` and `evaluator_ids` (reused or created)
- `test_config_id` and `run_id`
- Final run status
- `score` and `passed` per execution
- Transcript evidence

---

## 14. Final checklist

Don't say "done" unless you can answer with real data:

- Which user did I authenticate as?
- Which `agent_id` did I use?
- Which `persona_id` and `evaluator_id` did I reuse or create?
- Which `test_config_id` did I use?
- Which `run_id` was launched?
- Final run status?
- `score` and `passed` per execution?
- Transcript evidence?
