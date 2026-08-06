---
name: sofia-eval-api
description: Operate the SofIA Evaluator (sofia-evals) API end-to-end — pick local or deployed backend, authenticate with the least-privilege operator token, resolve or create rubrics / datasets / samples, launch a rubric evaluation run, poll it to a terminal state, and read back scores with the per-criterion breakdown, plus release gate reports against a baseline. Use this whenever the user wants to evaluate a SofIA agent, run or check a SofIA eval, create a rubric or eval dataset, gate a release against a baseline run, or mentions "sofia evals", "rubric eval", "gate report", "release gate", "evaluation run", or the surfaces "coding" / "patient_summary" / "medical_chat" / "note_generation". For the MarIA/voice Agent Testing Platform (personas, phone calls, transcripts) use `agent-eval-api` instead. Usage - "/sofia-eval-api evaluate the note agent against the ACI dataset".
user-invocable: true
---

# SofIA Evaluator API — Runbook

Procedural guide for operating the **SofIA Evaluator** (`sofia-evals-back`).
Follow the phases in order without skipping.

> Endpoint table: [references/endpoint-reference.md](references/endpoint-reference.md)
> Copy-paste curl: [references/curl-examples.md](references/curl-examples.md)
> Writing rubrics: [references/rubric-guide.md](references/rubric-guide.md)
> Surfaces + agent input contract: [references/surfaces-reference.md](references/surfaces-reference.md)
> Running the backend locally: [references/local-setup.md](references/local-setup.md)

---

## 1. MANDATORY rules

1. **Rubric surfaces first.** `coding`, `patient_summary`, `medical_chat` and
   `note_generation` grade against a rubric and are the current design. If the
   user asks for `extraction` / `patient_data` / `transcription`, say those are
   the legacy 0–100 judge path and offer the rubric surface that fits.
2. **Never invent IDs** — resolve every `dataset_id`, `rubric_id`,
   `evaluator_id`, `environment_id`, `assistant_id` with a GET first.
3. **Reuse before creating.** Search the catalog for a rubric/dataset that
   already covers the case. Create only when nothing fits.
4. **`AI_generated-` prefix** on every rubric and dataset you create, so humans
   can tell your artifacts from theirs.
5. **Append-only.** There is no update or delete. Completed runs reference
   rubrics and samples by id — never try to edit one, create a replacement.
6. **Never handle provider secrets.** Judge API keys are resolved server-side.
   If a task seems to need `/api/v1/api-keys`, stop and tell the user: the
   operator token deliberately cannot reach it.
7. **There is no cancel endpoint.** Don't promise the user you can stop a run.

---

## 2. Phase 0 — Pick the mode

Two deployments, same runbook, different base URL and credentials.

1. If the user names the mode, obey.
2. Otherwise probe local: `GET http://localhost:8001/health`.
   - Responds → **local mode**.
   - Doesn't → **deployed mode** (needs `SOFIA_EVAL_URL` configured).
3. If neither is reachable, offer to start the backend locally
   ([local-setup.md](references/local-setup.md)) rather than guessing a URL.

| | Local | Deployed |
|---|---|---|
| Base URL | `http://localhost:8001` | `$SOFIA_EVAL_URL` (the portal domain — nginx proxies `/api/` to the evaluator, which is not publicly reachable on its own) |
| Token | `EVALS_SKILL_TOKEN` from the backend's `.env` | `EVALS_SKILL_TOKEN` from the environment |
| Data | Supabase **cloud** — shared with everyone | Same |

**Local does not mean private.** The backend has no local database; both modes
write to the same shared Supabase. Keep the `AI_generated-` prefix and don't
gate a real release on a local experiment.

---

## 3. Phase 1 — Authenticate

Send `Authorization: Bearer $EVALS_SKILL_TOKEN` on every `/api/v1/*` call.

This is the **eval-operator identity**: it can read the catalog, create
rubrics/datasets/samples, launch runs, and read results and gate reports. It
cannot reach judge provider keys, the audit log, or the assistants endpoint —
by design.

- `401` → the token is wrong or unset. Report it; don't try other credentials.
- `403` → you hit a portal-admin-only endpoint. You're not supposed to be
  there — re-read the endpoint table.
- `500 "EVALS_SKILL_TOKEN must differ from EVALUATOR_AUTH_TOKEN"` → server
  misconfiguration. Report it to the user; it is not something you can work
  around.

---

## 4. Phase 2 — Resolve the catalog

Four ids to pin down before anything else. Always GET first.

| What | How | Notes |
|---|---|---|
| `environment_id` | `GET /api/v1/environments` | The LangGraph deployment under evaluation. **Preferred** over the legacy `group_id`. |
| `assistant_id` | from the user, or the environment's deployment | The graph to evaluate. Ask if ambiguous — don't guess. |
| `evaluator_id` | `GET /api/v1/evaluators` | Judge preset. Optional: omitting it defaults to OpenAI `gpt-4o-mini`. |
| `dataset_id` | `GET /api/v1/datasets?dataset_type=<surface>` | Must match the surface you're evaluating. |

Then resolve the rubric: `GET /api/v1/rubrics?evaluation_type=<surface>&is_active=true`.

