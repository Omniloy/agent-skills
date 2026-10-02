# Designing and writing a flow

How to shape a booking + FAQ flow so it survives real callers. It is distilled from San Roque:
51 clone versions and 5 production pilots between 07/09 and 28/09/2026. The worklog is in
maria-voice `docs/wip/san_roque_piloto_fixes/WORKLOG.md`, and each rule cites the version
that paid for it. Runtime mechanics are in `runtime.md`; read that first.

## Contents

1. [The blueprint: booking + FAQ](#1-the-blueprint-booking--faq)
2. [The outcome matrix: design every node against it](#2-the-outcome-matrix)
3. [Choosing the mechanism for each thing a node does](#3-choosing-the-mechanism)
4. [Writing node prompts](#4-writing-node-prompts)
5. [Writing edge descriptions](#5-writing-edge-descriptions)
6. [Global nodes](#6-global-nodes)
7. [Identity and documents by voice](#7-identity-and-documents-by-voice)
8. [Reading backend data to a caller](#8-reading-backend-data-to-a-caller)
9. [A common-rules block for every prompt node](#9-a-common-rules-block)
10. [Anti-patterns](#10-anti-patterns)

---

## 1. The blueprint: booking + FAQ

Most customers need the same skeleton. Start from it and delete what the customer does not
have. Do not start from a blank graph.

```
node-saludo (static_text, start, speaks first)
  ├─ new appointment ──► n_auth_nueva ──(patient found, eval_after_tools)──► n_especialidad
  │                          └─ not found ─► registration chain (n_alta_*) ─► n_especialidad
  ├─ manage appointment ► n_auth_gestion ─(lookup done, eval_after_tools)──► n_citas
  │                                                                           ├─ cancel ─► n_cancelar
  │                                                                           └─ change ─► n_reagendar_*
  └─ anything else ────────────────────────────────────────────────────────► n_otro_motivo (GLOBAL)
n_especialidad ─► (coverage check) ─► n_centro ─► n_huecos ─(slot chosen)─► n_confirmar
n_confirmar ─(booked, eval_after_tools)─► n_algo_mas ─► node-end | n_citas | n_otro_motivo | …
Globals:   node-end (end, "wants to hang up")
           n_operador (transfer_call, "caller ASKS for a person")  ── transfer_failed_edge ─► n_transfer_fallo
           n_fallo_operador (static_text, "an operation failed and cannot be retried")
                  └─ skip_response_edge ─► n_operador
           n_otro_motivo (prompt, "a question that is not a booking") ─ answers from the FAQ
```

The pieces that earned their place:
- **A global "other question" node** (`n_otro_motivo`). The saludo only knew bookings, and
  in pilot 1 fifteen of seventeen lost callers died before reaching the booking part.
  - It must be GLOBAL. A node entered by an edge opens with a pre-generated line that cannot
    call `get_node_content` (v23).
  - It answers from the FAQ and, when the answer is not there, offers a person instead of
    improvising.
  - Real reasons appear AFTER identification ("me habéis llamado y no sé por qué", v15), so it
    must be reachable from everywhere.
- **Two human routes, split by whether an explanation is owed.**
  - A caller who ASKED for a person goes straight to `n_operador`, with no notice.
  - A caller whose operation FAILED goes through `n_fallo_operador`, a `static_text` that says
    so, then `skip_response_edge` to `n_operador`.
  - A person OFFERED by us and accepted also goes straight to `n_operador`. Routing it through
    the failure notice made callers hear «No he podido completar esta gestión» after merely
    accepting help (v29, v31).
- **A transfer-failed fallback** (`n_transfer_fallo`). It gives the PUBLIC number and says
  goodbye (v37).
- **A local edge into `node-end` from every node that can finish**, besides the global one
  (§8 of `runtime.md`, v36).
- **A verification node** for operations whose result is unknown (timeout, conflict). It
  CHECKS the backend and has an exit to a person when the check cannot settle it. Never
  infer the outcome from the error (v11, `n_verificar`).
- **An "anything else?" node** after each completed operation, with exits to every intent
  and to the end.

## 2. The outcome matrix

The single most productive review. For every conversation node, fill in where each of these
goes. An empty cell is a caller who hangs up. San Roque v9, v11, v17, v31 and v36 were all
"the exit that was missing".

| the caller… | goes to | common mistake |
|---|---|---|
| gives what the node needs | the next step (often an equation after a tool) | — |
| **declines / changes their mind** | a polite end, or back to the menu | routed to the ERROR ending: «no he podido resolverlo, vuelva a llamar» to someone who simply said no |
| **asks for a person** | `n_operador` (global jump is enough if the global condition says "the caller asks") | — |
| **accepts a person we OFFERED** | `n_operador`, by a LOCAL edge | through the failure notice («no he podido…») |
| **the operation fails** (backend error, timeout) | `n_fallo_operador`, or a checking node when state is unknown | «gracias, adiós»; or telling them it worked |
| **the backend says "none"** | say "I cannot see it", offer alternatives + a person | «no tiene ninguna cita» to a caller holding the printed appointment |
| **asks something else** (FAQ) | global `n_otro_motivo` | forcing them back to the menu |
| **is unintelligible / talking to someone else** | re-ask ONCE, take no exit, call no irreversible tool | «Vale, eso. O <nombre>, cállese» read as «vale» → booked / hung up (v24–v26) |
| **wants to hang up** | local edge into `node-end` | improvised goodbyes |
| **is calling for someone else** | name WHOSE document; without it, a person | answering about the caller instead of the patient (v14) |

Also check every node's edge list against what its prompt OFFERS. An edge the prompt never
offers is dead. An offer with no edge is a dead end, or a coin toss with a global.

## 3. Choosing the mechanism

| the node must… | use | not |
|---|---|---|
| say a fixed sentence and move on | `static_text` + `skip_response_edge` | "tell them X and take exit Y" in a prompt (the model speaks and waits; 2/4 times it skipped the sentence, v8) |
| open with the same question from every predecessor | `pre_message` + trim the instruction ("you have ALREADY asked for X") | a pre-generated opening (~1 s TTFT) |
| open differently depending on where the caller came from | split the node by entry path, or leave the opening generated | a `pre_message` that is false on half its entries (v35, P4-2) |
| act (call a tool) on entry | the ORIGIN node calls it + equation `eval_after_tools`; or make it a global node | a `pre_message` holding line (17 s silence), or "on entry call X" (pre-gen has no tools) |
| move on a tool result | equation + `eval_after_tools` | a prompt edge the model must remember to take (+1.6–3.4 s when it takes a second response, v33) |
| save a choice AND route | ONE `update_flow_variables` with every key, equation edges on it | update + advance in two responses |
| enforce a value the model must not choose | fix it in `query_params`/`body_params` and do NOT expose it in `args_schema`; make a second tool if needed | an optional argument (the model sends `""`, which wins, v7/v41) |
| decide on a tool failure deterministically | the tool writes a variable, `== ""` equation after it | hoping the model reads the error text (it said «su cita sigue como estaba» and waited, v47) |
| route to a human from anywhere | a global `transfer_call` node | N×M edges |

## 4. Writing node prompts

What worked, each rule with the version where the alternative failed.

1. **"STEP 1, before saying anything and always: call X. Do not announce it. Do not decide
   yet."** For a node whose job is to look something up, the first sentence of the prompt is
   the call (v13, v16). Also forbid what the model does by default when the question looks
   ambiguous: re-asking before searching. Allow the clarifying question only AFTER the search,
   and only if what was found does not settle it.
2. **Never open with the cheap alternative.** A prompt that opened with "you can answer from
   the documentation, or pass them to a person" made the model take the second option before
   reaching the first (v13).
3. **Name the case that gets confused, explicitly.** Rules applied by omission fail.
   - «Querer otra cita NO es esta salida» fixed a loop that sent identified callers back to
     identification, where the model invented a DNI (v21).
   - «"Pásame con dermatología" is asking for a person, not an appointment: the verb wins
     over the noun» (v13).
4. **Say which cases a rule does NOT cover.** The model applies rules wider than written.
   - "A result or a report" in the send-to-a-person list swallowed "what are your hours to
     collect a result?" (v10).
   - A rule about doubtful letters D/E/C/S made it query every DNI containing a D (v45 → v46:
     doubt rules apply only AFTER an empty search).
5. **Positive phrasing.** "Dictating in words is the normal way on the phone: treat it as valid
   and convert it yourself" beats "NEVER tell them the format is wrong".
6. **One list, one place.** Two exception lists in one node contradicted each other (two vs
   four cases) and the model followed the wrong one (v22). The same list goes in the prompt
   AND the matching edge description.
7. **Confirming an irreversible action requires a clear yes addressed to the agent.** "Yes"
   mixed with sentences to someone else confirms nothing; noise after a clear yes does not
   cancel it (v25, v32).
8. **Choosing WHICH item is not choosing WHAT to do.** "The first one" (which appointment) is
   not "change it" (v27). Only route when both are known.
9. **Do not ask for what the flow already has.** Birth date after identification (~16 times,
   v29); the DNI again after a detour (P4-2). Look at earlier turns before asking.
10. **Stop adding rules when they stop working.** Past a point, each new rule dilutes the rest.
    The lever becomes the edge description, fewer edges (v19: 58 → fewer visible edges), or a
    structural change such as a global node or a split tool.
11. **Examples in the prompt must differ from the ones the tests use.** Otherwise the test
    measures memorisation (v50).
12. **Never infer an outcome from an error.** "Not found" when cancelling does not mean it was
    already cancelled (v11); a CONFLICT on retry does not prove the first attempt succeeded.
    Send the call to a node that checks, with a human exit.
13. **No transition acknowledgements in the source node.** «Perfecto, déjeme consultar…»
    written into the source node's instruction is generated in the same LLM turn, BEFORE the
    tool call, so it delays the transition (~+0.4 s measured) instead of masking it. Tell the
    model to call the tool immediately and silently; the destination speaks next (MAR-1337).
14. **One outcome, one announcer.** If node A decides the outcome and node B announces it
    again, they will disagree (B re-derives it from raw tool history). Make the destination
    silent about the outcome, or give each outcome its own destination.

## 5. Writing edge descriptions

The description is what the model reads when it routes. It outranks the node instruction
for routing decisions.
- **Discriminant first, in capitals; shared guard after, short** (v28). Two edges that share
  a long preamble and differ in four words at the end get confused.
- Failure exits next to an invisible happy equation must say **"ONLY if … If not, do NOT
  take this exit and do NOT call advance_conversation_flow: the transition is automatic."**
- Say what the edge is NOT for when a sibling is easily confused ("choosing which
  appointment is not this exit", v27).
- A precondition can live in the description: "ONLY after get_node_content was called and
  did not answer" fixed a node that transferred without searching (v18).
- On a `static_text` node the description is the only place routing can live, because the
  instruction is spoken.

## 6. Global nodes

- A `global_condition` is offered in EVERY node. Write it for all of them:
  - "not while the caller is dictating data" (on San Roque «ya está» meant "I finished
    dictating");
  - "not in the middle of a booking";
  - end with a LAST-RESORT clause: "if the current node says how to recover, its text
    wins" (v34).
- **Make globals disjoint from local edges.** Read every global condition against every
  node's edges. A global that says "no agenda for what they ask" beat the local rule "try the
  other centres" three seconds after an empty list (v33).
- **An empty `global_condition` is dropped silently.**
- **Globals and their local twins.** When a node's prompt OFFERS the same destination, give
  it a local edge too. The global's condition may say "the caller must ASK", and then the
  model has two rules that contradict (v11 B). Remove local edges the prompt does not offer
  (v19).

## 7. Identity and documents by voice

- **Nobody dictates a document as written.** `seis, ocho, tres…`, `68 35 94 82 D`, `sesenta y
  ocho mil…`, "pero" for "cero", date-like slashes, "C de casa" (the letter is C, not the D of
  "de"), doubled letters, the caller starting again. Converting is the node's job; say so
  positively (v45, v46).
- **Count the digits on the document written out continuously**, not the pieces the caller
  said. «43, 7, 54, 11, 6, C» is 8 digits; counted as 7, it made the node ask for a zero that
  was not missing (v34). When in doubt, SEARCH anyway.
- **Read back what you captured before concluding "not registered"**, digits as words in
  groups with the letter at the end. Carve it out of the "never read identifiers" rule: this
  is the caller's own document. Common recoveries:

  | what you counted | almost always |
  |---|---|
  | 7 digits + letter | a dropped leading zero |
  | starts with a letter | a NIE: X/Y/Z are hard to hear; ask with an anchor |
  | 8 digits + letter, empty result | a misheard digit: read it back once |

- **Always search again after a correction** before offering registration. Offering
  registration to someone who exists is how duplicate records are born (P5-5).
- **Say whose document.** «¿Me indica el DNI o NIE del paciente? Puede no ser usted.» Without
  the patient's document, go to a person; never substitute the caller's (v14).
- **Never invent or rebuild an identifier.** Every id comes from a tool result (v21: a
  number the model invented can belong to a real person).
- **Several records per document.** Decide by name and surnames, matched in both directions
  word by word, allowing for STT and spelling variants (Canarian/Latin-American names). A
  different birth date does NOT separate records: duplicates are usually badly registered
  (v48–v51). Every record that matches is theirs.

## 8. Reading backend data to a caller

- **Presentable values come from the model, not the backend.** A `pre_message` interpolating
  the raw field read «angiologia … con el doctor bordes galvan, elisa»: no accents, no gender,
  "surname, name". Ask for the presentable form in the tool's argument description
  ("specialty_name exactly as you say it to the patient") and put the title inside
  `doctor_name` (v28). First check that the backend ignores those display arguments.
- **An empty result is "I cannot see it", never "it does not exist".** The same empty list
  can mean different things:
  - San Roque free-spaces: "your insurance does not cover it", "no slots now", or "not
    bookable by phone";
  - San Roque appointments: "you have none", "it is on another record", or "the API has not
    synced yet (>95 s after creation)".

  Say what you know, offer the alternatives and a person (v27, v30, v35, v38).
- **Search the other options before saying no.** One empty centre is "no slots at THAT centre":
  name it, offer the others (same island first), re-search the one they accept, and offer a
  person from the FIRST empty result (v30, v31, v36). Cap the date-hunting: after two empty
  dates, say there is no agenda soon and offer a person (P5-6).
- **Keep filters that can hide live data out of the request.** `status_id=scheduled` returned
  0 appointments to patients who had them. The nodes now filter on the returned
  `appointment_status` themselves (v43).
- **Carry the chosen item's own fields forward** (centre, agreement): the slot's centre, not
  the centre asked for at the start (v3).
- **Never read raw tool errors aloud**, and never read internal ids. Dictate phone numbers
  slowly, in groups, and ask if they got it (v36, v37).

## 9. A common-rules block

`global_prompt` reaches only the start node. Append a short block like this to EVERY `prompt`
node, but not to `static_text` nodes, which would read it aloud (v30). Keep it identical so a
script can assert it:

```
REGLAS DE TODA LA LLAMADA (aplican también aquí):
- Nunca leas en voz alta un error de herramienta ni un identificador interno.
- Una sola pregunta cada vez.
- Nombres de personas, especialidades y centros con mayúsculas y tildes correctas, nunca en MAYÚSCULAS.
- Fechas y horas en lenguaje natural («el martes 14 a las diez y media»).
- No inventes ni reconstruyas identificadores: solo los que devuelve una herramienta.
- Si no entiendes lo que te dicen, pide que lo repitan una vez; no tomes ninguna salida.
```

## 10. Anti-patterns

- A holding `pre_message` on a node that must act.
- "Tell them X and take exit Y" in a prompt, and announcing a transfer the code already
  announces.
- A bare equation on `update_flow_variables`.
- A rule in `global_prompt` that is meant for every node.
- A rule in a `static_text` instruction: it is read aloud.
- Asking the model not to hang up instead of giving it an edge into `end`.
- A decline routed to the error ending.
- A failure routed to «gracias, adiós».
- An optional tool argument that must not be sent.
- An orphan tool, or a declared variable nobody writes.
- Different texts for the same outcome in two nodes.
- More rules, when the fix is structural.
