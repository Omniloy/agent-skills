# Running the SofIA Evaluator locally

Repo: `~/sofia-evals/sofia-evals-back` (FastAPI, Python ≥3.11, `uv`).

> **Local is not private.** The backend has no local database — it talks to the
> same shared Supabase as the deployed one. Everything you create is visible to
> the team and counts against real data. Keep the `AI_generated-` prefix and
> never gate a real release on a local experiment.

## 1. Check the environment

The service needs a `.env` in the repo root (loaded before any module reads
env vars). Required for the skill's flow:

| Var | Why |
|---|---|
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | All persistence |
| `EVALS_SKILL_TOKEN` | The operator bearer this skill uses. Must differ from `EVALUATOR_AUTH_TOKEN` or requests are refused |
| `EVALS_SKILL_USER_ID` | UUID stamped as `created_by`; must exist in `public.portal_users` |
| `OPENAI_API_KEY` / `AZURE_OPENAI_*` | Only as fallback — judge keys normally resolve from `evals.api_keys` |

If `.env` is missing or `EVALS_SKILL_TOKEN` is unset, **stop and tell the
user** — do not invent credentials or fall back to another identity.

Generate a token: `openssl rand -hex 32`.

## 2. Start it

```bash
cd ~/sofia-evals/sofia-evals-back
uv sync                 # only if .venv is missing
.venv/bin/uvicorn app.main:app --reload --port 8001
```

Run it in the background so you can keep working; it stays up across steps.

## 3. Verify before doing anything else

```bash
curl -s http://localhost:8001/health          # {"status":"ok","active_tasks":0}
curl -s http://localhost:8001/api/v1/rubrics \
  -H "Authorization: Bearer $EVALS_SKILL_TOKEN" | head -c 200
```

A 200 on the second call means both the service and the token are good.
Interactive API docs: <http://localhost:8001/docs>.

## 4. Queue mode (optional)

With `EVALUATOR_USE_QUEUE=true` the API only enqueues; a separate worker
processes runs:

```bash
.venv/bin/python -m app.worker
```

If runs sit at `pending` forever, this is the usual cause. For local work the
simpler setup is queue **off** (the default), where the API dispatches in-process.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `/health` refuses connection | Service not running, or on another port |
| `401` on `/api/v1/*` | `EVALS_SKILL_TOKEN` mismatch between your shell and the server's `.env` — the server reads it per request, so re-check the file, not the process |
| `500 "must differ from EVALUATOR_AUTH_TOKEN"` | Both vars share a value; give the skill token its own secret |
| `RuntimeError: SUPABASE_SERVICE_ROLE_KEY is not set` | `.env` missing or not in the repo root |
| Runs stay `pending` | Queue mode on with no worker running |
| `Address already in use` | Something already on 8001 — reuse it (check `/health`) rather than starting a second instance |
