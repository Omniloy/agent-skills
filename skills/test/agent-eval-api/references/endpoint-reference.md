# Endpoint reference

Quick table for the Omniloy Agent Testing Platform API.

- **Base URL:** `https://mariaevals-dev.api.omniloy.com` (local: `http://localhost:8000`)
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
| `POST` | `/api/test-runs?test_config_id={id}` | Launch a run. **Query param, not JSON body.** Returns the run (`id`). |
| `GET`  | `/api/test-runs/{run_id}` | Run status (`pending`/`running`/`completed`/`failed`/`cancelled`). |
| `POST` | `/api/test-runs/{run_id}/cancel` | Cancel a run. |
| `GET`  | `/api/test-runs/{run_id}/executions` | List executions for a run. |
| `GET`  | `/api/test-executions?status=running` | Active executions (best-effort phone-in-use check; no `total`/`offset`). |
| `GET`  | `/api/test-executions/{execution_id}` | Full execution detail: `status`, `score`, `passed`, `transcript`, `evaluation_results`, `error_message`. |
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
