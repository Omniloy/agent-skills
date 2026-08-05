---
name: feature-to-tasks
description: >-
  Creates and maintains the technical tasks of the Jira SOC board (SofIA Care).
  The product already exists as unofficial code in the sofia-* repos; these tasks
  move it into the audited monorepo, one task per package per Feature — typically
  three to five, never thirty. Each task carries Requisito, Punto de partida (Porte,
  Reescritura or Nuevo, with the real repo and paths behind it), Alcance IN/OUT,
  Destino, Criterios de aceptación, Comprobación and Riesgos ISO 14971. Mode crear decomposes a Feature.
  Mode refinar brings an existing ticket up to date with what has been learned,
  showing a diff and leaving a comment. It never assigns anyone, never creates
  Features, never fills Verifications, and never widens the scope of a live ticket.
  Use it for "crear las tareas técnicas de SOC-n", "descomponer esta feature",
  "actualizar el ticket con lo que hemos aprendido".
allowed-tools: Read, Grep, Glob, Bash, AskUserQuestion, mcp__claude_ai_Atlassian__getAccessibleAtlassianResources, mcp__claude_ai_Atlassian__getJiraIssue, mcp__claude_ai_Atlassian__searchJiraIssuesUsingJql, mcp__claude_ai_Atlassian__getJiraProjectIssueTypesMetadata, mcp__claude_ai_Atlassian__createJiraIssue, mcp__claude_ai_Atlassian__editJiraIssue, mcp__claude_ai_Atlassian__addCommentToJiraIssue, mcp__claude_ai_Atlassian__createIssueLink
user-invocable: true
---

# feature-to-tasks

## The one thing to understand first

**The product is already built.** It runs today as `sofia-api`, `sofia-assistants`,
`sofia-transcriber`, `sofia-sdk-core` and `sofia-sdk-db`. What SOC is doing is
moving that code into an audited monorepo, in phases, until everything we have now
exists there under class II control.

So a technical task is **not a design exercise**. It starts from something that
already runs. Nobody re-invents the resumen from a blank page.

**But not everything is a move.** Every task declares which of three kinds it is, on
the first line of `### Punto de partida`:

| Kind | When | What the origin gives you |
| --- | --- | --- |
| `Porte` | The architecture stays and the code moves | The code itself, adapted to the monorepo rules |
| `Reescritura` | The capability exists but the target architecture is deliberately different — `apps/assistant` as a deep agent is the standing case | The behaviour: prompts, rules, fixtures, clinical cases. Not the structure |
| `Nuevo` | Nothing behind it — `packages/contracts`, the streaming channel, client-side anonymisation | Nothing. Say so plainly |

Getting the kind wrong is the expensive mistake, because the acceptance criteria
differ: a `Porte` is proven by the origin's own tests passing unchanged, and asking
that of a `Reescritura` sets a bar the task cannot clear by design.

A `Reescritura` needs a decision behind it that someone already took — an ADR, a
Feature, a decision recorded in the foundation task. If there is none, stop and ask.
That is the line between "the architecture changed on purpose" and this skill
inventing one.

If a run starts producing architecture decisions nobody took, invented contracts, or
thirty slices of a Feature, it has drifted.

| File | What it holds |
| --- | --- |
| `references/plantilla.md` | The seven-section template, per-section rules, the worked SOC-1 example |
| `references/repos.md` | Origin repos → monorepo packages, the `just` contract, labels |
| `references/verification.md` | The Verification standard — tasks link to them, this skill never fills them |

Read `plantilla.md` and `repos.md` on every run. Do not write a ticket from memory.

## Input

```
/feature-to-tasks SOC-1              # one Feature → its technical tasks
/feature-to-tasks refinar SOC-142    # bring one live ticket up to date
```

Nothing is written to Jira without an explicit approval on screen. There is no
dry-run file and no format checker: at three to five tasks per Feature the human
reads them whole, which catches more than a linter ever did.

## What it does not do

