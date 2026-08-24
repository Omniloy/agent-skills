# Writing personas and evaluators that work

Launching a run is the easy half. On a real campaign — 48 voice calls against a
hospital booking agent — almost none of the early failures were the agent's
fault. They split between a persona that misheard, an evaluator that judged
something it wasn't asked to judge, and a handful of genuine defects hiding
underneath both.

This file is what that cost, written down.

---

## 1. The order of suspicion

When an execution fails, work through this order. Skipping to the last one is how
you file a bug against an agent that did nothing wrong.

1. **The persona.** It runs on a small model. It mishears, mispronounces, invents
   agreement, and picks the worst of two rules that conflict.
2. **The evaluator.** Also an LLM, also small, and strongly drawn to the overall
   objective of the call rather than the narrow criterion you gave it.
3. **The agent.** Only once the conversation reached the end and the evaluators
   are scoped correctly is a failure evidence of a defect.

A useful reflex: **an execution that never reached the end proves nothing about
any evaluator**. Fix the conversation first, then judge it.

---

## 2. Evaluators

### 2.1 They drift to the global objective

The failure is always the same shape. You ask "did the agent say X?" and the
evaluator reasons "the booking was never completed, so no". Five evaluators
rewritten with one recipe fixed it:

1. **Pass condition first**: `SE CUMPLE si...` / `PASSES if...`, then
   `NO SE CUMPLE unicamente si...` / `FAILS only if...`.
2. **Bound the scope in time**: "look ONLY at the turns where...", "look ONLY at
   what it says AFTER...". Without an anchor, normal parts of the flow get
   counted as failures.
3. **List what does not count**: "do not judge whether the booking completed, nor
   the number of turns, nor the language, nor the transcription quality".
4. **State the vacuous case**: "if the premise never occurred, it PASSES".
5. **Only the agent's voice.** The transcript may contain `[TOOL]` blocks with
   raw errors in them; evaluators will read those as if the agent said them out
   loud. Say so explicitly.

### 2.2 An evaluator can only see what was said

This one is subtle and it cost a whole afternoon. An evaluator scored a call as
failing "books two distinct appointments" — and both appointments existed in the
hospital's system. It wasn't lying: only one had been confirmed out loud.

- Write criteria about what is **observable in the conversation** ("count the
  booking exchanges: a concrete recap plus the patient agreeing"), never about
  the state of an external system.
- **Split "did it happen" from "was the caller told".** Bundled together, a
  missing announcement sinks the functional criterion and hides the real defect —
  in that case, that the agent booked the appointment and then announced a
  conflict.
- To verify facts, **query the client's system after the call** and compare. The
  evaluator confirms the script; an external check confirms the outcome.

### 2.3 Never phrase an evaluator negatively

`points` set to a negative number has **the sign discarded** when the platform
converts it to a weight. A criterion written as "the agent must NOT read error
codes aloud" scores as if reading them aloud were the goal, and "the bad thing
didn't happen" is recorded as a failure.

Write every criterion in the positive: not "does not loop asking for the
document" but "asks for the document at most twice".

### 2.4 One criterion, one evaluator

Obvious, and still the most common mistake, because it feels efficient to write
"greets, identifies the caller and offers a slot" as one evaluator. When it
fails you learn nothing about which third failed.

### 2.5 Check stability before you trust a verdict

Re-evaluate the same execution twice (§11.b of the runbook — it costs seconds).
A loosely scoped evaluator will give 95 and then 20 on identical text. If the two
passes disagree, tighten the prompt; don't retry until you like the answer.

---

## 3. Personas

### 3.1 Every rule must say when it does **not** apply

The persona model is not clever enough to resolve a conflict, and it reliably
picks the worse branch. Real collisions from one campaign:

| the rule | what it collided with | what happened |
|---|---|---|
| "if they ask you to wait, wait" | "don't go silent" | it went silent and the agent hung up on it for inactivity |
| "don't confirm until they read it back correctly" | it doesn't know `@` is spoken as "arroba" | infinite loop correcting an address that was already right |
| three different phrasings for dictating an ID | — | it used the worst one |
| "don't repeat yourself" (anti-loop) | "repeat your document if asked" | it stopped repeating the document |

### 3.2 Closing words end the run

Whatever you put in `stopping_criteria_rules` will be matched against what the
persona says — and personas say "goodbye" or "thanks, bye" in the middle of a
sentence out of politeness, ending the run mid-conversation. Forbid the closing
formula explicitly except in the real closing conditions.

### 3.3 Don't let it burn turns

Personas that answer every prompt with "understood", "of course", "all right"
double the length of a call, and long calls hit `timeout_seconds` and die. Tell
it to answer with content or stay quiet.

### 3.4 Keep it short

Defensive text accumulates: after a few fixes a persona prompt is 3.000
characters, most of it repeated warnings, and it slows the persona's own LLM
down. Rewrite rather than append.

---

## 4. Dictating data over voice

Voice agents are tested through TTS and STT, so the data your persona speaks has
to survive both. These are properties of the pipeline, not of any one client.

- **ID / document numbers**: dictate in **one single utterance**, with a phonetic
  anchor at the end — `one three, one three, one three, one three, S for Sierra`.
  A letter in a separate sentence is read as a new turn and lost; glued onto the
  last digit it is lost too. **Avoid documents ending in letters the TTS
  mispronounces** — a Spanish "D" was consistently unusable; pick one ending in
  "S".
- **Email**: the domain is what breaks. A brand domain comes back misspelled
  every single time; use a common one (`gmail.com`). Tell the persona that the
  written form equals the dictated one, or it will loop correcting a correct
  address.
- **Phone**: the harness's `caller_phone_number` may arrive malformed and be
  rejected by the client's backend. Give the persona an explicit valid number.

When a defect is *caused* by this layer and can't be fixed from the persona,
don't fight it: create the config, name it `KNOWNFAIL-...`, say so in the
description, and let it sit red on purpose. It documents the defect and turns
green the day someone fixes it.

---

## 5. What no persona or evaluator can fix

If the agent under test stalls — tens of seconds between the persona finishing
and any audio coming back — the call may die no matter what you write. Two things
to know:

- **Diagnose it from `metrics.response_times_ms`**, not from the transcript.
  A peak far above the agent's own inactivity threshold is the signature.
- **The persona may not be able to rescue it.** If the agent fills the silence
  with holding phrases, the persona's voice-activity gate treats that as "the
  agent is still talking" and keeps it quiet — exactly when speaking up would
  save the call. Instructing the persona to break the silence helps only when the
  turn actually reaches it.

Report it as an agent defect with the numbers attached, and move on.
