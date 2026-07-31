---
name: feature-to-tasks
description: >-
  Creates and maintains the technical tasks of the Jira SOC board (SofIA Care) to
  the team's standard. Mode create - decomposes a Feature, or a whole Epic, into N
  tasks, one per monorepo package, each carrying Requisito, Funcional, Enfoque
  técnico, Alcance IN/OUT, Puntos de entrada, Contrato, Criterios de aceptación,
  Comprobación and Riesgos ISO 14971. Mode refine - re-reads an existing ticket and
  brings it up to date with what has been learned (paths now real, contract closed,
  approach corrected), showing a section-by-section diff and leaving a comment.
  Mode audit - re-validates the board against the standard. Dry-run unless --apply.
  It never assigns anyone, never creates Features, never touches Verifications, and
  never widens the scope of an existing ticket. Use it whenever someone asks to
  "crear las tareas técnicas de SOC-n", "descomponer esta feature", "generar el
  backlog técnico de este epic", "actualizar el ticket con lo que hemos aprendido",
  or to check whether the SOC tasks still match the standard.
allowed-tools: Read, Grep, Glob, Bash, AskUserQuestion, mcp__claude_ai_Atlassian__getAccessibleAtlassianResources, mcp__claude_ai_Atlassian__getJiraIssue, mcp__claude_ai_Atlassian__searchJiraIssuesUsingJql, mcp__claude_ai_Atlassian__getJiraProjectIssueTypesMetadata, mcp__claude_ai_Atlassian__createJiraIssue, mcp__claude_ai_Atlassian__editJiraIssue, mcp__claude_ai_Atlassian__addCommentToJiraIssue, mcp__claude_ai_Atlassian__createIssueLink
user-invocable: true
---

# feature-to-tasks

Turns SOC Features into technical tasks an AI can implement without a human
re-explaining the work, and keeps those tasks true as the code appears.

The whole point is that ~150 tickets come out **identical in shape**. That only
survives if the format is copied rather than remembered, so the templates live in
`references/` as literal files. Read them; do not write a ticket from memory.

| File | What it fixes |
| --- | --- |
| `references/tarea.md` | The nine-section template, per-section rules, three worked examples |
| `references/monorepo.md` | Package layout and the `just` command contract — the only source for paths and commands |
| `references/convenciones.md` | Summary, labels, hierarchy, links, fields, rollback |
| `references/refinado.md` | The live-ticket rules and the anti-scope-creep guardrail |
| `references/verification.md` | The Verification standard — **deferred, do not act on it** |

## Input

```
/feature-to-tasks SOC-1                  # one Feature → its technical tasks
/feature-to-tasks E4                     # a whole Epic's Features
/feature-to-tasks SOC-1 --apply          # actually write to Jira
/feature-to-tasks refine SOC-201         # bring one ticket up to date
/feature-to-tasks audit                  # re-validate the whole board
```

**Dry-run is the default.** Without `--apply` nothing is written to Jira — the batch
goes to a file and gets validated there. This is deliberate: the team creates the
whole backlog in one go with no pilot, so the file is the only place a format
mistake is cheap to fix.

## What it is not

Read this before anything else; most of it is a correction of a previous skill.

- **It does not assign anyone.** No assignee, no reviewer, no verifier. Assignment
  is human scheduling, unrelated to writing a spec, and guessing it fills the board
  with wrong owners.
- **It does not create Features.** It starts from one that exists on SOC-1…64.
- **It does not create or fill Verifications.** SOC-65…128 are out of scope by team
  decision. `references/verification.md` documents the standard for later.
- **It does not create Epics.** Those are a one-off pass; this skill reads them.
- **It does not invent acceptance criteria or thresholds.** If the Feature carries
  no measurable criterion, say so and stop.
- **It does not widen scope on refine.** Adding a bullet to `IN` means a new task.
- **It does not write to Jira without `--apply` and an explicit approval.**

## Mode: create

### 1. Resolve

`getAccessibleAtlassianResources` → `cloudId` for `omniloy.atlassian.net`. Reuse it
for every call. Project is `SOC`; issue type is `Tarea`. If a call rejects the type
name, confirm with `getJiraProjectIssueTypesMetadata` — do not guess a synonym.

Print the batch start timestamp now, before anything else:

```
Lote iniciado: 2026-07-31 09:14 — guarda esta marca, es el selector de rollback
```

There is no batch label (team decision), so this timestamp plus
`reporter = currentUser()` is the only way to select the batch afterwards. See
`references/convenciones.md`.

### 2. Read

- The Feature: `getJiraIssue` with `["summary","description","issuetype","status",
  "labels","parent","comment","issuelinks"]`. The description carries the user
  story, the quantitative acceptance criterion, and the ISO 14971 risks — all three
  feed the task.