- **It does not assign anyone.** Assignment is human scheduling.
- **It does not create Features or Epics.** It starts from a Feature that exists on
  SOC-1…64.
- **It does not create or fill Verifications.** SOC-65…128 exist; a task *links* to
  the one that will prove it. Filling them is another pass — `verification.md`.
- **It does not invent thresholds.** They are copied from the Feature, literally.
- **It does not decide an architecture.** A `Reescritura` cites the ADR or Feature
  that decided it; a `Nuevo` says plainly that there is nothing behind it. Neither is
  permission to design something elaborate on the spot.
- **It does not widen a live ticket.** Adding a bullet to `IN` means a new task.

## The foundation tasks

Before anything can be ported there has to be somewhere to port it to: the monorepo
scaffold and its `justfile`, CI, the shared contracts, the database with its tenancy
and retention, auth and SDK versioning.

That work is real but it hangs off no capability Feature, so this skill does not
generate it. It follows the same rule anyway — **one task per package** — and it
should be as few tickets as it can possibly be:

```
infra:     monorepo, tooling y CI
contracts: contratos del producto como fuente única
db:        tenancy, dominio clínico, aislamiento y retención
api:       auth, rotación de credenciales y versionado de SDK
```

Four, not ten. Splitting the foundation by sub-topic is the same mistake as splitting
a Feature by behaviour: it multiplies tickets that will be built in one sitting by one
person. Decisions that the existing code already settled — the database platform, the
agent runtime — are recorded inside the task that carries them, not as separate ADR
tickets; an ADR is a document, and one ticket per document is how a board fills with
things nobody closes.

Every port task `Blocks` on these. That dependency, not a label, is what says the
foundation comes first.

## Mode: crear

### 1. Read the Feature and the code

`getAccessibleAtlassianResources` → `cloudId` for `omniloy.atlassian.net`. Project
`SOC`, issue type `Tarea`.

Fetch the Feature with `["summary","description","labels","status","issuelinks",
"comment"]`. Its description carries the user story, the measurable criterion and
the ISO 14971 risks — the criterion and the risks go into the tasks verbatim.

Then **find the code that already does this**, in the clones under
`~/Workspace/Omniloy/Sofia/`. Use `ls`, `Glob` and `Grep` against the real trees.
Paths written into a ticket must have been seen; a plausible-looking path that does
not exist is worse than no path, because it sends the implementer to the wrong file.

If the Feature's summary is one of the truncated ones (ends in `...`), do not
propagate it. Write a real title from the description.

### 2. Decompose by package, not by behaviour

**One task per monorepo package the Feature touches.** Typically three to five:
`db`, `api`, `assistant`, `sdk`, sometimes `transcriber` or `contracts`.

The unit is *the whole of that Feature's capability inside that package*, not a
slice of it. For SOC-1 that means one `assistant` task covering chat, resumen and
the logic around them — not one task per logic. A task may take several PRs; that
is fine and is a deliberate reversal of the old one-task-one-PR rule, which is what
produced thirty tickets per Feature.

**If more than six tasks come out, the decomposition is wrong.** Go back.

**Check what a previous Feature already brought across.** The second Feature that
touches `api` does not re-port the module the first one already moved. Search the
board for an existing task on the same package citing the same origin module; if it
exists, the new task declares `Blocks` on it and its `Punto de partida` lists only
what is missing. This is the rule that keeps 64 Features from becoming 250 tasks.

Order dependencies `contracts → db → api · assistant · transcriber → sdk` and
declare them with `Blocks`. Do not rely on creation order.

### 3. Write them

Fill `references/plantilla.md` for each. Every section, no placeholders. Origin
paths verified against the clone; destination paths from `references/repos.md`,
marked `[previsto]` while the monorepo package does not exist.

### 4. Approve and create

Show the user every task in full — at this volume, entire and on screen. Then one
`AskUserQuestion`: Crear / Corregir / Cancelar. The batch is approved as a whole.

Print the creation timestamp before writing anything:

```
Lote iniciado: 2026-08-05 09:14 — es el selector de rollback
```

