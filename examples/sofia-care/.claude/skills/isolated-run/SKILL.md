---
name: isolated-run
description: Run autonomous / bypass-permissions work in a disposable git worktree so a bad rm/reset can't touch the real checkout. Use before long unattended runs (/loop /epic-loop overnight, bulk refactors) or whenever running with elevated auto-accept.
user-invocable: true
---

# Isolated run (worktree descartable)

Para runs desatendidos (`/loop /epic-loop` toda la noche, refactors masivos) el
guard + permisos protegen los secretos, pero el blast-radius del working tree
se controla trabajando en un **worktree descartable**.

## Preparar

```bash
git worktree add ../sofia-care-run-$(date +%m%d) -b run/$(date +%m%d-%H%M)
cd ../sofia-care-run-*
pnpm install && uv sync        # deps del worktree
```

O, dentro de una sesión de Claude Code, usa directamente `EnterWorktree` (el
tooling nativo de worktrees) — mismo efecto, gestionado por el harness.

## Reglas del run aislado

1. El run trabaja SOLO en el worktree; el checkout real no se toca.
2. Los gates siguen activos (hooks se cargan igual): quality-gate + verify +
   review + `mark_verified` por iteración.
3. El resultado sale por **PR** (rama del worktree → PR → `/review-pr`), nunca
   por merge local a main.
4. Nada de claves de producción: el stack local del worktree usa los mocks
   (`/run-stack`).

## Recoger y limpiar

```bash
# desde el checkout real, al terminar:
git worktree list
git worktree remove ../sofia-care-run-*        # si la rama ya está en PR/mergeada
git branch -D run/<...>                        # solo si se abandonó
```

Un worktree con cambios sin PR no se borra: revisa qué hay antes (`git -C
../sofia-care-run-* status`).
