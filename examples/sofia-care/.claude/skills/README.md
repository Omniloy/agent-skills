# Skills del proyecto

Skills específicas del monorepo. Se componen con las de
[Omniloy/agent-skills](https://github.com/Omniloy/agent-skills) (instaladas en
`~/.claude/skills/`), que ponen el motor de loops.

## Las de aquí

| Skill | Qué hace | Cuándo |
|-------|----------|--------|
| `/quality-gate` | Gate estático de los paquetes **afectados** (turbo filtered / uv por app / db) | Antes de commitear; paso 1 de toda iteración |
| `/verify-change` | Smoke real del flujo tocado (servicio o compose+mocks) → `mark_verified` | Tras quality-gate; lo exige el Stop gate |
| `/run-stack` | Stack completo local con mocks y seeds (`just dev`) | Correr la app, demos, debug cross-servicio |
| `/add-contract` | Cambio contract-first: schema → codegen → tests de los 4 consumidores → compat | Cualquier payload que cruza fronteras |
| `/new-migration` | Migración Supabase idempotente + apply-from-scratch + test RLS obligatorio | Cualquier cambio en `db/` |
| `/soc-task` | Feature SOC → Tarea técnica enlazada (vía `create-jira-work-items`) → `/ship` | Arrancar trabajo de una Feature del Jira |
| `/sdk-release` | Release del SDK: builds → PR automatizado al repo de releases (0 fuente) → publicación manual | Cortar release del widget |
| `/isolated-run` | Worktree descartable para runs autónomos / bypass | Antes de `/loop /epic-loop` desatendido |

## Las de agent-skills (el motor)

| Skill | Papel aquí |
|-------|-----------|
| `/ship` (+ `/loop`) | Conductor por tarea: plan aprobado → implementación desatendida → un PR → gate a verde → recap |
| `/epic-loop` (+ `/loop`) | Construir el backlog epic a epic, autónomo, manteniendo el backlog veraz |
| `/review-pr` | Gate de PR: Greptile 5/5 + CI verde + comentarios resueltos |
| `/prd-to-issues` | Crear el backlog GitHub desde `manifest.json` |
| `/create-jira-work-items` | El trío Feature+Tarea+Verificación (`Relates`) — `/soc-task` delega aquí |
| `/visual-recap` | Recap interactivo de lo shipped |
| `/release-title-changelog` | Changelog del PR de distribución — `/sdk-release` lo reutiliza |
| `/live-testing-plan` + `/agent-eval-api` | Ejecutar las Verifications SOC-65…128 |

## El ciclo completo

```
/soc-task SOC-nn                     prepara la Tarea (aprobación humana para crear en Jira)
   └─ /loop /ship <tarea>            implementa: especialista → quality-gate → verify-change
                                     → change-reviewer (→ medical-safety-reviewer)
                                     → mark_verified → PR → /review-pr → recap
/loop /epic-loop                     o el modo autónomo por epics (con /isolated-run si es bypass)
/live-testing-plan SOC-(nn+64)       ejecuta la Verification espejo
```
