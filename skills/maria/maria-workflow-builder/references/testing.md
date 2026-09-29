# The regression suite for a customer flow

What the suite is for: **pin where the flow stops, which tools it calls and what the backend
ends up holding**, for every edge. Then no change to the flow, the runtime or the model can
silently undo a fix. The reference implementation is San Roque's suites in maria-voice
`tests/integration/customers/`:
- `test_san_roque_citacion_paths.py` (TP, the base: harness, one test per edge);
- `test_san_roque_piloto.py` (PIL, cases replayed from production pilots);
- `test_san_roque_citacion.py` (TC, older harness plus latency);
- `test_san_roque_globales.py`, `_salidas.py`, `_alta.py`, `_availability.py`… (one file
  per topic).

## Contents

1. [Principles](#1-principles)
2. [Harness pieces to reuse](#2-harness-pieces-to-reuse)
3. [The edge → covering-test table](#3-the-edge-covering-test-table)
4. [Which tests to write](#4-which-tests-to-write)
5. [Errors: three classes, not two](#5-errors-three-classes-not-two)
6. [Running, repeating, and what it costs](#6-running-repeating-and-cost)
7. [Latency](#7-latency)
8. [Suite skeleton](#8-suite-skeleton)
9. [PII](#9-pii)

---

## 1. Principles

- **Assert on nodes, tools, variables and backend state, never on wording.** The deterministic
  part is where the flow stops. Wording checks use regexes of the behaviour, never exact
  sentences, and only for rules that are deterministic, such as no identifiers spoken.
- **Drive the open-ended middle with an LLM patient and script the deterministic prefix.** A
  fixed script desynchronises the moment the agent reads three options in a different order.
  Two flows with different styles merge turns differently, so a reused suite breaks on turn
  structure before it breaks on logic.
- **Stop by node, not by a judge.** The patient loop ends when the flow reaches the expected
  node, an end node, or `hang_up`.
- **Verify against the backend, not against what the agent said.** «Su cita ha quedado
  registrada» and "the appointment exists" are two claims. Snapshot before, diff after, and
  assert on the diff.
- **Clean up only what the run created** (the snapshot diff), never "all appointments of the
  test patient".
- **The caller's "no" makes the most valuable tests**: they decline and the irreversible tool
  is NOT called.
- **Non-strict `xfail` with the evidence in the message** for known flow defects. It reports
  without going red and flips to green when fixed. Put it LAST in the test: an xfail aborts
  execution, so earlier it hides exactly what you came to check.
- **Every production incident becomes a test before the fix** (PIL): the real turn sequence
  with identifiers taken out, the expected node and tools. Then fix the flow until it is
  green.
- **A suite that skips half its cases without saying so is worse than a red one.** Read the
  skip count, not just the failures. San Roque's TC once reported «2 passed, 5 skipped, 3
  failed» with none of the three meaning what it seemed.
- **Pin the flow in code, and assert it loaded.** PIL hard-codes the clone id and
  `_assert_es_el_clon` checks it: an env-var version once ran against the production flow
  unnoticed.

## 2. Harness pieces to reuse

They live in San Roque's files today. **When building the second customer's suite, extract
them into a shared `tests/integration/customers/_flow_harness.py` instead of copying them**,
and make San Roque import it. Two copies drift in a week.

| piece | where (TP = paths, TC = citacion) | what it does |
|---|---|---|
| `path_call(objective, phone, flow_id)` | TP:~713 | builds the session. Mock `JobContext`, Silero VAD, room metadata with `conversation_flow_id` pinned, participant with `api_key`, `sip.callStatus=active`, and `LiveKitAPI` patched in `base_agent` and `session` so `sip.transfer_sip_participant` succeeds silently. Runs `start_web_assistant`, attaches `CallRecorder`, warns if another flow loaded |
| `Driver` | TP:~507 | `greeting()`, `says(text)`, `reaches(*nodes)`, `until(check)`, `ran(tool)`, `node`, `vars()`, `called()`, `args_of()`, `tool_failures()`, `fillers()`, `hung_up()`. Nudges «¿Sigue ahí?» up to 2 times per wait |
| `Tape` | TC:~383 | records transcript, tool calls with args, sampled node ids |
| `drive_patient(d, goal, until, opening, max_turns)` | TP:~824 | LLM patient: `PERSONA_RULES` + per-test script, stops by node |
| `went_through(d, node, via_edges)`, `assert_path_contains` | TP:~1537/1637 | node sampling misses fast `static_text` nodes, so this also reads `advance_conversation_flow` args |
| `assert_no_tool_errors`, `skip_if_the_his_failed` | TP:~1453/1485 | the three error classes (§5); parses the error TEXT, since `is_error` is always False |
| `assert_no_ids_spoken(d, spoken, keys)` | TP:~1492 | the one deterministic spoken rule |
| `assert_call_ended_at(d, *nodes)` | TP:~1503 | ended where expected; a `hang_up` shortcut becomes an xfail, and it goes LAST |
| backend client built from `flow_document.tools[]` (`His`) | TP:~882 | preconditions and cleanup through the flow's own endpoints. No host or key in test code |
| `his_cleanup` (autouse, module) | TP:~1121 | snapshot ids, cancel only new ones |
| `inject_tool_fault(driver, tool, error, times)` | TP:~4202 | wraps `http_tool.execute_http_tool` to return a timeout or unreachable error. Guarded by `test_the_fault_injector_actually_intercepts` |
| `_respuesta_simulada`, `_respuestas_por_argumentos` | PIL:~2104/2424 | replace a GET webhook's body to simulate backend states you cannot create (duplicates, empty lists) |
| `CallRecorder` (`_citacion_artifacts.py`) | — | per-test `.txt`/`.json`, `index.json`, `transitions.csv`, PII-masked; `failed/` copies |
| `ConversationLogger` (`conversation_log.py`) | — | ordered turns plus tool calls to `/tmp/conversations/<test>.txt`, masked |
| `TransitionMetricsTracker` (`_transition_metrics.py`), `citacion_e2e.py` | — | latency (§7) |

Hard-coded to San Roque, and NOT to be carried over:
- flow and node ids, `AGENDAS`/centres/specialties;
- the `His` endpoint names and body shapes;
- the host checks, the Spanish persona and regexes;
- the test DNIs, which come from the gitignored `.env`.

If the customer's backend cannot be reached from tests, or writes are unsafe, use the **replay
backend** pattern from Blue:
- `fixtures/blue_sina_replay` + `gen_blue_sina_replay_fixtures.py`;
- a second core-service on its own port with `HIS_PROVIDER_OVERRIDE=sina_replay`.

## 3. The edge → covering-test table

```python
EDGES_UNDER_TEST: dict[str, str | None] = {
    "e_saludo_nueva": "test_new_appointment_end_to_end_books_and_confirms",
    "e_authn_ko":     "test_declining_registration_goes_to_a_person",
    "e_transfer_fallo_fin": None,   # only reachable when SIP fails on a real call
    "e_x": "piloto::test_p5_4_second_record",   # covered in another file
}
```

A test compares it against the REAL graph read from the backend
(`test_the_suite_covers_every_edge_of_the_flow`, TP:~1653). It includes `skip_response_edge`
and `transfer_failed_edge`, and it fails on:
- an edge no test names;
- a stale entry;
- a test name that no longer exists.

It prints the coverage ratio. Global jumps take no edge, so cover them with a parametrized
"from every node" test (`SALTO_GLOBAL_DESDE`). "Covered" then means *a test names it*, not
*something passed through it once*.

## 4. Which tests to write

For each node, one test per row of the outcome matrix (`authoring.md` §2) that the node can
produce, then:
- **the happy path end to end**, writing to the backend, verified by snapshot diff, cleaned
  up;
- **every decline** («no quiero darme de alta», «déjelo»): the irreversible tool is NOT
  called, and the call ends or goes to a person (never the error ending);
- **every backend failure** via `inject_tool_fault`: timeout, 500 and unreachable. The flow
  reaches the failure notice or the checking node, and never says it worked;
- **empty results** (simulated when needed): "I cannot see it", alternatives, a person;
- **identity**: someone calling for someone else, a document dictated the ways real callers
  do (`uno, dos…`, `12 34 56 78 D`, a missing digit, «C de casa», starting again), a
  corrected letter searched again, several records per document;
- **unintelligible turns** on nodes that confirm or end: no irreversible action;
- **globals**: the end and human globals from every node; the "other question" global answers
  from the FAQ (it calls `get_node_content`) and does not transfer when the answer is in the
  documentation;
- **languages**: one call in each language the customer serves, asserting the fixed texts
  came out in that language;
- **static document checks**, which drive no session:
  - webhooks of a test clone never point at production;
  - `body_params` types;
  - a KB collection is linked;
  - node names are strings;
  - the lint has no errors (`flow_lint.lint(doc)`);
- **production replays**: every pilot incident as a test (PIL pattern), named after the
  finding (`test_p5_4_…`).

Test data:
- synthetic identities in a range the suite owns (e.g. a throwaway 99M DNI range the backend
  does not know);
- vary the WHOLE identity, not just the document, when the backend deduplicates on personal
  data;
- a fresh caller phone per call (`next_phone`).

## 5. Errors: three classes, not two

| class | example | what to do |
|---|---|---|
| **outage** | GATEWAY_TIMEOUT, 502/503/504 | `skip`: retrying may work |
| **business** | malformed request, invalid document format, "Appointment conflict" | **red**: it says something about the flow. Many backends return these as HTTP 500; a "5xx = infra" heuristic hides real failures |
| **broken state** | "Resource not found" cancelling something the API lists | `skip` saying what happened: retrying fixes nothing and the flow did nothing wrong |

Precondition failures (no slots today, the test patient vanished) skip, never fail.

## 6. Running, repeating and cost

```bash
cd <maria-voice checkout>
poetry run pytest tests/integration/customers/test_<customer>_paths.py \
  -o addopts="" -o log_cli=false -p no:cacheprovider -q -k "<selection>"
```

- `-o addopts=""` is required: `pytest.ini` deselects `tests/integration`.
- Run SERIALLY. `-n 4` failed for everyone: the customer's test backend is small and the
  process-wide patches collide.
- Required services:
  - a core-service pointed at the environment of the flow (`BACKEND_URL`; San Roque's stg
    stack ran on `:3005`);
  - Azure OpenAI for the agent AND the harness. The patient and judge models must exist on
    the harness endpoint: `MC_HARNESS_AZURE_ENDPOINT`/`_API_KEY`, swedencentral has
    `gpt-5.1` and `gpt-4o`, France does not. A wall of `404 DeploymentNotFound` means the
    harness, not the agent;
  - the customer's test backend reachable.
- **`tests/integration/customers/.env` must not override the command line** (fixed in
  maria-voice#483), and must never define a variable name that `config.py` reads
  (`QUESTIONNAIRE_ID`): it would hijack every call in the folder.
- **Repeat before believing.** A model-driven test that passes once may pass one time in
  two. Use `scripts/repeat_tests.sh <label> <N> <file> <-k expr>` for a per-test tally over N
  sequential runs. Consider a fix done at 3/3 on its own tests.
- **Every test costs money.** It is an LLM conversation (agent + patient). Run the tests of the
  nodes and edges you touched and their direct neighbours. Before running any further suite,
  say in one sentence why the change can affect it. Full regressions only before a promotion.

## 7. Latency

The number that matters is end to end per TURN: from the caller finishing to the agent's
first token. Two kinds of turn have different fixes:
- **answering**: the active node responds, usually via a tool. p50 1.8 s / p95 4.3 s. The
  levers are model, reasoning effort and prompt;
- **transitioning**: the destination's opening has to be produced. p50 0.95 s plus ~0.3 s of
  handoff. The lever is not generating it (`pre_message`, `static_text`).

Where to read it:
- `/tmp/transcript.txt` (DEV), with the audio span of every line. The path is fixed, so
  snapshot it per call;
- the log line `E2E time:`;
- `Pre-generated greeting for <node> (… first chunk in <N>s)`;
- `TransitionMetricsTracker` for the handoff stages;
- `python -m tests.integration.customers.citacion_e2e <run_dir>` for per-turn e2e and
  `e2e − chain`, grouped by node, with turns with and without a backend tool reported
  SEPARATELY (the distribution is bimodal).

The text harness has no STT/TTS, so its figures are lower bounds. In production, measure with
`E2E time:` from the worker logs, **never** with `calls.transcription` timestamps. Compare
MEDIANS over several runs: model and webhook latency have long tails.

## 8. Suite skeleton

```python
"""<Customer> <flow> — regression suite for flow <clone id> (<env>).

Run: poetry run pytest tests/integration/customers/test_<customer>_paths.py -o addopts="" -q
Needs: core-service for <env> at BACKEND_URL, <CUSTOMER>_API_KEY, the test backend reachable.
"""
import pytest
from tests.integration.customers._flow_harness import (   # extracted from San Roque, §2
    path_call, drive_patient, assert_no_tool_errors, assert_no_ids_spoken,
    assert_call_ended_at, assert_path_contains, skip_if_backend_failed, inject_tool_fault,
    edge_coverage_test,
)

FLOW_ID = "<clone uuid>"            # pinned in code on purpose; asserted to have loaded
NODE_START, NODE_AUTH, NODE_END, NODE_HUMAN = "node-saludo", "n_auth", "node-end", "n_operador"

EDGES_UNDER_TEST = {
    "e_saludo_nueva": "test_new_appointment_end_to_end",
    "e_auth_ko": "test_declining_registration_goes_to_a_person",
    # …every edge of the graph, or None + the reason it cannot be reached from a conversation
}
test_the_suite_covers_every_edge_of_the_flow = edge_coverage_test(FLOW_ID, EDGES_UNDER_TEST, globals())


async def test_declining_registration_goes_to_a_person(backend, throwaway_identity):
    """e_auth_ko: an unknown document, the caller does not want to register.
    The registration tool must NOT run, and the call must not end in the error goodbye."""
    async with path_call(objective="Decline registration", flow_id=FLOW_ID) as d:
        await d.greeting()
        await d.says("Quiero pedir una cita.")
        await d.reaches(NODE_AUTH)
        await d.says(f"Mi DNI es {throwaway_identity['document_spoken']}.")
        await drive_patient(d, "No quieres darte de alta. Si te lo proponen, di que no, "
                               "que prefieres hacerlo en persona.", until=(NODE_HUMAN, NODE_END))
        skip_if_backend_failed(d, "buscar_paciente")
        assert not d.called("crear_paciente"), f"registered someone who declined: {d.tools()}"
        assert_no_tool_errors(d)
        assert_call_ended_at(d, NODE_HUMAN, NODE_END)          # LAST: may xfail
```

## 9. PII

Nothing from a real call goes into a test file, a docstring or a fixture: not the phone, not
the document, not the name.
- Name the call (`d7085960`), not the caller.
- Replayed sentences can stay when they are the point, with identifiers taken out.
- Test identities are synthetic.
- Artifacts written by a run are masked (`CallRecorder`, `ConversationLogger`).
