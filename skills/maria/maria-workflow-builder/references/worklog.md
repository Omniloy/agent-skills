# The customer worklog

One file per customer flow, `maria-voice/docs/wip/<customer>_<topic>/WORKLOG.md`, kept from
the first day. It is the story of the flow: what changed, why, what proved it, and what was
learned. It is written in the language the team works in.

The model is San Roque's (`docs/wip/san_roque_piloto_fixes/WORKLOG.md`): three weeks, 50+
versions and five pilots could be reconstructed from it. What made it useful is that every
entry says **why**, with the evidence (a call id, a test, a measurement), not just what.

## When to write

Write as you go, in the same turn as the change. Never reconstruct it at the end.
- **Each flow version** (clone or production) gets a line under its phase, written when the
  version is saved. The worklog line and the version's `change_description` say the same
  thing: write it once, paste it twice.
- **Each promotion, activation and deactivation**, with the UTC instant.
- **Each pilot, eval campaign or monitoring pass that changed something**: the numbers, the
  findings by call id, and what they became (a version, a test, a task, a KEDB entry).
- **Each backend fact that surprised you**: a filter hiding data, a sync lag, a type the API
  refuses. It is also written into `BACKEND_CONTRACT.md`.
- **Each thing that was tried and did not work, and why.** These are the lines that save the
  next person a day.

## Structure

```markdown
# Worklog · <Customer> <flow>, <start> → <current state>

<flow ids and api_key, the clone, the environments; where the sources are>

| qué | estado |          ← current state, kept up to date at the top
|---|---|
| prod <flow> | vN, workflow ACTIVE since <Z> / deactivated since <Z> |
| clone <id> | vM |
| promotion SQL | <file>, validated / applied |
| tests | PR #…, last commit |

## Cronología en una tabla
| fecha | prod | clon | hito |

## Fase N · <what the phase was about> (<dates>, <JIRA>) → <versions>

**vK:** what changed, and why, with the call/test/measurement that motivated it.
**Piloto N (<version>, <window>, <n> llamadas).** Results, then findings by call id.

> Lecciones de la fase:
> - the generalisable lesson, in one line.

## Cambios de código (maria-voice) que el flow necesitó
| commit | qué |

## Clases de defecto que se repitieron
1. **<class>** (vX, vY). What it looks like and what fixes it.

## Cómo se trabajó (y conviene repetir)
```

Rules:
- **Group versions into phases** by what drove them (a pilot, a customer request, a
  redesign), not by date alone. A phase ends with its lessons.
- **Name the evidence.** Call ids (`d7085960`), test names, execution ids, measured numbers,
  with the method. "It seemed better" is not an entry.
- **Say what was wrong before saying what was done**, especially when a fix undoes an
  earlier fix. Regressions of your own go in too, marked as such.
- **Keep the state table and the chronology current.** Someone opening the file cold must
  know in ten seconds what production is running and what is pending.
- **No PII.** Calls by id, documents by shape (`########L`), never a phone, a name or a
  transcript line that identifies someone. The worklog is excluded from git today (`docs/wip`),
  but it gets pasted into reports, Jira and PRs.
- **Promote the lessons.** When a "defect class" repeats in a second customer, move it to this
  skill (`authoring.md`, `defect-catalog.md`) and say so in the worklog. When it is a monitor
  pattern, propose a code (`maria-prod-monitor`). The worklog is where lessons are born; the
  skills are where they live.