Per task: `createJiraIssue` with `projectKey: SOC`, `issueTypeName: Tarea`,
`summary`, `description`, `labels`, `priority: Medium`, **no assignee**. Then
`createIssueLink` `Relates` to every `SOC-n` in `### Requisito`, `Relates` to the
Verification, and `Blocks` for declared dependencies once both keys exist.

Before creating, check for an existing task on the same package linked to the same
`SOC-n` and skip it. The anchor is the `SOC-n` plus the package, never a `text ~`
search.

## Mode: refinar

A ticket written before the monorepo package exists will not be its own final
version. It gets corrected when the port reveals something, when a decision moves,
when a path becomes real.

1. Fetch the issue with its comments.
2. Parse the seven sections. If it does not match the template, report that and
   stop — it may predate the standard and deserves a human look, not a silent
   reformat.
3. Rebuild the sections as **the current agreed state, not a log.** Never append
   `Update 12/03`. Never add a `### Historial` section.
4. Diff section by section, show only what changed.
5. `AskUserQuestion`: Aplicar / Corregir / Cancelar.
6. On approval: `editJiraIssue` with the full new description, then
   `addCommentToJiraIssue` with a short note saying what moved and why. The comment
   is the history — it carries author and timestamp for free.

Never step 6 without step 5.

### The guardrail: correct detail, never widen scope

If the change adds a bullet to `IN`, it is not an update — it is a new task.
Promoting an `OUT` bullet into `IN` is the same violation wearing a hat, because
every `OUT` bullet names the `SOC-n` that owns it.

Loosening a threshold to match what got built is the damaging one: the threshold
came from the Feature, the Feature from the requirement, and the requirement is
what gets audited. If the port cannot meet it, that is a finding for product, not
an edit to the ticket.

Say so and offer the alternative:

> Esto amplía el `IN` de SOC-142. No lo meto ahí — corresponde a SOC-159. ¿Creo la
> tarea nueva enlazada?

## Board conventions

**Summary** — `<alias>: <qué se construye>`, Spanish, lowercase after the colon, no
trailing period, max ~70 chars, never ends in `...`, no `SOC-n` inside.

**Labels** — the Feature's requirement ids (`RF-M1-001`, `RNF-DAT-001`) and one
`area:<alias>`. Nothing else.

No `phase-` label. The milestone is already inside the requirement id — `RF-M1-001`
*is* M1 — so a phase label is a hand-copy of something the board already knows, and
hand-copies drift. Ordering comes from `Blocks` links, which are checkable; a
scheduling axis nobody maintains is worse than none.

**Links** — `Relates` to every Feature cited in `### Requisito`, `Relates` to the
Verification that will prove it, `Blocks` for dependencies. No `parent`: the Epics
do not exist on this board, and the Feature is never the parent — that relation is
`Relates`.

**Rollback** — there is no batch label, so the selector is the creation window:

```
project = SOC AND issuetype = Tarea
  AND created >= "YYYY-MM-DD HH:mm" AND reporter = currentUser()
```

**Language** — Spanish in the tickets, matching the 64 Features. Paths, commands and
contracts inside fenced blocks; Jira mangles them in running text. These reference
files are English because the repo is.

## Guardrails

- **The code exists. Find it before writing the ticket.** An origin path that was
  not seen in the clone does not go in.
- **One task per package, three to five per Feature.** More than six means the
  decomposition is wrong.
- **Do not re-port what a previous task already brought.** Search first, then
  `Blocks` on it.
- **Thresholds copied literally** from the Feature. Paraphrasing one breaks the
  audit trail in a regulated product.
- **The E2E proof is not in the task.** It belongs to the Verification the task
  links to. The task's criteria are the Feature's thresholds plus parity with what
  the origin repo does today.
- **`just` only in `### Comprobación`.** The toolchain is mixed TS/Python.
- **No assignee, ever.** Not the requester, not a reviewer, not a verifier.
- **Refinar corrects, never widens.**
