# Conventions — naming, labels, hierarchy, links

Every rule here is mechanically checkable. `check_ready.py` enforces the ones it can.

## The board

Site `omniloy.atlassian.net`, project **SOC** (SofIA Care), board 269. Resolve the
`cloudId` at runtime with `getAccessibleAtlassianResources` — never hard-code it.

What is already there, and what is not:

| Type | Range | Count | Owned by this skill |
| --- | --- | --- | --- |
| `Feature` | SOC-1…64 | 64 | No — read only |
| `Verification` | SOC-65…128 | 64 | No — deferred, see `verification.md` |
| `Epic` | — | 0 | No — created once, by hand or a separate pass |
| `Tarea` | — | 0 | **Yes** |

## Summary

**`<alias>: <qué se construye>`** where `<alias>` is the package alias from
`monorepo.md` (`api`, `assistant`, `sdk`, `contracts`, `db`, `evals`, `infra`,
`transcriber`, `tests`).

```
api: endpoint de resumen del paciente
assistant: subagente de resumen con verificación de fundamentación
contracts: contrato de contexto de paciente
```

Rules:

- Max ~70 characters. Long enough to be unambiguous, short enough to read on a board.
- **Never ends in `...`.** The 64 Features were bulk-loaded with truncated summaries
  (`«Generar un resumen del paciente a partir de los datos demográficos y clínicos...»`).
  Do not inherit that. If the requirement text is long, write a real short title.
- Spanish, lowercase after the colon, no trailing period.
- No `SOC-n` in the summary — that is what the link and `### Requisito` are for.

## Labels

| Label | Value | Source |
| --- | --- | --- |
| Requirement id | `RF-M1-001`, `RNF-IA-002`, … | The Feature's own label. Copy it. |
| Area | `area:<alias>` | The package. One per task. |
| Phase | `phase-0` … `phase-4` | The manifest milestone of the originating issue. |

One requirement label per Feature served — a task citing two Features carries two.

No batch label: the team decided against it. Reversibility comes from the creation
timestamp instead — see "Rollback" below.

## Hierarchy and links

```
Epic  E0…E11                    ← parent of the technical tasks
  └── Tarea                     ← parent = the Epic
        ├── Relates → Feature   ← the requirement it serves (one per SOC-n cited)
        └── Blocks  → Tarea     ← declared dependencies
```

Three things that are easy to get wrong:

- **The Epic is the `parent`. The Feature is not.** Features are a requirements
  catalogue on their own axis; Epics organise execution. Linking a task to its
  Feature is `Relates`, never `parent`.
- **`Relates` for every `SOC-n` in `### Requisito`.** If the section cites two
  Features, create two links. The body text is a convenience; the link is the
  structure.
- **`Blocks` runs contracts → producer → consumer.** A task that changes a shape in
  `packages/contracts` blocks its readers. Declare it explicitly; do not rely on
  creation order.

Do not create Jira sub-tasks. `Tarea` sits at the same hierarchy level as `Feature`
and `Verification` (level 0); `Epic` is level 1.

## Fields set on creation

| Field | Value |
| --- | --- |
| `projectKey` | `SOC` |
| `issueTypeName` | `Tarea` |
| `parent` | the Epic key |
| `summary` | see above |
| `description` | the filled `tarea.md` template |
| `labels` | requirement id(s) + `area:` + `phase-` |
| `priority` | `Medium` unless the Feature says otherwise |

**No `assignee`.** Not the requester, not a reviewer, not a verifier. Assignment is
a human scheduling decision that has nothing to do with writing a spec, and guessing
it produces a board full of wrong owners. This is deliberate and is not an oversight.

## Language

Spanish, matching the 64 Features. Paths, commands, and contracts go in fenced code
blocks — Jira mangles them in running text, and the AI needs them literal.

`SKILL.md` and these reference files are in English because the repo is.

## Rollback

There is no batch label, so the selector for "everything this run created" is the
creation window:

```
project = SOC AND issuetype = Tarea
  AND created >= "YYYY-MM-DD HH:mm"
  AND reporter = currentUser()
```

**The skill prints that timestamp before creating anything.** Record it. Without it,
selecting the batch afterwards is guesswork, and correcting a format mistake across
~150 tickets becomes manual.

The window is precise as long as nobody else creates a `Tarea` in SOC during the run.
