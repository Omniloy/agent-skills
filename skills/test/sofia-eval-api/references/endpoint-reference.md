# Endpoint reference

SofIA Evaluator (`sofia-evals-back`) REST API.

- **Base URL:** `http://localhost:8001` (local) or `$SOFIA_EVAL_URL` (deployed —
  the portal domain; nginx proxies `/api/` to the evaluator, which is not
  publicly reachable on its own).
- **Auth:** `Authorization: Bearer $EVALS_SKILL_TOKEN` on every `/api/v1/*`
  call. `/health` needs none.

## Reachable with the operator token

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness + active task count. No auth. |
| `GET` | `/api/v1/environments` | LangGraph deployments → `environment_id`. |
| `GET` | `/api/v1/evaluators` | Judge presets → `evaluator_id` (optional; defaults to OpenAI `gpt-4o-mini`). |
| `GET` | `/api/v1/rubrics?evaluation_type=&is_active=` | Find a reusable rubric. |
| `GET` | `/api/v1/rubrics/{id}` | Read one rubric's criteria. |
| `POST` | `/api/v1/rubrics` | Create a rubric. 400 if it breaks the grader's rules. |
| `GET` | `/api/v1/datasets?dataset_type=` | Find a dataset for the surface. |
| `GET` | `/api/v1/datasets/{id}` | Read one dataset. |
| `POST` | `/api/v1/datasets` | Create a dataset. |
| `GET` | `/api/v1/datasets/{id}/samples?limit=&offset=` | List samples. |
| `POST` | `/api/v1/datasets/{id}/samples` | Append samples (bulk, ≤500). |
| `GET` | `/api/v1/runs?status=&dataset_id=&evaluation_type=&is_baseline=` | List runs — find a baseline, see what's in flight. |
| `POST` | `/api/v1/runs` | Launch a run. **JSON body.** → `202 {run_id}`. |
| `GET` | `/api/v1/runs/{id}` | Status + aggregates. Poll this. |
| `GET` | `/api/v1/runs/{id}/items?status=&limit=&offset=` | Per-sample results + rubric criterion breakdown. |
| `GET` | `/api/v1/runs/{id}/comparison-items` | Per-sample head-to-head (`compare` runs). |
| `POST` | `/api/v1/gate-reports` | Generate a gate report → `{id, verdict}`. |
| `GET` | `/api/v1/gate-reports/{id}` | Full report: `summary.checks[]`, categories, criteria, regressions. |
| `GET` | `/api/v1/gate-reports?candidate_run_id=` | Reports for a candidate run. |
| `GET` | `/api/v1/assistant-gate-config?agent_type=` | Release-gate mapping per agent type. |

## NOT reachable with the operator token (portal admin only)

`/api/v1/api-keys` (judge provider keys), `/api/v1/audit`,
`/api/v1/assistants`. A `403` here is the design working, not a bug — never try
to route around it.

## Deprecated — do not use

`/evaluate/quality`, `/evaluate/compare`, `/evaluate/consistency`,
`/generate-gold`: the legacy path for the decommissioned `sofia-evals-app`
Next.js frontend. They assume the run row already exists and know nothing about
rubrics.

## Run states

- **Terminal:** `completed`, `failed`, `cancelled` — stop polling.
- **Non-terminal:** `pending`, `running` — poll every ~10s.
- **No cancel endpoint exists.** A launched run runs to completion or times out.

## Write semantics

Rubrics, datasets and samples are **append-only** — no PUT, PATCH or DELETE.
Completed runs reference them by id, so mutating one would rewrite the grading
contract of historical results. Retire by creating a replacement.

## Common error codes

| Code | Meaning | Action |
|---|---|---|
| `400` | Missing `environment_id`/`group_id`, or an invalid rubric | Read `detail` — it names the rule broken |
| `401` | Bad or unset token | Report; don't try other credentials |
| `403` | Portal-admin-only endpoint | You're not meant to be there |
| `404` | Unresolved id (dataset, rubric, run, environment) | Re-resolve with a GET; never invent ids |
| `500` + "must differ from EVALUATOR_AUTH_TOKEN" | Server misconfiguration | Report to the user |
