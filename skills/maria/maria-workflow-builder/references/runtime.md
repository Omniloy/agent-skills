# How the runtime executes a flow

What the maria-voice worker actually does with a `flow_document`. Everything a flow author
writes is interpreted through these mechanics, and most "the model ignored my prompt" bugs
are one of them. Verified against maria-voice `origin/dev` `45d091bc` (2026-09-29); symbol
names are given so you can re-check (`git grep <symbol>`) when the code has moved on.

Main files: `maria_voice/assistant/agents/flow_node_agent.py` (the node agent),
`maria_voice/assistant/agents/node_agents/*` (per node type), `maria_voice/utils/http_tool.py`
(webhooks), `maria_voice/utils/equations.py` (edge equations),
`maria_voice/models/conversation_flow.py` (document model).

> **The MCP server is retired** (MAR-1505). Webhooks, equations, `eval_after_tools`, prefetch
> and integration packages all run **inside the worker**. Anything that says "the MCP server
> evaluates…" describes the old architecture. If you test an older branch, check whether it
> still needs a local MCP server.

## Contents

1. [The document](#1-the-document)
2. [What happens when a node is entered](#2-what-happens-when-a-node-is-entered)
3. [What the model sees in a node](#3-what-the-model-sees-in-a-node)
4. [Transitions](#4-transitions)
5. [Webhooks (`type: "custom"` tools)](#5-webhooks-type-custom-tools)
6. [Variables](#6-variables)
7. [Transfers to a human](#7-transfers-to-a-human)
8. [Ending the call](#8-ending-the-call)
9. [Languages](#9-languages)
10. [Filler (entertainer)](#10-filler-entertainer)
11. [Knowledge base (FAQ)](#11-knowledge-base-faq)
12. [Caches and versioning](#12-caches-and-versioning)

---

## 1. The document

Root fields:

| field | meaning |
|---|---|
| `start_node_id`, `start_speaker` | `agent`: the first node speaks before the caller does |
| `global_prompt`, `global_prompt_enabled` | injected **only into the start node** (see §3) |
| `faq_prompt`, `faq_prompt_enabled` | answering rules over the KB; the documents come from `conversation_flow_collections` |
| `default_dynamic_variables` | the variable table and its defaults |
| `tools[]` | `type: "custom"` = webhook; `type: "mcp"` = a name served by the worker (built-in or integration package; the type name is historical) |
| `nodes[]` | nodes with their `edges[]` |
| `share_node_history` | whether chat context carries across nodes |
| `transition_entertainer` | filler during transitions, tri-state per field, cascades node → flow → runtime |
| `default_language` | the fallback for every localized text |

Per node:
- identity: `node_id`, `node_name`, `node_type` (`conversation` / `end` / `transfer_call` /
  `audio` / `branch` / `function`), `instruction_type` (`prompt` / `static_text`);
- text: `instruction`, `pre_message`, `goodbye_message` (end), `transfer_number` (transfer);
- tools: `tool_ids`, `prefetch_tools`;
- routing: `is_global`, `global_condition`, `edges`, `skip_response_edge`,
  `transfer_failed_edge`.

Per edge: `edge_id`, `target_node_id`, `edge_type` (`prompt` / `equation`), `description` (what
the LLM reads), `equation`, `eval_after_tools`.

`tool_ids` resolve against `tools[].tool_id` first, then `tools[].name`, then the id is used
as-is (which is how a built-in like `transfer_to_human` can be enabled per node).

## 2. What happens when a node is entered

This decides latency and correctness more than any prompt wording.

- **Deterministic nodes** (`end`, `audio`, `transfer_call`, `branch`) resolve in
  `_handle_on_enter_special_cases` without giving the LLM a turn. A `branch` evaluates its
  equation edges on entry (`BranchNodeAgent._auto_evaluate_branch`). A `transfer_call` never
  speaks its `instruction`.
- **A `conversation` node entered by an EDGE opens with a PRE-GENERATED line**
  (`_start_pregeneration`). The line is generated while the transition runs, and it **cannot
  call tools**: the pre-generation prompt forbids them on purpose. "On entry, call X before
  saying anything" is therefore impossible in that turn. The model says "un momento, voy a
  mirarlo", stops, and the call sits silent (15–20 s) until the inactivity handler nudges it.
  The fixes, in order of preference:
  1. The ORIGIN node calls the tool, and the edge is an equation with `eval_after_tools`, so
     the destination opens with the result already in context (San Roque v32:
     `n_algo_mas` calls `buscar_citas`, `e_mas_gestion` routes).
  2. Make the destination a **global node**. A node entered by a global jump ANSWERS with
     `generate_reply`, tools included (`_speak_entry_answer`, MAR-1563).
- **`pre_message`** (the "Opening line"): spoken with `session.say()` on entry, `{{vars}}`
  interpolated. When `_speak_pre_message` returns True the node **skips generation and waits
  for the caller**. That is right for a node that ASKS something and wrong for one that must
  ACT. A holding line ("un momento, que lo compruebo") on an acting node produced 17 s of
  silence.
  - The line is always the same, so it must be true from EVERY predecessor. "¿Me indica su
    DNI?" to someone who already gave it is San Roque v35 and pilot 4 P4-2.
  - Pre-messages are non-interruptible: a caller turn that arrives while one plays is
    discarded (livekit `agent_activity`).
- **`instruction_type: static_text`**: the `instruction` is read aloud through TTS and is
  cacheable, so it costs no generation. With a `skip_response_edge`, the node advances
  server-side right after speaking, and the runtime tells the model to stop. This is the
  tool for "say one thing, then move on". Without a `skip_response_edge`, the node waits;
  its prompt tells the model to stay silent except for FAQ answers, plus the MAR-1542 rule
  that a turn never ends in silence.
- **`prefetch_tools`**: GETs run on the transition path into the node
  (`_run_prefetch_tools`). The model never sees the response; only its `response_variables`
  are written. It is a way to fill variables before the node speaks, or to measure a new
  endpoint's latency with its output discarded. They live OUTSIDE `tools[]`: any
  environment rewrite must cover them.

Measured on San Roque: the transition gap averages ~1.1 s, and **`speak` is 73–86 % of it**.
The handoff silence IS the time-to-first-token of the destination's first sentence
(777–3648 ms on a greeting that interpolates a name, 298 ms with a `pre_message`).

## 3. What the model sees in a node

The system prompt of a `conversation`/`prompt` node is assembled from:

- the node `instruction`, with `{{vars}}` interpolated;
- **`global_prompt` — only if this is the start node** (`if self._global_prompt and
  is_start_node`). Call-wide rules written there reach ONE node. San Roque v30 found that
  "no leas errores de herramienta" was in no node at all, and "una pregunta cada vez" in none
  either. Repeat a short common-rules block at the end of every `prompt` node; not in
  `static_text` nodes, which would read it aloud.
- **the Global Rules block** (runtime), which declares itself to override the node task. Its
  ENDING clause adapts (§8).
- **`## Global Jumps (interruption handlers)`**: every global node's `node_id` and
  `global_condition`, as alternatives at the same level as the node's own edges. An empty
  `global_condition` is dropped with a log warning.
- the node's edges **except** equation edges with `eval_after_tools`
  (`any_edges_in_llm_context`). Those are invisible: the model cannot take them.
- the writable-variables list (`default_dynamic_variables`), and the KB block when
  `get_node_content` is available.

Consequences:
- **An edge the prompt never mentions is dead.** A closed menu ("¿cancelarla o cambiarla?")
  makes an edge for "new appointment" unreachable in practice.
- **Every global condition is evaluated in every node**, not just the one that inspired it.
- **Adding rules dilutes the first one.** San Roque v18 spent four versions adding rules to a
  node instruction before moving the constraint into the edge DESCRIPTION, which is what the
  model reads when routing, and it worked at once.

## 4. Transitions

| mechanism | who decides | use it for |
|---|---|---|
| prompt edge | the LLM calls `advance_conversation_flow` | caller intent ("wants a new appointment") |
| equation + `eval_after_tools: ["<tool>"]` | the worker evaluates the equation right after `<tool>` runs, inside the tool invocation (`_advance_on_matching_equation_edge`) | anything gated on a tool result. Deterministic, and on par with or faster than a prompt edge |
| bare equation (no `eval_after_tools`) | nobody at the right moment | **never** on a conversation node. Gated on `update_flow_variables` it stalls ~15–20 s, because that tool's follow-up turn is cancelled |
| `branch` node | the worker, on entry | deterministic routing on variables with no turn |
| `skip_response_edge` | the worker, after the static text is spoken | notice, then move on |
| `transfer_failed_edge` | the worker, when SIP transfer fails | where to go when the human is unreachable |
| global jump | the LLM, `advance_conversation_flow(global_target_node_id=…)` | reachable-from-anywhere handlers: end, human, "other question" |

- The tool named in `eval_after_tools` MUST be in `flow_document.tools[]`
  (`update_flow_variables` is registered as `type: "mcp"`).
- **Equation engine** (`utils/equations.py`): `||` over `&&` over one comparison: `==`,
  `!=`, `>`, `>=`, `<`, `<=`, `contains`, `not_contains`, `exists`, `not_exist`. An absent
  variable resolves to `""`.
- **An equation cannot see a tool error.** A failing webhook returns a SUCCESSFUL result
  whose text begins `Error: el servicio de la herramienta '<x>' respondió con el código
  HTTP 500…`, and `is_error` is False. `{{created_id}} != ""` just stays false. Error
  handling for a webhook therefore needs a model-visible exit that the prompt ties to that
  text. Alternatively, make the tool write a variable on failure and route with
  `== ""` after it: San Roque v47 does this with `e_reh_error`.
- **The race.** If the happy exit is an invisible equation and the only edges the model can
  see are failure exits, a model that decides to advance on its own can only pick a wrong
  one. San Roque `n_auth_nueva` took "patient not found" while passing
  `patient_found: true`. Mitigate in the failure edges' descriptions: "ONLY if X returned
  EMPTY … if it carries a patient, do NOT take this exit and do NOT call
  advance_conversation_flow: the transition is automatic."
- **A global jump takes no edge**, so any notice node you put in front of the target is
  bypassed. If the transfer must be explained, the explaining node is the global target and
  the transfer node sits behind it via `skip_response_edge`.

## 5. Webhooks (`type: "custom"` tools)

Executed in the worker by `maria_voice/utils/http_tool.py`. Each tool is built as an
LLM-callable tool (`_build_custom_http_tools`). After the call, `_on_http_tool_complete`
extracts `response_variables`, evaluates the tool's `transition`, then the node's equation
edges. One round trip.

- **Parameter placement.** Four sources, each overriding the previous on a key collision:
  1. `params`, legacy: GET → query, else body;
  2. `query_params`;
  3. `body_params`;
  4. **the LLM's arguments**, JSON body by default. Mark a property `"in": "query"` in
     `args_schema` to send it in the URL; GET tools with arguments need it.

  **The model's argument wins.** A property you offer as optional gets filled with `""` and
  overrides your default: an `initial_date: ""` made the backend answer 400 (San Roque v7),
  and a `patient_id: ""` broke the normal case (v41). If a value must not come from the
  model, do not offer it; make a second tool with the argument required.
- **`body_params` keep JSON types.** `"lopd": "true"` as a string was a 400 from a .NET
  backend on every registration for months. The flow editor used to stringify
  `body_params` (fixed in one-stop-shop #490/#491); verify types after any UI save.
- **`{{var}}` interpolation** applies to strings in every source, to the URL and to header
  values, against `_get_dynamic_variables`: flow state + user/company/time built-ins, with
  `current_language` winning.
- **Response cap: `MAX_RESULT_CHARS = 8000`.** Bigger bodies are shrunk: an object gets a
  truncation marker, a bare array is cut silently. The result stays in the chat context for
  every later turn of the node, so the lever is asking the endpoint for fewer items (`size`,
  `free_spaces_size`), not raising the cap. San Roque: 15 slots = 12 451 chars, 5 = 4 151.
- **Timeouts:** default `DEFAULT_TIMEOUT_S` 30 s, explicit `timeout_s` honoured up to
  `MAX_TIMEOUT_S` 60 s (a total wall clock). `timeout_s` must exceed what the backend really
  takes on a cold write; measure it.
- **Orphan tools are still registered** on every node and pad the model's list; remove the
  tool with the capability.
- **Name grammar:** `[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}`. A name outside it is a dead tool. So is
  a `url` without `type: "custom"`.
- **Credentials sit in plaintext in the document** (a product decision). Never print a
  document's headers; the promotion scripts keep them by reference.
- **Integration packages** (`maria_voice/integrations/`, SINA today) serve vendor tools under
  the names the flow already uses (`login_and_onboard`, `schedule_appointment`…). A
  `type: "custom"` entry of the same name wins over the package; a package wins over a
  built-in. A name nothing serves is logged by `_warn_on_unserved_flow_tools` and never
  reaches the model.

## 6. Variables

- Declared in `default_dynamic_variables`. Written by `update_flow_variables` (built-in,
  available everywhere), by `response_variables` (JSONPath, also stored namespaced as
  `<tool>.<var>` plus `<tool>.result`), and by prefetch.
- **A `null` in a response cannot clear a variable**: resolved-null and absent are treated
  alike. Clearing needs `update_flow_variables` or a local handler.
- A variable nobody writes still shows up in the writable list: it is noise. Remove it.
- The same reader feeds the prompt, the equations and the webhook request, so a value is the
  same everywhere within one turn.

## 7. Transfers to a human

- **A `transfer_call` node** is deterministic, and preferred.
  - `transfer_number` interpolates, Jinja expressions included. San Roque picks one of four
    queues from `agreement_id` and `current_language`, with no extra turn.
  - The line the caller hears is `TRANSFER_ANNOUNCEMENT_MESSAGE` from `language_configs`,
    emitted by the code on every transfer. A `global_condition` that also says "tell them
    you are passing them on" makes the caller hear the announcement twice.
  - Give the node a `transfer_failed_edge`. Without one, the runtime's fail-safe reads the
    dialled number aloud, and an internal extension cannot be dialled from outside.
- **The `transfer_to_human` tool**: blocked by default (`_BLOCKED_LOCAL_TOOLS`), unblocked on
  a node that lists it in `tool_ids`. It resolves the centre and number from company config,
  but the LLM decides when to call it.
- Keep numbers in variables, so each environment can differ without touching nodes. A test
  clone must never carry the real front desk's number: it calls real people.

## 8. Ending the call

The Global Rules ENDING clause, picked by `_edge_id_into_end_node()`:
- **exactly one model-takeable edge into an `end` node**: the rule names that exit and says
  take it silently. The end node speaks the authored `goodbye_message` and hangs up.
- **otherwise**: "say a one-sentence goodbye and call `hang_up`". The authored farewell is
  replaced by improvisation; San Roque saw ten wordings in ten runs and up to 16 turns of
  goodbyes.

So give every node that can end the call **one** local edge into the end node. Do not add a
flow rule saying "do not hang up, transition instead": it contradicts a block that outranks
the node, and it was measured to half-work. A global end node helps, but keep a LOCAL edge
too: with only the global one, San Roque's main and stg behaved differently.

## 9. Languages

Localized fields (`{"es": …, "en": …}`) resolve through `resolve_localized_text`: the call's
language, then `default_language`, then any non-empty value, **with no warning**. Every text
that is READ, not generated, needs a variant per language the customer serves:
- `static_text` instructions;
- `pre_message`;
- `goodbye_message`;
- authored filler phrases.

San Roque call `04dd2ce5`: the caller spoke English throughout and heard one Spanish
`pre_message`. The `instruction` of a `prompt` node is a system prompt and needs no
translation. Translations can be generated with core-service
`POST /api/v1/ai/generate-translations`; check them placeholder by placeholder.

## 10. Filler (entertainer)

- Transition filler: `transition_entertainer` cascades node → flow → runtime constants, and
  each field is tri-state. `{"enabled": false}` at flow level turns it off for the whole
  flow (San Roque v32).
- Tool filler while a local webhook runs (`_local_entertainer_hook`): it opens after
  `voice_delay_ms` or the default window. When filler is off, the inactivity watchdog is
  still held so a long request does not hang up the call. With filler off and a backend
  that takes 30 s and then returns 500, the caller hears 30 s of silence (San Roque pilot 5).
  Decide per slow tool.

## 11. Knowledge base (FAQ)

- `get_node_content` is available on EVERY node when the company has a RAG provider. Do not
  list it in `tool_ids`: it is a built-in. On San Roque v12, listing it made it resolve to
  nothing.
- The documents come from `conversation_flow_collections`, read via
  `GET /api/v1/conversation-flows/{id}/collections`. **A clone starts with none.** The log
  line `[FAQ] node X: total_docs=0 | … | docs_in_block=0` tells you which layer failed:
  - `total_docs=0` means nothing was loaded at all (the collection link is missing);
  - `docs_in_block=0` with a non-zero `total_docs` is a filter problem.
- `faq_prompt` holds the ANSWERING RULES, and it applies to every FAQ answer in the flow.
  Put "do not improvise; if it is not in the documentation, offer a person" THERE, not in
  one node. And do not let it say "disambiguate first": search first, and ask only if what
  you found genuinely depends on the missing detail (San Roque v20).
- Deep heading hierarchies in the source document shrink the block but break answers:
  `get_node_content` does not return a subtree.

## 12. Caches and versioning

- `conversation_flows.version` is the freshness stamp of the worker's flow cache
  (`conversation_flow_backend_service.get_flow_version`). A document written without
  bumping it is invisible to running workers. See `versioning-and-promotion.md`.
- `llm_config` is a COLUMN of `conversation_flows`, not part of the document, and it picks
  the model for the flow. MAR-1599: it was silently dropped after the MCP removal, and flows
  "changed behaviour" when a different model took over. Check `call_timeline.model` if a flow
  changes with nobody touching it.
- The TTS audio cache keys on the RAW text. A pronunciation change
  (`language_configs.pronunciations`) is invisible for any sentence already cached; purge by
  `spoken_text`, blob before row.