- Its `Relates` links, one hop, lean fields.
- `examples/sofia-care/manifest.json` — find the issues whose `prd_refs[]` cite this
  `SOC-n`. Their `functional_md`, `technical_md`, `acceptance[]` and `estimate` are
  the technical thinking that has already been done. Use it; do not re-derive it.
- `references/monorepo.md` — the packages and their commands.

If the Feature's summary is one of the truncated ones (ends in `...`), do not
propagate it into the task summaries. Write a real title from the description.

### 3. Decompose

One task per package the work touches. The hard rule is **one task = one PR that
leaves the tree green**; the package is the default unit, not the law. A task may
span packages only when splitting it would break the build or leave dead code — the
usual case being a shared contract plus its producer and consumer — and then the
reason goes in `### Enfoque técnico` and the draft carries `"multi_package": true`.

Order dependencies as `contracts → producer → consumer` and declare them with
`Blocks`. Do not rely on creation order.

Fill `references/tarea.md` for each. Every section, no placeholders. Paths from the
layout, marked `[previsto]` while the package does not exist. Commands as `just`.

### 4. Validate

Write the batch to `feature-to-tasks-<timestamp>.json` and run:

```bash
python3 scripts/check_ready.py feature-to-tasks-<timestamp>.json
```

**If it reports a single error, fix the batch and re-run.** Do not create a partial
set and do not hand-wave a failure — the checker encodes the Definition of Ready
agreed in the plan, and at this volume one systematic mistake is 150 mistakes.

### 5. Approve

Show the user:

- The batch timestamp.
- A one-line-per-task table: summary, epic, requirement, `Blocks`.
- **A stratified sample expanded in full — one task per epic.** With no pilot, this
  is the human's real look at the format before ~150 tickets exist.
- The checker's summary line.

One `AskUserQuestion`: Approve / Approve with changes / Cancel. Nothing is created
without an explicit approve, and the batch is approved as a whole — never ticket by
ticket.

### 6. Create

Only with `--apply` and approval. Per task:

1. `createJiraIssue` — `projectKey: SOC`, `issueTypeName: Tarea`, `summary`,
   `description`, `parent: <EPIC-KEY>`, `labels`, `priority: Medium`. No assignee.
2. `createIssueLink` `Relates` to **every** `SOC-n` cited in `### Requisito`.
3. `createIssueLink` `Blocks` for declared dependencies, once both keys exist.

Be idempotent: before creating, search for an existing task with the same package
alias linked to the same `SOC-n`, and skip it if found. The anchor is the `SOC-n`
plus the package, never a `text ~` search.

Report the created keys and repeat the rollback JQL from
`references/convenciones.md`.

## Mode: refine

Read `references/refinado.md` first — the rules there are the point of this mode.

1. Fetch the issue with its comments.
2. Parse the nine sections. If it does not match the template, report that and stop;
   it may predate the standard and deserves a human look, not a silent reformat.
3. Rebuild the sections from what has been learned.
4. **Diff section by section, show only what changed.**
5. `AskUserQuestion`: Apply / Edit / Cancel.
6. On approval: `editJiraIssue` with the full new description, then
   `addCommentToJiraIssue` with a short note saying what moved and why.

Never step 6 without step 5. If the change would add a bullet to `IN`, refuse it and
offer to open a new task linked to the `SOC-n` that owns that scope.

## Mode: audit

Export the live tasks and run the checker over them:

```bash
python3 scripts/check_ready.py board.json --audit --existing-packages api,contracts
```

`--existing-packages` lists the aliases whose package now exists in the monorepo;
any `[previsto]` still sitting on those becomes an error. Also surfaces `just`
recipes that no longer exist, thresholds that drifted from the Feature, missing
`Relates`, and tasks with an assignee.

Report findings grouped by rule with the fix for each. Audit reports; it does not
edit — hand the list to `refine`.

## Guardrails

- **Templates are copied, not paraphrased.** Read `references/tarea.md` every run.
- **Paths and commands come from `references/monorepo.md`.** Never invent either.
- **`just` only in `### Comprobación`.** The toolchain is mixed TS/Python; the facade
  is what keeps 150 tickets stable.
- **Thresholds are copied literally** from the Feature. Paraphrasing one breaks the
  audit trail in a regulated product.
- **Dry-run by default; the whole batch or nothing.**
- **No assignee, ever.**
- **Print the batch timestamp before creating.** It is the only rollback selector.
- **Refine enriches, never widens.**
- If the Feature is too vague to produce verifiable criteria, stop and say so. A
  vague task multiplied by 150 is the failure this skill exists to prevent.
