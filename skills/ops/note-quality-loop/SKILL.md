---
name: note-quality-loop
description: Operate a periodic quality loop over generated clinical notes. Once daily (09:00 by default) it samples 10% of the notes per assistant directly from Supabase (read-only), judges each against the active SofIA eval rubric with literal evidence, aggregates per assistant and per template with deltas vs the previous run, and publishes a Slack canvas + short channel message naming which assistants/templates are failing. Also drafts follow-up emails on request (never sends). Use this whenever the user wants to "quality loop", "notas clínicas", "monitorear calidad de notas", "muestrear notas", "reporte de calidad a Slack", "qué asistentes/plantillas están fallando", or asks who to escalate when note quality drops. Requires Supabase MCP (read-only) and Slack MCP.
user-invocable: true
---

# note-quality-loop

A periodic observability loop over the clinical notes that the SDK generates. It exists to catch failing assistants and templates **before a client complains** — not to produce a pretty metric.

The loop has **two one-time phases** (kickoff + data contract) and **four per-run phases** (sample → judge → aggregate → publish), plus an on-demand email draft. There is **no helper script**: the model executes every phase directly via Supabase MCP, Slack MCP, and file I/O, following the exact templates and rules in this document and its references. Never write to Supabase. Never publish unmasked PII.

## 0. Preconditions

