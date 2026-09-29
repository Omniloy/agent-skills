# The evaluation platform, and maria-voice's tooling around it

Generic API mechanics live in the `agent-eval-api` skill:
- auth and endpoints;
- reuse-before-create and shared-vs-owned resources;
- pre-flight, launching, polling and reading results;
- `reevaluate`;
- persona and judge recipes.

This file covers what is specific to evaluating MarIA customer workflows.

## Contents

1. [Hosts](#1-hosts)
2. [What an eval actually tests](#2-what-an-eval-actually-tests)
3. [The code: scenarios module, bootstrap, manifest, runner](#3-the-code)
4. [Platform constraints](#4-platform-constraints)
5. [Running a campaign](#5-running-a-campaign)
6. [Legacy (non-workflow) agents](#6-legacy-non-workflow-agents)

---

## 1. Hosts

| host | what | use |
|---|---|---|
| `https://mariaevals.api.omniloy.com` | the platform's main deployment (env slug `prod` in `EvalsClient`) | **default**. The customer agents (`San Roque DEV/STG`, `Blue DEV/STG`, `HCB DEV/STG`, `Premium DEV/STG`…) live here |
| `https://mariaevals-dev.api.omniloy.com` | a separate deployment with its OWN database | old resources only; do not create new ones there |

- The two hosts do not share ids: the same resource has different ids in each. **Resolve
  everything by name**, never carry an id across hosts.
- A token from one host is 401 on the other.
- Credentials: `EVALS_USER_NAME` / `EVALS_PASSWORD` in maria-voice's `.env`. They authenticate
  as the shared `maria.team` account, and resources are created under it.
- The `.env` historically pointed `EVALS_API_BASE*` at `-dev`. `EvalsClient("prod")` resolves
  to the main host.

## 2. What an eval actually tests

The platform's agent record carries a LiveKit URL and a `maria_center_id` (the api_key), and
nothing else. So an eval call reaches **the worker DEPLOYED in that LiveKit environment,
serving the flow that api_key has as default in that environment's database**:
- not your worktree;
- not a sandbox clone;
- not a specific version.

Check before a campaign, and write it into the report:

```sql
-- in the environment the agent points at (dev / stg)
select voice_mode, default_conversation_flow_id from api_keys where id = '<maria_center_id>';
select version from conversation_flows where id = '<that flow>';
```

On 2026-09-29, `San Roque DEV` served flow `2da05455` v22 and `San Roque STG` served v40,
while production was at v43 and the sandbox clone at v51.

Consequences:
- **To evaluate a sandbox clone, it has to be what the api_key serves in that environment.**
  Activating a clone in stg changes stg for everyone who calls that tenant there, so it needs
  the user's OK. Otherwise, evaluate after promotion.
- Eval results are only comparable across runs of the same deployed code + flow version. Stamp
  both in the campaign report.
- **Transfers always fail in web mode.** There is no SIP bridge, and the agent may say so.
  Every transfer judge must carry that note (`_WEB_TRANSFER_NOTE`).

## 3. The code

In maria-voice, `tests/integration/customers/_evals/` (underscore: pytest does not collect
it):

| file | role |
|---|---|
| `api.py` | `EvalsClient(env)`: auth with a per-env token cache, CRUD, runs, polling |
| `bootstrap.py` | `python -m tests.integration.customers._evals.bootstrap <suite> --env prod`. Idempotent and auto-syncing: creates what is missing, PUT-updates what exists **by name**, never deletes. Applies `_waf_safe`. Writes `evals_manifest_<env>.json` with a `source_excerpt_hash` per scenario |
| `scenarios_<suite>.py` | **the source of truth** for a suite's platform resources |
| `evals/eval_<suite>.py` (repo root) | the runner: drift check → bootstrap → launch (≤2 concurrent, `SEQUENTIAL_GROUP` serialised) → live trace + JSONL log → summary; `--only`, `--resume`, `--summary-only`, `--env` |

**The scenarios module contract:**

```python
SUITE = "<customer>_<flow>"                  # module stem; resources AI_generated-<SUITE>-<slug>
CLIENT_TAG = "client:<customer>"
SUITE_TAGS = ["feature:customer-flow-eval", "feature:emulated"]
AGENT_NAME_BY_ENV = {"prod": "<Customer> STG", ...}   # key = PLATFORM env, value = agent name
def agent_name_for_env(env): ...
TEST_FILES = ["test_<customer>_paths.py", ...]         # optional; default test_<SUITE>.py
SCENARIOS = [ {"slug", "source_tests", "persona", "evaluators", "test_config"}, ... ]
DROPPED = [ {"source_tests": [...], "reason": "..."}, ... ]
```

- `persona`: `display_name`, `description`, `objective`, `preferred_language`, `tags`,
  `conversation_script` (the scripted first line: **always set it**, see
  `persona-and-judge.md` §2), and `llm_config`:
  - `model` `gpt-5.4-mini`;
  - `max_turns`;
  - `stopping_criteria_mode` `or`;
  - `stopping_criteria_rules`;
  - optionally `identification_transcription`.
- `evaluators`: `llm` with `model` `gpt-5.4-mini` and `threshold`, or `tool_called` with
  `tool_names` and `match_all`. A scenario can instead reuse an existing resource via
  `reuse_id`.
- `test_config`: `runs_per_persona`, `max_concurrency`, `timeout_seconds`, and
  `caller_phone_number` when needed. `reuse_test_config_id` points at an existing config
  instead of creating one.
- Scenario modules already exist for:
  - `scenarios_hcb.py` (legacy);
  - `scenarios_san_roque.py` (the OLD directory flow, not Citación);
  - `scenarios_blue_citacion.py` (branch `test/blue-citacion-suite`, the workflow pattern);
  - `scenarios_san_roque_citacion.py`.

## 4. Platform constraints

- **Models**: `gpt-5.4-mini` for BOTH `llm` evaluators and personas.
  - It is the only model provisioned for judges.
  - `gpt-4.1` personas answered HTTP 500 on every turn on 2026-09-29, so the persona never
    spoke. When executions fail with `LLM error: Error code: 500`, check the persona's model
    against a config that ran recently before touching anything else.
- **Tags** must start with `feature:`. The customer goes in `client_tag`.
- **WAF (403) on bodies** with ASCII quotes, word-adjacent slashes (`DNI/NIE`), or dense
  quoted English imperatives.
  - `_waf_safe()` fixes the first two.
  - For the third, rephrase the text.
  - If a PUT still 403s, the bootstrap falls back to an `llm_config`-only PUT.
- **Shared resources** are read-only (403 on PUT/DELETE): clone them.
- **Concurrency**:
  - at most 2 runs at once (pre-flight `GET /api/test-runs?status=running`);
  - `max_concurrency: 1` for any config that pins a phone or shares backend state.
- **Timeouts**: booking flows take 15–25 min, so set `timeout_seconds` to 1200–1800. A short
  FAQ or transfer run needs 420–800.
- **Negative `points` drop their sign.** Write every criterion positively.
- **Stopping guardrail**: `stop_conversation` is only accepted when the agent's last turn
  contains a confirmation or transfer keyword. On rejection, the platform forces the persona
  to ask `¿Entonces ya está confirmada {stopping_criteria_rules[0].text}?`. See
  `persona-and-judge.md` §1.

## 5. Running a campaign

1. **Pre-flight**:
   - the deployed flow and version (§2);
   - no more than 2 active runs;
   - the identities exist in the test backend;
   - the agent resolves by name.
2. **Bootstrap**, then check the manifest: created/updated counts, and dropped tests listed.
3. **Launch a SMALL batch first**, 1–2 scenarios, and read the transcripts before launching
   the rest. Most first-run failures are the persona or the judge.
4. **Triage each red execution in this order**:
   1. did the call reach the point under test? If not: persona (script, stop rules) or
      the agent's latency (`all_time_to_first_audio_ms` peaks);
   2. did the judge answer the question asked? Re-run the judge with
      `POST /api/test-executions/{id}/reevaluate` (weights must sum to 100). Do not repeat
      the call;
   3. only then is it the agent. Classify it with `maria-workflow-builder` →
      `defect-catalog.md`, and either hand it to a flow fix or record a KNOWNFAIL.
5. **Check judge stability**: re-evaluate the same execution twice. The same judge has given
   95 and then 20 before it was scoped.
6. **Report**:
   - the flow version and environment tested;
   - per scenario: run, execution, score, pass;
   - KNOWNFAILs with their evidence;
   - DROPPED tests;
   - resource ids (by name).

   Publish it if others will read it.
7. **Clean up** what the calls wrote in the customer's test backend.

## 6. Legacy (non-workflow) agents

HCB and Premium run the legacy `base_agent`. Their scenarios (`scenarios_hcb.py`) rely on
`@function_tool` names:
- `transfer_to_human`, `hang_up`, `increase_voice_speed`/`decrease_voice_speed`;
- `change_language`, `lookup_company_info`;
- `login_and_onboard`, `schedule_appointment`, `select_appointment_to_reschedule`,
  `cancel_scheduled_appointment`.

Since the MCP server was retired, those SINA tools are served by the worker's integration
package under the same names. HCB-specific traps:
- a 15-minute verification cache per phone, so pin a dedicated phone per identity scenario;
- a WhatsApp confirmation after booking that the persona must wait for before a second
  action;
- the wrong-id scenario needs a pre-registered phone that no other test uses.
