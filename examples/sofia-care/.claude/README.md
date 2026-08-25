# Sofia Care — Claude Code harness (team-visible)

Harness de Claude Code para el monorepo **Omniloy/sofia-care**. A diferencia de los
harness locales de los repos actuales (sofia-assistants, sofia-transcriber), este es
**versionado y compartido por el equipo**: se copia tal cual a la raíz del monorepo
(`sofia-care/.claude/`) cuando exista. Cada dev puede afinar permisos con su
`settings.local.json` (git-ignorado).

> Los comandos asumen las convenciones del scaffold E0.1 del plan (`just`, pnpm+turbo
> para TS, uv workspace para Python, Supabase CLI para `db/`). Ajusta los nombres de
> recetas cuando el scaffold real exista.

## La idea: programar con loops

El motor de delivery son las skills de [Omniloy/agent-skills](https://github.com/Omniloy/agent-skills)
(instaladas flat en `~/.claude/skills/` — ver su README). Este harness aporta lo
específico del monorepo y está diseñado para componerse con ellas:

```
manifest.json ──/prd-to-issues──► backlog GitHub (Milestone → Epic → sub-issues)
      │
      ├── por epic:   /loop /epic-loop        autónomo: PR por epic → /review-pr → merge
      ├── por tarea:  /loop /ship SOC-nn      atendido al inicio, desatendido después
      │                  ├─ subagentes de implementación: deepagent-dev / sdk-dev / transcriber-dev
      │                  └─ gates locales: quality-gate + verify-change + change-reviewer
      │                     gate de PR:    /review-pr  (Greptile 5/5 + CI verde + comentarios)
      └── verificación: /live-testing-plan + /agent-eval-api   (las Verifications SOC-65…128)
```

- **`/soc-task SOC-nn`** prepara la unidad de trabajo: lee la Feature del Jira, extrae
  sus criterios cuantitativos y riesgos ISO 14971, crea la **Tarea** técnica enlazada
  (`Relates`, nunca subtarea — delega en `create-jira-work-items`) y enlaza su
  **Verification** espejo. Después: `/loop /ship <tarea>`.
- **`/sdk-release`** corta release del SDK y abre el PR automatizado al repo de
  releases (solo builds — la publicación es siempre manual).

## Layout

| Path | Qué es |
|------|--------|
| `settings.json` | Permisos base del equipo + registro de hooks (versionado) |
| `settings.local.json` | Overrides personales (git-ignorado) |
| `hooks/` | guard PreToolUse · auto-format PostToolUse · codegen de contratos · gate de Stop — ver `hooks/README.md` |
| `agents/` | Subagentes: revisores + especialistas de dominio — ver `agents/README.md` |
| `skills/` | Skills del proyecto (`/`-commands) — ver `skills/README.md` |

## El gate de cambio (enforced)

Todo cambio que toca código pasa por el gate antes de poder terminar el turno
(`Stop` hook `hooks/require_checks.sh`):

1. **Gate estático** — lint + typecheck + tests **solo de los paquetes afectados**
   (turbo affected para TS; por-app para Python). Un fallo bloquea el cierre.
2. **Smoke** — `/verify-change`: ejecutar de verdad el flujo tocado.
3. **Review** — el agente `change-reviewer` sobre el diff (que delega la lente
   clínica en `medical-safety-reviewer` cuando el diff toca PHI/flujos clínicos).

Registrar con `bash .claude/hooks/mark_verified.sh` (o `… skip "<razón>"` para
cambios docs-only).

### Loop-friendliness

`/ship` y `/epic-loop` llevan su propio gate de PR (`/review-pr`). El Stop gate no
duplica esa fricción: cuando el flujo del loop ya pasó quality-gate + verify +
review, ejecuta `mark_verified.sh` como último paso de su iteración y el turno
cierra sin bloqueo. El sentinel por hash de estado evita bucles infinitos del hook.

## Seguridad

- **Fase inicial (actual): permisos abiertos.** `settings.json` permite todo y el
  guard PreToolUse (`hooks/guard.py`) **no está registrado** — no hay secretos ni
  datos reales en el entorno todavía.
- **Antes de que entren claves reales o PHI**: registrar el bloque PreToolUse de
  `guard.py` en `settings.json` y repoblar las listas `ask`/`deny` (secretos,
  `fixtures/phi/`, push a main, red externa, destructivos). Los tests usan SIEMPRE
  datos sintéticos.
- Para runs autónomos (bypass / auto-accept): `/isolated-run` — worktree descartable.
