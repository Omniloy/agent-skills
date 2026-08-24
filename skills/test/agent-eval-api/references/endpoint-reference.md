# Endpoint reference

Quick table for the Omniloy Agent Testing Platform API.

- **Base URL:** `https://mariaevals.api.omniloy.com` (local: `http://localhost:8000`).
  `https://mariaevals-dev.api.omniloy.com` is a **different deployment** with its
  own database and credentials — tokens are not interchangeable.
- **All paths live under `/api/...`.** The bare host serves the web SPA, not JSON.
- **Auth:** every endpoint except `POST /api/auth/token` requires
  `Authorization: Bearer <access_token>`. A `401` means re-authenticate.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/auth/token` | Log in with `{email, password}` → returns `access_token`. |
| `GET`  | `/api/agents` | List agents (resolve `agent_id` by exact name). |
| `GET`  | `/api/personas` | List personas. |
| `POST` | `/api/personas` | Create a persona (`AI_generated-` prefix). |
| `GET`  | `/api/evaluators` | List evaluators. |
| `POST` | `/api/evaluators` | Create an evaluator (one criterion each). |
| `GET`  | `/api/test-configs` | List test configs. |
| `POST` | `/api/test-configs` | Create a test config binding agent + personas + evaluators. |
| `GET`  | `/api/test-runs?status=running` | Count active runs (pre-flight; keep < 2). |
| `POST` | `/api/test-runs` | Launch a run. **JSON body** `{"test_config_id": <id>}` — a query param alone returns `422`. Returns the run (`id`). |
| `GET`  | `/api/test-runs/{run_id}` | Run status (`pending`/`running`/`completed`/`failed`/`cancelled`). |
| `POST` | `/api/test-runs/{run_id}/cancel` | Cancel a run. |
| `GET`  | `/api/test-runs/{run_id}/executions` | List executions for a run. |
| `GET`  | `/api/test-executions?status=running` | Active executions (best-effort phone-in-use check; no `total`/`offset`). |
| `GET`  | `/api/test-executions?limit={n}` | Execution list. **Defaults to the last 100** — pass `limit` (500 works) or you will silently miss runs. |
| `GET`  | `/api/test-runs?page={n}&page_size={m}` | Paginated runs: `{items, total, page, page_size}`. |
| `POST` | `/api/test-executions/{id}/reevaluate` | Re-score a recorded execution: `{"evaluator_assignments":[{"evaluator_id":N,"weight":W}]}`. **Weights must sum to exactly 100.** No new call is placed. |
| `GET`  | `/api/test-executions/{execution_id}` | Full execution detail: `status`, `score`, `passed`, `transcript`, `evaluation_results`, `error_message`, and `metrics` (latency, turns, tool calls, `cer`). |
| `GET`  | `/api/audio/{execution_id}` | Audio recording of an execution. |

## Terminal vs non-terminal run states

- **Terminal:** `completed`, `failed`, `cancelled` — stop polling.
- **Non-terminal:** `pending`, `running` — keep polling every ~10s.

## Concurrency limits

- Max **2** simultaneous runs.
- If `caller_phone_number` is pinned on the test config, set `max_concurrency = 1`.

## Shared vs owned (403 handling)

A `PUT`/`DELETE` that returns `403` because the resource is **shared** means it
can't be modified or deleted — stop retrying and create a new **owned** resource
instead (with the `AI_generated-` prefix).

## Gotchas that cost real time

- **The WAF.** A bare `403 Access Forbidden` with an HTML body (not the API's
  JSON `{"detail": ...}`) means a firewall rejected the payload, typically over
  quote characters in a persona or evaluator prompt. Rewrite without quotes.
- **Negative `points` don't work.** An evaluator scored with negative `points`
  has the sign discarded when it is converted to a weight, so a criterion phrased
  as "this bad thing must NOT happen" is scored as if you wanted it to happen.
  Always phrase evaluators positively.
- **`GET` listings are truncated by default** — see `limit` / `page_size` above.
