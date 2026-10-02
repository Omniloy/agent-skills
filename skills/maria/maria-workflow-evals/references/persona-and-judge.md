# Personas and judges for MarIA workflow evals

Read `agent-eval-api` → `references/persona-and-judge-recipes.md` first: order of suspicion,
judges drifting to the global objective, positive phrasing, one criterion per evaluator,
stability, dictating data. This adds what MarIA workflows specifically need. Every rule was
paid for by a failed run (run/config numbers are on `mariaevals.api`).

## 1. Stopping: the guardrail and its forced question

`stop_conversation` is accepted only if the agent's last turn contains a confirmation keyword
(`confirmada/o`, `reservada/o`, `agendada/o`, `queda confirmada`, `cita confirmada`) or a
transfer keyword (`te transfiero`, `le paso`, `con un humano`, `derivar`, `transferencia`).
Otherwise the platform FORCES the persona to say
`¿Entonces ya está confirmada {stopping_criteria_rules[0].text}?`.

- **Transactional flows** (book, cancel, change): the agent says the keyword itself. Do NOT put
  a Spanish noun phrase first. «la gestión de la cita» matched any early mention and killed
  the call at turn 3 (run 756).
- **Non-transactional flows** (FAQ, goodbye, language): end the script by asking the agent to
  CONFIRM something ("Por favor, confírmame que lo has anotado y terminamos"), so it answers
  «confirmado».
- **Transfer and identity-failure flows**: a Spanish noun phrase as the FIRST rule, so the
  forced question reads naturally («la transferencia de la llamada a recepción tras no poder
  verificar la identidad»).
- **Always append end-of-call catch-alls.** The platform cannot see the end node, only the
  goodbye. Without them, runs that clearly ended ran to the timeout (runs 208, 209):
  - "the agent has said goodbye / hung up";
  - "stuck on the same step for 5–6 attempts".
- Never set the legacy string `stopping_criteria`; only `stopping_criteria_rules`.

## 2. Persona scripts

- **Script the first line**:
  `"conversation_script": [{"step": 1, "action": "say", "text": "Hola, quiero …"}]`.
  Without it, the persona waits for "the greeting to finish" and never speaks. On
  2026-09-29 both San Roque scenarios got «¿me escucha?» twice and the agent hung up for
  inactivity. The judges then score an empty call; one of them passed it vacuously. The
  objective then starts at step 2: «ya has pedido X nada más empezar». `bootstrap.py` syncs
  the field (it did not before).
- **Numbered steps, one sentence per turn, wait for the agent.** Put a strict rule at the top:
  «REGLA: en cada turno dices UNA sola frase corta y esperas». Personas compress four steps
  into one turn otherwise (run 177).
- **Every rule says when it does NOT apply.** "Si te ofrecen una persona, acepta", without
  "solo si ya te han dicho que no hay huecos", makes the persona accept the first offer and
  skip what you came to test.
- **The persona does not answer a long agent monologue.** After a ~60 s text (a privacy
  policy), it stayed silent even when the objective said to always answer, and the agent hung
  up for inactivity. Do not design a scenario whose next step depends on answering a
  monologue; or put what you need to check BEFORE it.
- **Front-load what the agent will ask.** Name, document, specialty and date, in the order the
  flow asks, so a slow agent does not time the call out before the point under test.
- **Wait for the explicit closure cue before a second action** (book → cancel), or the agent
  is still finishing the first one.
- **Match `preferred_language` to the script.** For a language-switch test, start in the
  default language and ask to switch; check which language the agent opened in.
- **`max_turns`**: 8–15 for FAQ, transfer or goodbye; 30–50 for identification + booking.
- **Dictation** (see `scenario-design.md` §5):
  - a document goes in ONE sentence with a phonetic anchor for the letter;
  - to bypass the ASR on purpose, set `llm_config.identification_transcription` AND tell the
    persona to use `provide_identification` and never say the document aloud. The objective
    outranks the platform's automatic instruction (run 758);
  - check the document's check letter (`number % 23` → `TRWAGMYFPDXBNJZSQVHLCKE`).

## 3. Judges

Template that works (`SE CUMPLE` first, scoped, vacuous case explicit):

```
Evalúa SOLO lo que dice el AGENTE, no la persona ni los bloques [TOOL].
Mira SOLO los turnos posteriores a <anchor: "el paciente pide hablar con una persona">.
SE CUMPLE si el agente <the ONE behaviour>.
NO SE CUMPLE únicamente si <the concrete failure>.
No juzgues <what is out of scope: whether the booking finished, tone, language, transcription>.
Si <the premise> no llegó a ocurrir, SE CUMPLE.
<_WEB_TRANSFER_NOTE when a transfer is involved>
```

- **One criterion per evaluator, and pair it with a deterministic one when possible**:
  `tool_called: crear_cita` next to "confirmed the appointment with day and time".
- **Transfers**:
  - never require the SIP to connect;
  - never require the agent to read a number or extension aloud, since it often resolves the
    target internally and the SIP fails first (runs 205/206);
  - accept "announced or attempted, or closed with the transfer-error goodbye".
- **Quote the authored text** when the behaviour is a fixed sentence (a `static_text` notice,
  the `goodbye_message`) and accept paraphrases only if the flow generates it.
- **An execution whose TESTER never spoke proves nothing.** Look at the transcript before the
  score: a vacuous "SE CUMPLE" on a silent call is a pass nobody earned.
- **Workflow-specific things a judge must not penalise**:
  - the agent reading back the caller's own document to confirm it (allowed);
  - a bilingual line during a language switch;
  - a filler line while a tool runs.
- **Fix judges by re-evaluation, not by repeating calls.** Get ONE execution that reaches the
  end, then iterate the judge on it with `reevaluate`.
- Thresholds: 70 by default.
