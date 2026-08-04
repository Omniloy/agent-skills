# Hooks

Registrados en `../settings.json`. Se cargan al arrancar la sesión (tras editar,
reinicia o verifica con `/hooks`).

| Hook | Evento | Qué hace |
|------|--------|----------|
| `guard.py` | PreToolUse (todo) | **Opcional — no registrado por ahora** (fase inicial sin secretos: settings.json permite todo). Cuando haya claves reales o PHI: registrar el bloque PreToolUse en settings.json y repoblar `ask`/`deny`. 3 niveles: **deny** secretos + PHI fixtures + push a main · **ask** red externa / git mutante / destructivos · **allow** el loop local. |
| `format_fix.sh` | PostToolUse (Edit/Write) | Auto-format por lenguaje: ruff (`.py`), prettier+eslint (`.ts/.tsx/.js`), prettier (`.json/.css/.md`). No bloquea. |
| `contracts_codegen.sh` | PostToolUse (Edit/Write) | Si el fichero editado es un schema de `packages/contracts/`, regenera los tipos TS+Pydantic; si no puede, avisa en rojo de que quedaron stale. |
| `require_checks.sh` | Stop | El gate de cambio: checks estáticos de los paquetes **afectados** (turbo filtered / por app uv) → bloquea si fallan; si pasan, exige `/verify-change` + `change-reviewer` una vez y espera `mark_verified.sh`. |
| `mark_verified.sh` | (manual) | Registra el hash del estado verificado en `.claude/.verify_state` para desbloquear el cierre. `skip "razón"` para docs-only. |

## Loop-safety

- El sentinel por **hash de estado** hace idempotente el gate: el mismo estado
  verificado nunca se vuelve a bloquear — imprescindible para `/loop`, `/ship` y
  `/epic-loop`, cuyos wakeups cierran turnos constantemente.
- `/ship` y `/epic-loop` ya llevan su gate de PR (`/review-pr`): su iteración debe
  terminar con `bash .claude/hooks/mark_verified.sh` tras pasar quality-gate +
  verify + review, y el Stop hook no añade fricción extra.
- Todos los hooks **fallan en abierto** (exit 0) ante errores de parseo: nunca
  dejan la sesión colgada.
