# The `sofia-care` monorepo — packages and command contract

This file is the **only** source for two sections of every technical task:
`### Puntos de entrada` (paths) and `### Comprobación` (commands). Nothing else
in the skill may invent a path or a command.

Source of truth for the layout: `examples/sofia-care/plan.html` (the approved
`prd-to-issues` plan for `Omniloy/sofia-care`). If that plan changes, change this
file, then run `check_ready.py --audit` over the board.

> **Status: the repo does not exist yet.** Until `E0 · Fundación del monorepo`
> is merged, every path written into a ticket carries the `[previsto]` marker.
> See "Path markers" below.

## Packages

| Package root | `area:` label | What lives there | Language |
| --- | --- | --- | --- |
| `apps/api/` | `area:api` | Multi-tenant gateway. REST `/v1` + JWT/SSO. | TS |
| `apps/transcriber/` | `area:transcriber` | STT realtime + batch, speaker roles, VAD. | TS |
| `apps/assistant/` | `area:assistant` | Single deep agent (resumen · nota · guías · CIE-10). | **Python** |
| `packages/contracts/` | `area:contracts` | HIS context, WS, extraction, roles, auth. The shared wire contracts. | TS |
| `packages/sdk/` | `area:sdk` | Embedded widget. Sub-packages `{core,react,webcomponent}`. | TS |
| `db/` | `area:db` | New database: tenancy, templates, audit, retention. | SQL + TS |
| `tests/` | `area:tests` | Cross-package integration tests. | mixed |
| `evals/` | `area:evals` | Gold-standard suites, bias bench, release gates. | Python |
| `infra/` | `area:infra` | Dev compose, CI with per-package change detection, SBOM. | — |

`packages/contracts` is deliberately first-class: the biggest debt in the current
product is the same contracts duplicated field-by-field across three repos. A task
that redefines a wire shape belongs in `contracts`, never in its consumer.

## Summary prefix

The `summary` prefix is the package **alias** — the last path segment, without
`apps/`, `packages/`, or any npm scope:

```
api  transcriber  assistant  contracts  sdk  db  tests  evals  infra
```

So: `api: endpoint de resumen del paciente` — not `apps/api: …`, not `@sofia/api: …`.

For `packages/sdk`, the sub-package goes in the body, not the prefix:
`sdk: render del resumen por secciones` with `packages/sdk/core/...` in the paths.

## Command contract — `just` only

The toolchain is **mixed**: pnpm + Turborepo for TypeScript, uv workspace for
Python. `apps/assistant` and `evals/` are Python; everything else is TS.

Tickets never write `pnpm`, `uv`, `turbo`, or `pytest` directly. They write `just`,
which is the agreed facade over both:

```
just build <alias>
just test <alias>
just lint <alias>
just typecheck <alias>
```

Plus the whole-tree forms, for tasks that genuinely span packages:

```
just build      just test      just lint      just typecheck
```

Two reasons this is not bikeshedding:

1. **A ticket author would otherwise have to know each package's language.** Get it
   wrong and the AI runs `pnpm test` in a uv workspace and reports a false failure.
2. **~150 tickets embed these commands.** Swapping Turborepo for something else
   later would invalidate every one of them. Behind `just`, it invalidates the
   `justfile`.

The price: the `justfile` must exist and cover all four verbs for every package.
That is `E0.1 · Scaffold del monorepo y ADR de tooling`. **Until it lands, the
commands in tickets are readable and plannable but not runnable** — which is fine,
because no implementation starts before E0 either.

A ticket may add package-specific extras when the task calls for it, as long as
they are also `just` recipes:

```bash
just eval:gold summary-grounding      # evals/
just db:migrate                       # db/
```

If a task needs a command that has no `just` recipe yet, that is a finding: either
add the recipe to E0.1's scope, or the task is reaching outside the contract.

## Path markers

`### Puntos de entrada` uses real paths from the table above. While the tree does
not exist, each path carries `[previsto]`:

```
* `apps/api/src/routes/summary.ts` [previsto] — endpoint nuevo
* Patrón a seguir: `apps/api/src/routes/guidelines.ts` [previsto]
```

The marker means *"this is where it goes according to the agreed layout, not
verified against a tree"*. Once `E0` is merged, `check_ready.py --audit` flags every
remaining `[previsto]` whose package now exists, and `feature-to-tasks refine`
removes the marker after validating the path.

A path whose root is not in the table above is always an error, marked or not.

## Dependency order

Within the monorepo the ordering that matters for `Blocks` links is:

```
contracts  →  producer (api · assistant · transcriber · db)  →  consumer (sdk)
```

A task that changes a shape in `contracts` blocks the tasks that read it. Declare
it; do not rely on creation order.
