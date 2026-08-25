---
name: quality-gate
description: Run the sofia-care static gate on the AFFECTED packages only - turbo lint+typecheck+test for TS workspaces, ruff+mypy+pytest per Python app, migration lint for db/. Use before committing, when asked to "run the checks", or as step one of a /ship / /epic-loop iteration.
user-invocable: true
---

# Quality gate (monorepo-aware)

El gate estático del monorepo. Corre **solo lo afectado** por el diff — nunca el
mundo entero.

## 1. Detectar lo afectado

```bash
git status --porcelain -- apps packages db tests evals
```

Clasifica: TS (`packages/*`, `apps/api`) · Python (`apps/transcriber`,
`apps/assistant`) · DB (`db/`) · contratos (`packages/contracts`).

## 2. Correr por ecosistema

```bash
# TypeScript — turbo filtra a los workspaces cambiados
pnpm exec turbo run lint typecheck test --filter='...[HEAD]'

# Python — por app tocada
cd apps/transcriber && uv run ruff format . && uv run ruff check . --fix \
  && uv run mypy src/ && uv run pytest -q -m "not slow"
cd apps/assistant   && uv run ruff format . && uv run ruff check . --fix \
  && uv run mypy src/ && uv run pytest -q -m "not slow"

# Contratos — si cambió un schema, codegen + tests de TODOS los consumidores
just contracts-codegen && pnpm exec turbo run test --filter='...@sofia-care/contracts'

# DB — si cambió una migración: aplicar desde cero + test RLS (ver /new-migration)
supabase db reset && cd db && uv run pytest tests/ -q -m "not slow"
```

Atajo raíz cuando exista: `just gate` (equivale a lo anterior).

## 3. Reportar y arreglar

- Agrupa fallos por herramienta con `file:line`. Arregla lint/format/type
  directamente; para tests, diagnostica la causa raíz antes de tocar aserciones.
- Re-ejecuta hasta verde. **No** commitees salvo petición explícita.
- Nota: editar `.py`/`.ts` ya dispara el auto-format del hook PostToolUse, así
  que el formato suele llegar limpio aquí.

## Convenciones que vigila

- Python: ruff (google docstrings, isort, `T201` no-print), mypy, markers
  estrictos (`slow`, `integration`, `medical`, `security`, `bias`).
- TS: eslint + prettier, `tsc --noEmit` limpio, sin `any` en superficies públicas.
- Cobertura objetivo ≥ 80 % en paquetes con lógica clínica.