**Rubric precedence** (most specific wins): `run.rubric_id` → the sample's
`metadata.rubric` → the dataset's `rubric_id`. Pass `rubric_id` on the run when
you want to grade an existing dataset against different criteria.

---

## 5. Phase 3 — Create what's missing

Only after Phase 2 found nothing reusable.

**Rubric** — `POST /api/v1/rubrics`. The server enforces the grader's rules and
400s if you break them: unique criterion ids, `points` sign matching `polarity`,
and **at least one positive AND one negative (safety) criterion**. Read
[rubric-guide.md](references/rubric-guide.md) before writing criteria — a vague
rubric produces noise, not signal.

**Dataset** — `POST /api/v1/datasets` with `dataset_type` = the surface,
`source: "custom"`, and `rubric_id` as the default rubric.

**Samples** — `POST /api/v1/datasets/{id}/samples` (bulk, up to 500). Shape the
`input` for the surface ([surfaces-reference.md](references/surfaces-reference.md)):
`medical_chat` takes `{question, patient_data}`; extraction-style surfaces take
`{transcription}` and require a `json_schema` on the run.

---

## 6. Phase 4 — Pre-flight

Do NOT launch without confirming:

- [ ] Mode chosen and `/health` answered
- [ ] `dataset_id` resolved AND it has samples (`GET /datasets/{id}/samples`)
- [ ] `environment_id` (or legacy `group_id`) — one is required, else 400
- [ ] `assistant_id`
- [ ] Rubric resolvable through one of the three precedence levels
- [ ] `json_schema` in hand if the surface is not `medical_chat`
- [ ] `assistant_id_2` if `run_type` is `compare`
- [ ] Nothing heavy already running (`GET /api/v1/runs?status=running`)

---

## 7. Phase 5 — Launch

`POST /api/v1/runs` with a **JSON body** → `202 {"run_id": ...}`.

Key fields: `name`, `run_type` (`quality` | `compare` | `consistency`),
`evaluation_type` (the surface), `dataset_id`, `assistant_id`,
`environment_id`, `rubric_id`, `json_schema`, `concurrency` (1–50, default 5).

Release gate: `is_baseline` on the reference run, then `baseline_run_id` +
`release_label` on the candidate.

---

## 8. Phase 6 — Poll

`GET /api/v1/runs/{run_id}` every ~10s.

- Terminal: `completed`, `failed`, `cancelled`.
- Non-terminal: `pending`, `running` — report progress as
  `completed_samples/total_samples`.

Failure semantics worth knowing:

| Symptom | Meaning |
|---|---|
| `failed` with "All samples failed" | Every sample errored — read the items, it's usually a bad `json_schema` or an unreachable assistant |
| Stuck `running` >30 min | The orphan sweep will mark it `failed`; the run is not coming back |
| Individual sample never finishes | 420s per-sample wall timeout (scaled for transcription) |

---

## 9. Phase 7 — Read the results

1. `GET /api/v1/runs/{run_id}` — aggregates: `average_score`, `min_score`,
   `max_score`, `gate_status`, and wins/ties for compare runs.
2. `GET /api/v1/runs/{run_id}/items` — per sample: `score`, `reasoning`, and
   **`metrics.rubric_results[]`** with `criterion_id` / `met` / `reason` /
   `points_applied`, plus `earned_positive`, `triggered_negative`,
   `max_positive`.
3. `GET /api/v1/runs/{run_id}/items?status=error` — go straight to failures.
4. `GET /api/v1/runs/{run_id}/comparison-items` — head-to-head for `compare`.

**Rubric scores are not percentages.**
`raw_score = (earned_positive + triggered_negative) / max_positive`, and it can
go **negative** when penalties exceed what was earned. Never present it as a
0–100 grade. Legacy non-rubric surfaces do score 0–100 — don't mix the two in
one table without saying which is which.

---

## 10. Phase 8 — Release gate (when asked)

1. A baseline run exists (`is_baseline: true`) and completed.
2. Launch the candidate with `baseline_run_id` + `release_label`.
3. `POST /api/v1/gate-reports` with `{candidate_run_id, thresholds}` →
   `{id, verdict}` where verdict is `passed` or `blocked`.
4. `GET /api/v1/gate-reports/{id}` for the reasoning: `summary.checks[]` (each
   with `metric_class` quality/safety, `passed`, `detail`),
   `summary.categories[]`, `summary.criteria[]`, `regressions`.

A verdict without the failing check quoted is not a useful answer — always read
the report back.

---

## 11. Report to the user

ALWAYS include:

- Mode (local / deployed) and base URL
- Surface + `run_type`
- Which rubric, dataset, environment and assistant were used — and for each,
  **reused or created** (with ids)
- `run_id` and final status
- `average_score` (stating the rubric scale) and the sample spread
- The criterion breakdown for at least the worst-scoring samples: which
  criteria failed and the judge's stated reason
- Gate verdict + the specific check that blocked, if a gate ran

---

## 12. Final checklist

Don't say "done" without real data for each:

- Which mode and which base URL?
- Which surface, and is it rubric-based?
- Which rubric id, and did I reuse or create it?
- Which dataset id, and how many samples?
- Which `run_id`, and what terminal status?
- What is the aggregate score, on which scale?
- Which criteria failed, and what reason did the judge give?
- If gated: which verdict, and which check decided it?
