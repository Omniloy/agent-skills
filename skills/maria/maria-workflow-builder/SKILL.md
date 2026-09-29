---
name: maria-workflow-builder
description: Create a new MarIA voice workflow (conversation_flow) for a customer from instructions and use cases, or take an existing flow and make it production-ready, or fix one from QA/production feedback — producing a versioned flow in a sandbox clone, an integration regression suite that pins its behaviour, a readiness checklist, and the promotion SQL. Use whenever the user wants to build, clone, review, harden, fix, lint, test, promote or activate a maria-voice workflow / conversation flow / flow_document (booking/citación, FAQ, triage, transfer), mentions a customer flow (San Roque, Premium, Blue, HCB…), pastes call feedback about a workflow, or asks whether a flow is ready for production. Supersedes maria-voice-workflow-tests.
user-invocable: true
---

# maria-workflow-builder

Build or repair a customer's voice workflow so it survives real callers, and leave behind a
regression suite that keeps it that way. What this encodes was paid for on San Roque: 51
sandbox versions and 5 production pilots in three weeks. The worklog is
`maria-voice/docs/wip/san_roque_piloto_fixes/WORKLOG.md`.

**Outputs, every time:**
1. **The flow**, in a sandbox clone the customer never hears, every change a new version with
   a concrete description (never an in-place edit);
2. **an integration suite** in `maria-voice/tests/integration/customers/` that names a test
   for every edge, verifies against the backend and replays each incident;
3. **`READINESS.md`**, the production checklist ticked with evidence;
4. **the promotion files**: SQL generated, rehearsed and rolled back in a throwaway Postgres,
   applied only with the user's OK.

Next stages: `maria-workflow-evals` turns the suite into voice evals on the evaluation platform,
and `maria-prod-monitor` watches the pilot and production.

## References: read before the phase that needs them

| file | read when |
|---|---|
| `references/runtime.md` | **always, first.** How the worker executes a flow; most "the model ignored the prompt" bugs are mechanics |
| `references/authoring.md` | designing nodes, writing prompts and edge descriptions: the booking+FAQ blueprint and the outcome matrix |
| `references/backend-recon.md` | before designing any node that reads the customer's API |
| `references/testing.md` | writing and running the suite |
| `references/versioning-and-promotion.md` | before ANY write to a database; clones, edits, promotion, activation |
| `references/defect-catalog.md` | triaging a red test, a QA call or a production call |
| `references/production-checklist.md` | the gate; copy it into the workplan |

## Scripts (`scripts/`)

All are Python 3 standard library and read credentials from maria-core-service's `.env`
(override with `MARIA_CORE_ENV_PATH`). Everything that writes is **dry-run by default**.

| script | does |
|---|---|
| `flow_lint.py --env E --flow ID` / `--file doc.json` | static review: 43 rules from real incidents; exit 1 on errors; with `--env` also checks the KB link, the version health and whether an api_key serves it |
| `flow_edit.py` | the frame for one-version edit scripts: asserts per change, host guard, live-flow guard, lint delta, readable diff, save via core-service, read-back, health |
| `clone_flow.py` | sandbox clone across or within environments, KB collections included, environment rewrite |
| `create_flow.py` | a new flow from a local JSON (from-scratch mode), lint-gated, versioned through core-service |
| `gen_promotion_sql.py` | promotion + rollback SQL + structural diff, environment values kept from the target |
| `validate_promotion.py` | rehearses those files in a throwaway `postgres:16` seeded with the target's real rows |
| `repeat_tests.sh` | N sequential runs of a pytest selection with a per-test tally |
| `flowdb.py` | the shared access layer (read via PostgREST, write via core-service, health) |

Writes through the API need `MARIA_CORE_URL_<ENV>` pointing at a core-service connected to
that environment's database. The script refuses to guess.

## Hard rules

1. **Find the active flow first:** `api_keys.default_conversation_flow_id`. Never trust the id
   in the task.
2. **Never edit a flow an api_key serves.** Clone, fix the clone, promote. The only exception
   is a hotfix the user explicitly approves.
3. **Every change is a new version through core-service** (`flow_edit.py`). A direct
   document write is invisible to running workers. The documented exceptions are creating a
   clone and promotion SQL; both move all three version fields together.
4. **Production is read-only without an explicit OK**, for each write. Applying SQL, activating
   and deactivating are the user's call.
5. **A test clone never points at production** backends or phone numbers.
6. **No PII in the repo**: name calls, not callers; test identities are synthetic.
7. **Integration tests cost money.** Run what the change can affect; say why before running
   more.
8. **Ask only what you cannot find.** Ids, backend behaviour and current state can be found;
   the customer's business rules and the user's decisions cannot.

## Procedure

Pick the mode. The phases are the same; the entry point differs.

| mode | the user gives | start at |
|---|---|---|
| **new**: flow from scratch | instructions / use cases / a Jira task, customer, backend API | Phase 0 |
| **harden**: existing flow to production | a flow (or a customer) | Phase 0, then Phase 3 on the current document |
| **fix**: feedback / incidents | call ids, QA notes, pilot findings | Phase 0 briefly, then Phase 6 |

### Phase 0 · Intake

1. Resolve the maria-voice checkout (look for `maria_voice/assistant/session.py` upward from
   CWD, then `~/omniloy/dev/maria-voice`, then ask) and the sibling `maria-core-service`.
2. Identify the customer's `api_key` per environment and **what it serves now** (voice_mode,
   default flow, version). Record it.
