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

---

## 1. MANDATORY rules

1. **Never invent IDs** — always resolve with a GET first (`agent_id`, `persona_id`, `evaluator_id`, `test_config_id`, tool names).
2. **Fixed order** — auth → resolve agent → persona → evaluator → config → pre-flight → run → results → report.
3. **Reuse before creating** — if an existing resource covers the case, use it. When in doubt, create a new one.
4. **`AI_generated-` prefix** — every new resource created by AI must start with that prefix.
5. **Concurrency** — max 2 simultaneous runs. If you pin `caller_phone_number`, set `max_concurrency = 1`.
6. **If the user already named the agent**, don't ask again.

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

- Production / staging: `https://mariaevals-dev.api.omniloy.com`
- Local: `http://localhost:8000`

> The host `https://mariaevals-dev.api.omniloy.com` serves both the web app (the
> "Agent Testing Platform" SPA at `/`) and the REST API under `/api/...`. Always
> hit the `/api/...` paths — the bare paths return the SPA's HTML, not JSON.

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

`POST /api/test-runs?test_config_id={id}` — pass it as a query param, **NOT** a JSON body.

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

---

## 12. What to do if something fails

| Problem | Action |
|---|---|
| `401` | Re-authenticate |
| `403` saying a shared resource can't be modified or deleted | Create a new owned resource |
| Run `failed` | Read `error_message` + the execution detail |
| Timeout | Raise `timeout_seconds` and retry |
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
