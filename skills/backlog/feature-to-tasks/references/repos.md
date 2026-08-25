# Origin repos → monorepo packages

This file is the only source for two sections of every task: `### Punto de partida`
(which repo the code comes from) and `### Destino` (where it lands). Nothing else in
the skill may invent a path or a command.

## The map

The product runs today as separate repos, all cloned under
`~/Workspace/Omniloy/Sofia/`. The monorepo is `Omniloy/sofia-care`, built in phases.

| Origin repo | Monorepo package | `area:` | Language |
| --- | --- | --- | --- |
| `sofia-api` | `apps/api/` | `area:api` | TS (NestJS) |
| `sofia-assistants` | `apps/assistant/` | `area:assistant` | Python |
| `sofia-transcriber` | `apps/transcriber/` | `area:transcriber` | Python |
| `sofia-sdk-core` | `packages/sdk/` | `area:sdk` | TS |
| `sofia-sdk-db` | `db/` | `area:db` | SQL + TS |
| `sofia-evaluator` | `evals/` | `area:evals` | Python |
| — (new) | `packages/contracts/` | `area:contracts` | TS |
| — (new) | `tests/`, `infra/` | `area:tests`, `area:infra` | mixed |

A row in this table is **not** a promise that the move is a `Porte`. It says where
the origin code lives, nothing more:

* `sofia-assistants` → `apps/assistant` is the standing `Reescritura`. The target is
  the deep agent decided in SOC-139, so the origin gives prompts, rules and clinical
  cases — not its structure.
* `packages/contracts`, `tests/` and `infra/` have no origin at all: `Nuevo`.
* The rest are ports unless a decision says otherwise, and that decision has to
  exist before a task claims it.

Note the language flip on `apps/transcriber`: the monorepo plan had it as TS, the
code that exists is Python. **The code wins.** A port that also rewrites the
language is not a port — if the rewrite is genuinely wanted, it is a `Reescritura`
and needs the decision to say so.

`packages/contracts` has no origin repo on purpose — it is the answer to the biggest
debt in the current product, the same wire shapes mirrored by hand across three
repos. A task that defines a shape belongs there, never in a consumer.

## Where things actually are

Verified against the clones. Use it as a starting point, then confirm with `ls`,
`Glob` and `Grep` — these trees move.

```
sofia-api/src/app/modules/     auth · chat-settings · chat-sources · clinical-notes
                               codify · consent · customers · deepgram · encryption
                               extraction · feedback · lang-graph · langsmith
                               profiles · rag · templates · transcriptions
                               transcriber-url · app-events · app-logs

sofia-assistants/src/          agents/{scribe,coding,extraction,transcription}
                               assistants/{registry.py,definitions.py,main,dev,stg}
                               evaluation/bias · shared · types
                               prompts/

sofia-transcriber/             server.py · client.py · connection_manager.py
                               transcript_processor.py · openai_client.py
                               azure_storage_client.py · extraction_client.py
                               auth_client.py · vad_coverage_client.py
                               providers/ · utils/

sofia-sdk-core/core/src/       modules/{chat,header,layout,settings,
                               insertionPreview,transcription}
                               web-components/ · shared/ · config/

sofia-sdk-db/supabase/         migrations/ · functions/{evals-assistants,
                               sync-langsmith,sync-metrics}

sofia-evaluator/               app/{api,tasks} · src/ · tests/
```

An origin path that was not seen in the clone does not go into a ticket. A path
that looks right and is not sends the implementer to the wrong file, which is worse
than saying "no lo he localizado".

## Summary prefix

The package **alias** — the last path segment, no `apps/`, no `packages/`, no npm
scope:

```
api  assistant  transcriber  sdk  contracts  db  evals  tests  infra
```

So `assistant: chat clínico y resumen del paciente`, never `apps/assistant: …`.

For `packages/sdk` the sub-package goes in the body, not the prefix: `sdk: render
del resumen` with `packages/sdk/core/...` in `### Destino`.

## No phase label

Tasks carry the requirement ids and one `area:`. Nothing else.

The milestone already lives inside the requirement id — `RF-M1-001` *is* M1 — so a
`phase-` label duplicates it by hand, and a hand-copy on 150 tickets drifts the first
time something moves between milestones. What order things get built in comes from
`Blocks`, which is checkable and is where a dependency actually belongs.

## Command contract — `just` only

The toolchain is mixed: pnpm + Turborepo for TypeScript, uv workspace for Python.
`apps/assistant`, `apps/transcriber` and `evals/` are Python; the rest is TS.

Tickets never write `pnpm`, `uv`, `turbo` or `pytest` directly. They write `just`:

```
just build <alias>      just test <alias>
just lint <alias>       just typecheck <alias>
```

Plus package-specific recipes when the task calls for them:

```bash
just db:migrate
just eval:gold summary-grounding
```

Two reasons this is not bikeshedding. A ticket author would otherwise have to know
each package's language, and getting it wrong makes the AI run `pnpm test` in a uv
workspace and report a false failure. And the commands are embedded in every ticket
on the board: behind `just`, swapping the toolchain invalidates the `justfile`
instead of the backlog.

The price is that the `justfile` must cover all four verbs for every package. That
is the `infra` foundation task. Until it lands, the commands in tickets are
readable and plannable but not runnable — which is fine, because no port starts
before the scaffold either.

If a task needs a command with no `just` recipe, that is a finding: either the
recipe joins the scaffold's scope, or the task is reaching outside the contract.

## Path markers

`### Destino` uses real monorepo paths. While the package does not exist, each one
carries `[previsto]`:

```
* `apps/assistant/src/subagents/summary/` [previsto] — subagente nuevo
```

The marker means *"this is where it goes according to the agreed layout, not
verified against a tree"*. `refinar` removes it once the path is real.

Use `[nuevo]` for the different case: code that has no origin because the capability
does not exist yet. `[previsto]` is about the destination not existing; `[nuevo]` is
about the origin not existing.

## Dependency order

For `Blocks` links:

```
contracts  →  db  →  api · assistant · transcriber  →  sdk
```

A task that defines a shape in `contracts` blocks its readers. A task that ports a
schema blocks the services that query it. Declare it; do not rely on creation order.
