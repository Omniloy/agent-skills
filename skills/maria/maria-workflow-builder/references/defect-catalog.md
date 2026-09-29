# Defect catalog: symptom → cause → fix

Use this to triage a red test, a QA call or a production call. Start from what you SEE. The
tags in brackets match the `flow_lint.py` finding tags, so a static finding points here and
here points back to a check. The examples come from San Roque (versions refer to the
worklog), except where another customer is named.

## Silences

| symptom | cause | fix |
|---|---|---|
| 15–20 s silence after the agent says «un momento, lo consulto» on entering a node | pre-generated opening cannot call tools [pregen-no-tools] | origin node calls the tool + `eval_after_tools`; or make the node global (runtime §2) |
| silence after the agent says what it will do («le paso con una persona») | announced an exit and waited for a reply [announce-and-wait] | the destination speaks (transfer code announces; `static_text`+`skip_response_edge`); description: take the exit in the SAME turn without announcing |
| silence after a node's opening line | a `pre_message` on a node that must ACT [pre-message] | remove it; let the transition filler cover the wait |
| silence after `update_flow_variables` | bare equation edge [bare-equation] | add `eval_after_tools: ["update_flow_variables"]` |
| +1.6–3.4 s after the caller chooses | update and advance in two model responses | one `update_flow_variables` call with every key + equation edges |
| the agent says nothing at all, every turn, with `{"text":"","tool_calls":[]}` in `call_timeline` | the node prompt forbids speaking (`static_text` start node, off-script opener) | fixed in code (MAR-1542, `3fac4495`); if it reappears check the runtime version |
| 30 s silence, then 500 | backend gateway timeout, filler off | backend issue; decide on tool filler for slow tools |
| silence after the caller speaks during a fixed line | pre-messages are non-interruptible: the turn is dropped | keep fixed lines short; do not put a question mid-line |

## Wrong destination

| symptom | cause | fix |
|---|---|---|
| a caller who said no hears «no he podido resolverlo, vuelva a llamar» | decline routed to the error ending [decline-is-not-failure] | separate edges: decline → polite end / human; failure → failure notice |
| «No he podido completar esta gestión» to a caller who just accepted a person | accepted offer routed through the failure notice | local edge straight to the transfer node |
| transferred without the caller asking, right after an empty result | a global condition that overlaps a local rule | narrow the global; add "last resort: if the node says how to recover, it wins" |
| hangs up while the caller is still dictating | global end condition matched «ya está» | exclude data collection from the end global |
| an identified caller is asked to identify again | two new edges chained through a hub node back to identification | name the confused case in both descriptions; forbid re-asking a document you have |
| the model takes a failure exit while the tool said success | the race: happy exit invisible, only failure exits visible [race] | "ONLY if … otherwise the transition is automatic" in failure descriptions |
| "pásame con X" treated as booking X | noun over verb | "the verb wins over the noun" in the intent edge |
| an edge is never taken | the prompt never offers it (closed menu) | offer it in the prompt, or delete it |
| two sibling edges are confused | shared preamble, discriminant last [discriminant-last] | discriminant first |
| the model loops routing between two nodes | two globals, or a global and a local edge, with the same condition | make them disjoint |

## Wrong content

| symptom | cause | fix |
|---|---|---|
| «no tiene ninguna cita» / «no existe» when it does | empty list read as a negative [empty-result] | «a mí no me aparece» + alternatives + person; check filters that hide data |
| a whole specialty declared unbookable after one empty centre | an empty list interpreted as a global negative | «no slots at THAT centre», try the others |
| the recap names the wrong centre / agreement | a variable kept from the request, not from the chosen item | save the chosen item's own fields |
| accents missing, «el doctor» for a woman, SURNAME, NAME | raw backend value in a `pre_message` | presentable value from the model via argument descriptions |
| one fixed sentence in Spanish in an English call | missing translation, silent fallback [language] | add every language to fixed texts |
| the FAQ answers «no dispongo de esa información» to everything | no collection linked (clone) — `total_docs=0` [clone-kb] | copy `conversation_flow_collections` |
| the node transfers instead of answering from the FAQ | `get_node_content` never called: cheap alternative first, two contradicting lists, listed in `tool_ids`, or pre-gen opening | STEP 1 call; one list; do not list the built-in; global node |
| invents facts («volante», codes not in the KB) | no "do not improvise" rule in `faq_prompt` | answering rules in `faq_prompt`, which applies to every FAQ answer |
| invents an identifier | no rule, or a loop that re-asks | "only ids a tool returned"; fix the loop |
| reads an error / id aloud | common rules never reached the node (`global_prompt` = start node only) [global-prompt-start-only] | common-rules block in every prompt node |
| the announcement «le paso con…» twice | the flow and the code both announce | remove the flow's |
| a Jinja/`{{var}}` renders empty on the first turn only | older runtimes rendered the node prompt twice (server vs client) | check the runtime; on current dev there is one reader |

## Tool calls

| symptom | cause | fix |
|---|---|---|
| backend 400 for a field the model sends empty | optional argument filled with `""` beats `query_params` [optional-arg] | do not expose it; a second tool with the argument required |
| backend 400 on every write, «could not be converted to Boolean» | `body_params` value stored as a string [body-types] | real JSON types; check after every UI save |
| the model negotiates dates when you wanted a coverage check | the same tool used for two jobs; the prompt's parameter makes it think it is searching | a dedicated tool whose signature carries the invariant |
| results truncated / JSON cut | response over 8000 chars | ask the endpoint for fewer items |
| timeout, but the backend did the write | `timeout_s` below the backend's cold write, or above the 60 s cap | measure; set `timeout_s` with headroom ≤ 60 |
| a tool is never available | name outside the grammar, `url` without `type:"custom"`, unresolvable `tool_ids` entry, or a name no package serves | fix the definition; check the worker's warnings |
| `eval_after_tools` edge never fires | the tool is not in `tools[]` | register it |
| a cancel/reschedule of an appointment the API lists fails with "not found" | backend state out of sync (San Roque's wrapper) — a broken-state error | not a flow bug: route to a person; skip in tests with the reason |
| a just-created appointment is not listed | backend sync lag | «no me aparece» + person; do not deny it |

## Environment and versions

| symptom | cause | fix |
|---|---|---|
| a change to the flow has no effect on live calls | document written out of band: `version` did not move | save through core-service; health query |
| a clone calls production / real phones | environment values copied with the document | host guard, keep-vars, test transfer number |
| a flow changes behaviour on a day nobody touched it | `llm_config` dropped or model chain changed (MAR-1599) | check `call_timeline.model` |
| a pronunciation fix does not sound | TTS cache keyed on raw text | purge by `spoken_text`, blob first |
| a test passes against the wrong flow | the flow id in the task is not what the api_key serves | query `api_keys.default_conversation_flow_id` first |