- **Supabase MCP** available with `execute_sql`, configured **read-only** (`read_only=true` in the MCP URL — the DB enforces read-only, the model doesn't remember to). If the server isn't listed, stop and say so — the skill cannot run without it. If `execute_sql` would accept writes, fix the MCP config before continuing.
- **Slack MCP** available (only needed for publishing; dry-runs skip it).
- Config at `~/.claude/note-quality-loop/config.json` (produced by the kickoff, §1). If missing → run the kickoff first.
- If a rubric is needed and not yet cached → resolve it via the SofIA evals API or Supabase read (see `references/rubric.md`).

## 1. Kickoff — run ONCE (attended)

If the config file already exists, skip straight to §2 — do not re-ask.

Ask the **four** open questions with `AskUserQuestion` (one call, recommended option first). Record answers in the config file. Never hardcode the answers into the skill.

1. **Slack channel + mention** — where the report goes (decided: `#sofia-sdk-info`) and **who to mention** when an assistant exceeds the escalation threshold (e.g. `@equipo-clinico`). Options: the proposed mention / nobody / a specific user.
2. **Escalation threshold** — % of notes with a `major+` finding per assistant that triggers the mention (decided: **15 %**; option to raise/lower).
3. **Sample size** — 10 % per assistant with min 3 / max 15, global cap 120 notes/run (recommended, per plan).
4. **Email drafts** — whether phase 6 (draft follow-up emails) is enabled. Recommended: enabled, never sent.

Also **confirm** (do not re-ask, these are decided): cadence **1×/day at 09:00** (seniors' choice: "1×/día, sino será mucho ruido"), criterion **rubrics from the SofIA evals DB** (Option A), channel `#sofia-sdk-info`.

Write the config file:

```json
{
  "schedule": {"time": "09:00", "timezone": "Europe/Madrid", "daily": true},
  "slack": {"channel": "#sofia-sdk-info", "mention": "@equipo-clinico", "mention_when_above": 0.15},
  "sampling": {"fraction": 0.10, "min_per_assistant": 3, "max_per_assistant": 15, "global_cap": 120},
  "rubric": {"source": "sofia-evals", "evaluation_type": "note_generation"},
  "email_drafts": true,
  "history_file": "~/.claude/note-quality-loop/history.jsonl",
  "loop": {
    "state_file": "~/.claude/note-quality-loop/state.json",
    "armed": false
  }
}
```

Then initialize the loop state file (§8) and run the data contract (§2) once, caching its result in the config (`data_contract` key).

## 2. Resolve the data contract — run ONCE, cache it

Never hardcode table/column names. The real schema is discovered via Supabase MCP.

1. List candidate tables: query `information_schema.tables` (or list relations via MCP) filtering for names like `*run*`, `*note*`, `*langsmith*`.
2. For the chosen table, resolve the **six column roles** from `references/data-contract.md`: `id`, `created_at`, `assistant_id`, `template_id`, `note`, `input` (optional).
3. Present the mapping to the user for confirmation **before** caching it in the config.
4. Cache as `data_contract` in the config file: `{"table": "…", "columns": {"id": "…", …}}`.

If `input` does not exist → the fidelity axis is unavailable; the rubric-only mode still works (it never needs `input`). Record that in the config.

## 3. Sample — every run

Window = **24 h** before the scheduled time (or `--from/--to` for backfill). Compute the boundary with a shell one-liner (the boundary is the last `HH:MM` in the configured timezone strictly before now; `from = boundary − 24 h`).

Emit the deterministic sampling SQL using the cached column roles, then execute it via Supabase MCP (`execute_sql`). The template — fill `<cols>` from the data contract:

```sql
WITH pool AS (
  SELECT <id>, <created_at>, <assistant_id>, <template_id>, <note>[, <input>]
  FROM <tabla_runs>
  WHERE created_at >= '<from>'::timestamptz
    AND created_at < '<to>'::timestamptz
    AND <note> IS NOT NULL AND length(<note>) > 0
), ranked AS (
  SELECT *,
    row_number() OVER (PARTITION BY <assistant_id>
                        ORDER BY md5(<id>::text || '<seed>')) AS rn,
    count(*)     OVER (PARTITION BY <assistant_id>) AS n_total
  FROM pool
)
SELECT * FROM ranked
WHERE rn <= least(<max_per_assistant>,
                greatest(<min_per_assistant>, ceil(n_total * <fraction>)))
ORDER BY <assistant_id>, rn;
```

**Never use `random()`** — the `md5(id || seed)` ranking makes the same window + seed reproduce the exact same sample, so a finding can always be re-checked. Use a **stable seed** (e.g. `nql-2026-08-26` = the window date) so re-runs of the same window match, and a different seed per day. Enforce the global cap of 120 notes/run when reading the result. If the query returns 0 rows, report "no notes in window" and stop — do not publish an empty report.

## 4. Judge — every run (LLM, structured output)

Group the sampled notes by `assistant_id`, judge in batches of ~10. Load the active rubric first (see `references/rubric.md`):

- If the rubric is cached in the config → use it.
- Else resolve the **active** rubric for `evaluation_type=note_generation` (SofIA evals API or Supabase read). If more than one active rubric exists, ask the user which one (or the newest).

For each note, emit a verdict with the **exact JSON Schema** from `references/rubric.md`. Non-negotiable rules:

- **Every finding requires a literal quote ≤ 200 chars** from the note (`quote`). A finding without a verifiable quote is discarded.
- Severity is **assigned by the judge** per finding: `minor` | `major` | `blocking`, using the rubric's `polarity`/`category` as a guide (penalty in `safety` → `blocking`; required section missing → `major`; format/language → `minor`).
- Judge against the **rubric criteria** (the active SofIA rubric), not against an ad-hoc list. Map each rubric criterion to a finding when it fails (negative criteria met, or positive criteria not met).
- **Mask PII in quotes as you write them**: names → `[Nombre]`, NHC → `[NHC]`, phones → `[Teléfono]`, DOB → `[FechaNac]`, email → `[Email]`, address → `[Dirección]` (full list in `references/report-format.md` §3).
- A note with ≥1 `major+` finding counts as "failing" for the aggregation.

Save the per-note verdicts as a JSONL file (one verdict per line) for the next phase.

## 5. Verify + aggregate — every run, no LLM

**Verify quotes first (anti-hallucination, mechanical):** for each finding, confirm the `quote` appears **literally** in the original note text (use `grep -F` or read the note). Drop any finding whose quote is missing or > 200 chars. If a note ends up with zero surviving findings, mark it `pass`. This is the check that stops the judge inventing problems.

**Aggregate (count, do not re-judge):** build the metrics in your session, then append this run's summary to the history file (`history.jsonl`):

- **Per assistant and per template:** sampled / total in window / % with `major+` / worst severity.
- **Top-3 recurring patterns:** group findings by (criterion_id + normalized quote); a pattern needs ≥ 2 occurrences; **rank `major+` first**, then by count. Each pattern carries its literal quote and affected templates.
- **Delta vs previous run:** read the last line of `history.jsonl`, compare `pct_major_plus` (trend, not snapshot). If the rubric changed between runs, mark the delta `n/a` — do not compare different rubrics.
- **Escalation:** an assistant is `above_threshold` when its % `major+` > the configured threshold (15 %).

Write the aggregate to a file (e.g. `<run>.agg.json`) for the publish phase.

## 6. Publish — every run

If the loop is **not armed** (`state.loop_armed == false`), produce the message + canvas and show them locally — do NOT publish to Slack. The loop only publishes after a human validates a real dry-run and arms it (§8).

Render the **channel message** (5–8 lines) and the **canvas** per `references/report-format.md`. Layout rules:

- Channel message: window label · sampled count · delta line (`▲ 24 %` / `▼ 17 %` / `= 20 %`, or "primer run") · top-3 worst assistants (id, failing/sampled, severity) · dominant pattern (first `major+` pattern, else top) with its quote · mention line only when some assistant exceeds the threshold, with the canvas link.
- Canvas: summary (window, sampled, % major+, delta) · per-assistant table · per-template table · top-3 patterns with quotes · template-change suggestions **with proposed text** (not "review the prompt") · annex with per-note verdicts.

**PII gate (blocking) — apply the exact rules in `references/report-format.md` §3 before publishing.** Scan every quote that will be published; if any unmasked identifier (NHC, name with a patient/title marker, phone, email, DOB) is found, **stop — do not publish**. Fix the offending verdicts (re-mask) and re-render. The gate is conservative: when in doubt, fail. Never disable it, never publish a full note (only masked quotes ≤ 200 chars).

Then publish via Slack MCP: create/update the **canvas** with the full body, and post the **channel message** (thread reply or standalone). The mention fires **only** when some assistant exceeds the threshold.

## 7. Follow-up email draft — on demand

In the report thread, when the user asks "prepárame el correo para X": draft a follow-up email with the findings and the already-redacted evidence. **Never send it.** Present the draft in the thread for review. Disabled if `email_drafts: false` in the config.

## 8. Scheduling — two modes

The skill runs the same per-run pipeline in either mode; only the *trigger* differs.

### Mode A — Desktop scheduled task (recommended for production)

A **local scheduled task** in Claude Code Desktop (Code tab → **Routines** → New routine → **Local**). It starts a fresh session at the configured time (09:00) with access to your files and MCP servers, with no session kept open. See [desktop-scheduled-tasks](https://code.claude.com/docs/en/desktop-scheduled-tasks).

- The task's **instructions** (its `SKILL.md` at `~/.claude/scheduled-tasks/<task-name>/SKILL.md`) should say: *run the note-quality-loop skill; read the loop state; if `loop_armed` is false run in dry-run and report locally, if true run the full pipeline and publish*.
- The task's **folder** must be the project repo (where the Supabase/Slack `.mcp.json` lives).
- On creation, click **Run now** once, approve the permission prompts (MCP tools, file writes to `~/.claude/note-quality-loop/`), and select **always allow** so future runs don't stall.
- Schedule: **Daily, 09:00**. Pick the preset or ask in plain language.
- **Keep computer awake** (Settings → Desktop app → General) so the run isn't skipped during sleep.
- **Missed runs:** Desktop catches up with at most one run for the most recently missed time (7-day window). The 24 h window derived from the schedule means a catch-up run still samples correctly — but it may run late (e.g. 11 pm instead of 9 am). The `last_run_at` double-fire guard still applies.

### Mode B — Claude `/loop` (self-paced, dev)

A **dynamic Claude `/loop`**: the loop fires once a day, does its work, and sleeps via `ScheduleWakeup` until the next boundary (default 09:00). Re-entry is safe because every wake reads the loop state file before acting. Requires the session to stay alive; fine for validation, not for production.

```text
/loop /note-quality-loop
```

Each wake: read the loop state → double-fire guard → dry-run or publish per `loop_armed` → update state → `ScheduleWakeup` with the **full `/loop …` prompt verbatim** so the next firing re-enters the loop.

### Loop state file (both modes)

`state.json` (default `~/.claude/note-quality-loop/state.json`) — plain JSON, read/write it directly:

```json
{
  "kickoff_done": true,
  "contract_done": true,
  "loop_armed": false,
  "last_run_at": "2026-08-26T09:00:00+02:00",
  "last_window": "2026-08-25T09:00:00+02:00"
}
```

### Armed — the human gate (both modes)

The loop never publishes to Slack until a human has validated a real **dry-run** end-to-end (every finding's quote present in the note, PII gate passing). To arm, flip the flag in the state file:

```json
{ "loop_armed": true }
```

Until then, every wake runs in dry-run and reports locally. Arming is the only switch that turns on real publishing.

### One run (what the scheduled task / loop does each time)

1. Read `state.json`. If `last_run_at` is inside the current 24 h window → **already ran, finish** (double-fire guard; Desktop catch-up runs rely on this).
2. Compute the window: `from = <last 09:00 boundary> − 24 h`, `to = <boundary>` (in the configured timezone).
3. Sample 10 % per assistant (deterministic SQL, §3) via Supabase MCP.
4. Judge against the active SofIA rubric (§4), masking PII, quotes ≤ 200 chars.
5. Verify quotes + aggregate + delta (§5), write history.
6. If `loop_armed == false` → produce message + canvas and **show locally, do NOT publish**. If `true` → apply the PII gate and publish (§6).
7. Update `state.json` (`last_run_at`, `last_window`).

### Example scheduled-task prompt (Mode A)

```text
Run the note-quality-loop skill (installed in the project's ~/.claude/skills/).
Read ~/.claude/note-quality-loop/state.json and execute exactly one run per the
skill's SKILL.md §8 "One run". If loop_armed is false, dry-run and report locally.
If true, publish the report to the configured Slack channel. Never write to
Supabase; the MCP is read-only. Never publish unmasked PII.
```

## Guardrails (always)

- **Read-only Supabase.** Never execute INSERT / UPDATE / DELETE / DDL. If the MCP server is write-capable, still never write.
- **No credentials in the skill.** Never store tokens, passwords, or API keys in `config.json`, `state.json`, or anywhere in the skill directory. All auth lives in the MCP server config (`.mcp.json`).
- **Global cap** of 120 notes/run; per-assistant min/max enforced in SQL.
- **Literal evidence required** — every finding carries `note_id` + quote ≤ 200 chars; a finding whose quote isn't found in the note is discarded (verify before aggregating).
- **PII gate is blocking** — an unmasked identifier stops the publish; never disable it.
- **Email drafts are never sent.**
- **The loop publishes only after a human arms it** (`state.loop_armed = true`); dry-run until then.
- **No hardcoded table/column names** — everything goes through the cached data contract.
- The rubric is **append-only** (SofIA evals rule): never edit a rubric; if criteria must change, create a new one and re-baseline. If the active rubric changes between runs, the deltas are not comparable — note it in the report.

## References
- `references/data-contract.md` — the 6 column roles and how to resolve them via Supabase MCP.
- `references/rubric.md` — loading the SofIA rubric, severity mapping, judge JSON Schema.
- `references/report-format.md` — canvas + message layout and the exact PII gate rules.
