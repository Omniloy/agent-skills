# From a workflow's regression suite to voice evals

The integration suite (built by `maria-workflow-builder`) and the platform evals measure
DIFFERENT things. Design the evals for what only they can see, or you pay for a slow, flaky
copy of a test you already have.

| | integration suite (pytest) | platform eval |
|---|---|---|
| channel | text injected into the session | a real voice call: TTS persona → STT → agent → TTS |
| runs | your local worktree against a pinned clone | the DEPLOYED worker of an environment, with the flow the api_key serves there (see `platform.md` §2) |
| sees | nodes, tools + arguments, variables, backend state | only the transcript (+ tool calls the agent made) and audio timing metrics |
| can | fault injection, simulated backend responses, snapshot diffs | endpointing, barge-in, ASR of dictated data, TTS pronunciation, real latency and silences, inactivity hang-ups |
| costs | one LLM conversation | a 3–30 min voice call; flaky by nature |

## Contents

1. [What to turn into an eval](#1-what-to-turn-into-an-eval)
2. [What NOT to turn into an eval](#2-what-not-to-turn-into-an-eval)
3. [Mapping a workflow suite](#3-mapping-a-workflow-suite)
4. [Signals an evaluator can use in a workflow](#4-signals-an-evaluator-can-use-in-a-workflow)
5. [Identities and data](#5-identities-and-data)
6. [KNOWNFAIL scenarios](#6-knownfail-scenarios)
7. [Naming, tags and the scenario record](#7-naming-tags-and-the-scenario-record)

---

## 1. What to turn into an eval

In priority order:

1. **The voice layer on the paths that matter.** Identification by dictated document
   (digits in words, groups, the letter with an anchor), email and phone capture, names.
   Every San Roque identity defect that reached production was a voice defect first:
   - a "D" the TTS does not pronounce;
   - `omniloy` transcribed as `omniloi`;
   - «43, 7, 54, 11, 6, C» counted as 7 digits.
2. **One happy path per core use case, end to end**: book, manage (cancel/change), FAQ.
   These are the smoke tests of a deployment.
3. **Turn-taking and silences on the transitions the pilots flagged**:
   - announce-and-wait;
   - entering a node that must look something up;
   - a slow backend with no filler.

   Read them from the execution's `metrics.response_times_ms`.
4. **Global exits under voice conditions**: asking for a person mid-flow, hanging up
   mid-dictation, an unintelligible or overlapping turn.
5. **Language**: one call per language served, since fixed texts fall back silently.
6. **Each production incident whose cause was in the voice layer**, as a regression eval named
   after the finding.

## 2. What NOT to turn into an eval

- **Backend failure branches.** You cannot inject a 500 into a deployed worker. They stay in
  pytest with `inject_tool_fault`.
- **States you have to fabricate**: duplicate records, empty lists, sync lag. They stay in
  pytest with simulated responses.
- **Assertions on variables or node ids.** The platform cannot see them; approximating them
  with an LLM judge turns an exact check into a coin toss.
- **Anything that needs a clean backend state across calls** unless you pin a dedicated
  identity and run it sequentially (§5).
- **Prompt-routing details the text suite already pins**, unless the voice channel changes them.

Put every dropped test in the scenario module's `DROPPED` list with the reason. Those are the
visible gaps, and reviewers need to see them.

## 3. Mapping a workflow suite

Start from the suite's `EDGES_UNDER_TEST` table and the outcome matrix of the flow
(`maria-workflow-builder` → `authoring.md` §2). For each outcome row, decide:

| outcome | eval? | how |
|---|---|---|
| happy path (book / manage / FAQ) | **yes**, one per use case | persona script + `tool_called` on the write tool + one LLM judge on the confirmation |
| caller declines | yes if cheap (no DNI needed), else pytest | `tool_not_called` is not reliable (negative points drop their sign); use an LLM judge "SE CUMPLE si no se creó/canceló nada" |
| caller asks for a person | **yes** | LLM judge "announced or attempted a transfer", with the web-mode transfer note |
| operation fails | no | pytest fault injection |
| empty result | only if the backend has a REAL empty case (a specialty with no agenda) | judge on the wording ("no me aparece", alternatives offered) |
| other question (FAQ) | **yes** | judge the answer against the KB fact; `tool_called: get_node_content` as the deterministic signal |
| unintelligible / noise | yes | the persona says a mixed sentence ("Vale, eso. O <nombre>, cállese"); judge that nothing irreversible happened |
| wants to hang up | yes, cheap | judge on the authored goodbye |
| calling for someone else | yes | judge that the agent asked for the PATIENT's document |

Collapse tests that share the same observable signal into one scenario. San Roque's globals
for person and goodbye each became one scenario (configs 113, 114).

## 4. Signals an evaluator can use in a workflow

- **`tool_called`** is deterministic and cheap. Use it for the tool that proves the path ran:
  - `buscar_paciente` (identification);
  - `crear_cita` / `cancelar_cita` / `reagendar_cita`;
  - `get_node_content` (the FAQ was consulted);
  - `update_flow_variables` (a choice was saved).

  Names must match the flow's `tools[].name` exactly, because a mismatch silently never
  matches. Read them from the flow document; never invent them.
- **A `transfer_call` node is not a tool call.** It fires on node entry, so there is no
  `tool_called` for it. Use an LLM judge on the announcement, and accept the failure message:
  every SIP transfer fails in web mode.
- **Hang-up and the end node** show only as the authored goodbye in the transcript. Quote the
  `goodbye_message` in the judge.
- **Timing**:
  - `metrics.response_times_ms.all_time_to_first_audio_ms` for silences;
  - the inactivity hang-up shows as the agent closing ~10 s after a long gap.

  A peak of tens of seconds is the agent, not the persona: rewriting the persona will not
  save that run.

## 5. Identities and data

- **A dedicated synthetic identity per scenario family**, created in the customer's TEST
  backend on purpose. Never a real person's data. Put the ids in the gitignored
  `AGENTS.local.md`, not in the skill, and never in a docstring.
- **Choose data that survives the voice pipeline.** Test it once and write down what works:
  - on San Roque a DNI ending in S dictated in ONE sentence with a phonetic anchor («uno tres,
    uno tres, uno tres, uno tres, ese de Soria»);
  - email on `gmail.com`;
  - the exact specialty name, not an abbreviation;
  - an explicit valid phone number (the harness' `caller_phone_number` arrives malformed).
- **Pin `caller_phone_number` and `max_concurrency: 1`** when the agent caches identity by
  phone (HCB legacy: 15 min).
- **Writes are real.** Evals book and cancel in the customer's test backend. The persona
  cancels what it booked when the scenario allows it; otherwise a cleanup script cancels by id
  after the campaign.
- Scenarios that share an identity's backend state go in the runner's `SEQUENTIAL_GROUP`.

## 6. KNOWNFAIL scenarios

A defect that only the voice channel reproduces, and that the task does not fix, becomes a
`KNOWNFAIL-<slug>` scenario. It stays RED on purpose and turns green by itself the day the
defect is fixed. Do not "fix" it from the persona or the judge. Record, next to each one:
- the defect;
- the execution that demonstrates it;
- what would make it green.

Rule of engagement for "make the evals pass": touch only personas and judges, never the agent
or the flow. What cannot be fixed that way is documented and becomes KNOWNFAIL.

## 7. Naming, tags and the scenario record

- Resources are named `AI_generated-<suite>-<slug>` (the bootstrap does it). `<suite>` is the
  scenario module's stem: `san_roque_citacion`, `blue_citacion`.
- `CLIENT_TAG = "client:<customer>"`, `SUITE_TAGS = ["feature:customer-flow-eval",
  "feature:emulated"]`, plus per-scenario `feature:*` tags. Every tag must start with
  `feature:`, or the API returns 422.
- Each scenario carries `source_tests` (qualified pytest names): that is what the drift hash
  covers. When a workflow suite spans several files, set `TEST_FILES` in the module (see
  `platform.md` §3).