3. Collect the use cases. For each one: who calls, what they want, what counts as done, what
   is irreversible, when a person must take over. Record the languages served, the transfer
   destinations per case, and the knowledge-base documents. Pull the Jira task if linked
   (`jira-tasks`).
4. Create the workplan folder `maria-voice/docs/wip/<customer>_<topic>/`: `WORKPLAN.md`,
   later `BACKEND_CONTRACT.md`, `READINESS.md`, `edit_vN.py`, `promotion/`. `docs/wip` is
   git-excluded, so it is the scratch that survives the session.
5. Ask the user, once and together, only what remains: business rules the docs do not settle,
   the target environment, and the Jira key for PR titles (`[MAR-XXXX] …`).

### Phase 1 · Backend reconnaissance

Follow `backend-recon.md`. Probe every endpoint the flow needs, read-only in production and
writes only in the test environment, cleaned up. Write `BACKEND_CONTRACT.md`. The things to
nail down:
- what each empty result means;
- filters that hide data;
- sizes against the 8000-character cap;
- cold-write latency against `timeout_s`;
- the exact text of error bodies;
- JSON types;
- duplicate records and sync lag;
- what is bookable at all, confirmed with the customer.

### Phase 2 · Design

Follow `authoring.md`.
1. Start from the **booking + FAQ blueprint** (§1) and cut what the customer does not need.
   Harden mode: map the existing graph onto it and list the gaps.
2. Fill the **outcome matrix** (§2) for every conversation node. Each empty cell is a design
   task.
3. For each node, choose the **mechanism** (§3): `pre_message`, `static_text` +
   `skip_response_edge`, equation + `eval_after_tools`, global node, `transfer_call` +
   `transfer_failed_edge`.
4. Write down the **environment values** (hosts, credentials, transfer numbers/queues) as
   variables, not literals.
5. Show the design to the user as a node table plus the outcome matrix before writing JSON.
   This is the cheapest point to be wrong.

### Phase 3 · Author in a sandbox

- **New:** write `flow_v1.json` in the workplan, then
  `create_flow.py --env <sandbox env> … --apply`. Link the KB collection.
- **Harden / fix:** `clone_flow.py` from the flow the customer is served, with the test
  backend and a test transfer number.
- Every change after that is an `edit_vN.py` in the `flow_edit.py` frame: one version per
  coherent change, one assert per change, and a description with the evidence. Dry-run, read
  the diff, then `--apply`.
- Write prompts with `authoring.md` §4–§9. Add the common-rules block to every prompt node, and
  translate every spoken fixed text.
- Run `flow_lint.py` after each version: **0 errors**, and every warning fixed or justified.

### Phase 4 · The suite

Follow `testing.md`.
1. If `tests/integration/customers/_flow_harness.py` does not exist yet, **extract** it from
   the San Roque suites (the table in §2). Do this in its own commit, with San Roque switched
   to import it and its suite still green. Do not copy the helpers.
2. Write `test_<customer>_paths.py`:
   - the `EDGES_UNDER_TEST` table plus the coverage test;
   - one test per outcome-matrix row, happy paths verified against the backend;
   - declines, fault injection on every write, identity cases, globals, languages;
   - static document checks.
3. Pin the clone id in code and assert it loaded. Test data is synthetic.

### Phase 5 · Iterate to green

Run the tests of what changed, serially, and triage with `defect-catalog.md`:
- **flow defect**: a new version, back to Phase 3;
- **backend defect**: add it to the contract's defect list, put the flow's mitigation in
  place, and give the test a non-strict xfail with evidence;
- **outage / broken state**: a skip with its reason;
- **harness defect**: fix the harness, never the assertion's meaning.

A fix is done at 3/3 on its own tests (`repeat_tests.sh`). Before promotion, run the whole
suite once on the final version. Read the skip count.

### Phase 6 · Fix mode: from feedback to versions

For each reported call:
1. Read the call read-only: `calls.transcription`, `call_timeline`, `call_workflow_transitions`,
   and the worker logs if the pod is still alive.
2. Classify it with `defect-catalog.md`.
3. Number the finding (`P<pilot>-<n>` / `H<n>`) with its call id.
4. Write the test that reproduces it, replaying the real turns with identifiers taken out.
5. Then write the flow change, and after it look for the SAME SHAPE elsewhere in the graph:
   `flow_lint.py` plus the outcome matrix. The paths nobody walked have the same defects.

Keep the findings in the workplan. They become the version descriptions and, later, the
monitoring skill's labels.

### Phase 7 · Readiness, promotion, activation

1. Copy `production-checklist.md` into `READINESS.md` and tick every item with evidence.
2. Run `gen_promotion_sql.py` with every environment value covered, including `--forbid` for
   the test host and test phones. Then `validate_promotion.py` must be green. Review the DIFF
   with the user.
3. Prepare the activation SQL (guarded) and the kill switch (`98_deactivate.sql`)
   (`versioning-and-promotion.md` §7). The user applies them, or you do with their explicit
   OK.
4. Activate at the start of a watched pilot window. After it, go to Phase 6 with the pilot's
   calls.

## Finish

Report to the user:
- the clone id and version;
- lint counts;
- the suite result: pass/xfail/skip, with repetitions for the fixes;
- the readiness items that remain open and why;
- the files produced;
- the PRs, with titles `[MAR-XXXX] …` in English and no Claude attribution.

Record in memory only what is not derivable: the customer's quirks, decisions the user made,
and where things stand.
