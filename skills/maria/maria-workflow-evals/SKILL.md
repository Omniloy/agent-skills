---
name: maria-workflow-evals
description: Create or update the voice evals of a MarIA customer workflow on the Agent Evaluator platform (mariaevals.api.omniloy.com) from the flow and its integration regression suite — choose what only a real voice call can verify, write the declarative scenarios module (personas, judges, test configs), bootstrap it idempotently, run a small batch, triage by persona/judge/agent, keep KNOWNFAIL evidence, and detect drift when the suite or flow changes. Use when the user wants to create evals, translate tests to evals, sync eval resources after a test/flow change, run or triage an eval campaign for a customer (San Roque, Blue, HCB, Premium…). Supersedes maria-voice-customer-evals; composes agent-eval-api for platform mechanics.
user-invocable: true
---

# maria-workflow-evals

Turns a customer workflow and its regression suite into voice evals on the Agent Evaluator
platform, and keeps them in step. It is the second of the MarIA workflow skills:
1. `maria-workflow-builder` builds the flow and its pytest suite;
2. **this skill** evaluates the result through the voice channel;
3. monitoring watches production.

**Outputs:**
- a `tests/integration/customers/_evals/scenarios_<suite>.py` module that is the source of
  truth for the platform resources;
- the resources themselves (`AI_generated-<suite>-*`), bootstrapped;
- `evals_manifest_<env>.json` with drift hashes;
- a campaign report: flow version tested, per-scenario results, KNOWNFAILs with evidence,
  DROPPED tests.

## Read first

| file | for |
|---|---|
| `references/scenario-design.md` | what to evaluate. The suite and the evals measure different things; do not duplicate the suite |
| `references/platform.md` | hosts, **what an eval actually tests** (the deployed worker + the api_key's flow in that env, not a clone), the maria-voice tooling, platform constraints, the campaign loop |
| `references/persona-and-judge.md` | the stopping guardrail, persona scripts, the judge template |
| `agent-eval-api` skill → `references/persona-and-judge-recipes.md` | the generic recipes this builds on |

## Script

`scripts/eval_plan.py --suite <suite> [--repo <maria-voice>] [--env prod]` is offline and
reports:
- each scenario and the tests it cites (missing ones flagged);
- the suite's tests that are neither evaluated nor DROPPED;
- **drift** against the manifest, computed exactly like the bootstrap hash;
- manifest entries whose scenario was deleted.

Run it before and after editing a scenarios module.

## Hard rules

1. **Say which flow version the evals hit.** Query the api_key's default flow in the
   environment the platform agent points at (`platform.md` §2), and put it in the report.
   Evaluating a sandbox clone means making it the served flow there, which is the user's call.
2. **Resolve platform resources by name, never by id across hosts.** The default host is
   `mariaevals.api.omniloy.com` (`EvalsClient("prod")`).
3. **The scenarios module is the source of truth.** Edit it and re-bootstrap; never
   hand-edit a resource the module owns.
4. **Small batch first.** Run 1–2 scenarios, read the transcripts, then the rest. At most 2
   concurrent runs.
5. **"Make the evals pass" touches personas and judges only.** An agent or flow defect goes to
   `maria-workflow-builder`, or stays as a KNOWNFAIL with evidence.
6. **No real personal data**:
   - synthetic identities created in the customer's test backend;
   - their ids go in the gitignored `AGENTS.local.md`;
   - calls are named by id, never the caller.
7. **Evals cost real calls, and some write to the customer's test backend.** Launch what the
   change can affect; clean up what the calls created.

## Procedure

### 1 · Intake

- Resolve the maria-voice checkout and credentials (`EVALS_USER_NAME`/`EVALS_PASSWORD` in its
  `.env`).
- Identify the customer, the suite files and the platform agent: `<Customer> DEV|STG`, whose
  `maria_center_id` is the api_key.
- Query what that environment serves (flow id + version).
- If `scenarios_<suite>.py` exists, run `eval_plan.py`: that decides between the **sync** and
  **generate** paths.

### 2 · Design (generate path, or new tests on the sync path)

Follow `scenario-design.md`.
1. Walk the suite's `EDGES_UNDER_TEST` and the flow's outcome matrix.
2. Pick what only voice can verify: dictated data, turn-taking and silences, the happy paths,
   globals under voice, languages, and incidents from the voice layer.
3. Put everything else in `DROPPED` with the reason.
4. Show the user the scenario list, with what each proves and what it costs (minutes, backend
   writes), before writing it.

### 3 · Write the module

- Follow the contract in `platform.md` §3: `AGENT_NAME_BY_ENV` keyed by PLATFORM env,
  `TEST_FILES`, `SCENARIOS`, `DROPPED`.
- Write personas and judges with `persona-and-judge.md`:
  - the stop rules include the end-of-call catch-alls;
  - transfer judges carry the web-mode note;
  - each judge is scoped and positive;
  - pair it with a `tool_called` on the proving tool when there is one, using names read
    from the flow's `tools[]`.
- Start from `scenarios_san_roque_citacion.py` (workflow, no identity) or
  `scenarios_blue_citacion.py` on branch `test/blue-citacion-suite` (workflow with
  identities).

### 4 · Bootstrap

```bash
cd <maria-voice>
python -m tests.integration.customers._evals.bootstrap <suite> --env prod
```

Idempotent: it creates what is missing, PUT-updates by name, never deletes, and writes the
manifest. On a WAF 403, rephrase the text (`platform.md` §4). Then run `eval_plan.py`: every
scenario must read `in sync`.

### 5 · Run and triage

Launch a small batch through `EvalsClient` (or `evals/eval_<suite>.py` once the suite is
stable), 1–2 at a time. For each red execution, go in the order of `platform.md` §5:
1. **Did the call reach the point under test?** If not, it is the persona or the agent's
   latency.
2. **Is the judge sound?** Check with `reevaluate`; do not repeat the call.
3. **Only then the agent.** Classify with `maria-workflow-builder`'s `defect-catalog.md`.

A voice-only defect that is not being fixed now becomes `KNOWNFAIL-<slug>`, with the
execution that shows it.

### 6 · Report and keep in step

- **Report**:
  - the flow version and environment;
  - per scenario: config / run / execution, score, pass;
  - KNOWNFAILs and DROPPED;
  - the resources by name.

  Publish it if others will read it.
- **Add the campaign to the customer's `WORKLOG.md`** (`maria-workflow-builder` →
  `references/worklog.md`): the flow version tested, the results, what each red means, and
  what was learned about personas or the platform.
- **Re-run `eval_plan.py`** whenever the suite or the flow changes. Drift means re-bootstrap,
  and a new test means deciding: eval, or DROPPED.
- Commit the module and the manifest in maria-voice (PR title `[MAR-XXXX] …`, in English, no
  Claude attribution).
