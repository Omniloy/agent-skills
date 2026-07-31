# The Verification standard — defined, deferred

> **The team decided not to create or fill Verifications yet.** This file is the
> agreed standard for when that work starts. `feature-to-tasks` does not touch
> `Verification` issues in any mode.

It is written down now for one reason: technical tasks should be authored knowing
what evidence will eventually be demanded of them. A task whose acceptance criteria
cannot produce that evidence is a task that will need rewriting later.

## What already exists

SOC-65…128 — 64 Verifications, one mirroring each Feature, all in `Discovery`. They
were bulk-loaded from a requirements document and share their Feature's summary
verbatim. Their structure is right; their content is placeholder. A representative
step reads:

```
2. Ejecutar la acción asociada al requisito RNF-AUD-002.
```

## The six existing sections stay

`### Objetivo` · `### Precondiciones` · `### Pasos de prueba` · `### Resultado
esperado` · `### Criterio de aprobación` · `### Método de verificación`

They are already consistent across all 64. Do not churn them.

## Two additions and one fix

- **`### Requisito` at the top.** Today traceability lives only in `labels`, invisible
  when reading the ticket. Same format as a technical task: `SOC-1 (RF-M1-001)`.
- **`### Evidencia` at the end.** Where the proof ends up. A verification with no
  recorded evidence is worthless in the technical file — the auditor asks for the
  artifact, not the ticket.
- **`### Método de verificación` moves to second place** and takes a closed vocabulary.
  It determines how every other section reads, so it belongs near the top.

## Method vocabulary

| Method | When | Who runs it | Evidence it leaves |
| --- | --- | --- | --- |
| `Test automatizado` | Deterministic behaviour checkable in CI | CI | CI run + commit sha |
| `Eval (gold standard)` | Requirement with a statistical threshold over a sample | Evals platform | `run_id` + JSON report + model version |
| `Prueba manual` | UI flow, real audio, hardware | Someone other than the implementer | Signed checklist + captures |
| `Inspección de código` | Structural property (EU region, pinned model) | Human reviewer | Link to the lines + review date |
| `Revisión documental` | Process or documentation requirement (SBOM, IFU) | Quality / regulatory | Versioned document |

**All 64 currently say `Revisión documental / de proceso`** — the weakest of the
five, applied even to requirements that are measurable by eval (SOC-1 carries
«≤ 1 % over a sample ≥ 100»). Reclassifying them is real work and should be
validated by someone in regulatory: in an audit, the declared method is what you
are required to have executed.

Logged as known debt. Out of scope for the current round.

## Independence

A Verification derives from the **Feature**, never from the implementation. It can
and should be writable before any code exists — that is what makes it independent
evidence rather than a description of what was built.

Concretely: do not open the technical task to write a Verification. Open the Feature.

## Template

```markdown
### Requisito
SOC-1 (RF-M1-001)

### Método de verificación
Eval (gold standard)

### Objetivo
Verificar que el resumen generado no supera el umbral declarado de contenido no
fundamentado y no introduce diagnósticos nuevos.

### Precondiciones
1. Suite gold `summary-grounding` con >= 100 resúmenes anotados, en `evals/`
2. Staging con `api` desplegado en la versión bajo prueba
3. Versión del modelo de inferencia fijada y declarada (RNF-IA-008)

### Pasos de prueba
1. `just eval:gold summary-grounding --env staging`
2. Recoger `reports/summary-grounding-<run>.json`
3. Leer `metrics.ungrounded_rate` y `metrics.new_diagnoses`

### Resultado esperado
`ungrounded_rate` <= 0,010 y `new_diagnoses` == 0 sobre n >= 100

### Criterio de aprobación
PASS  si ungrounded_rate <= 1,0 % y new_diagnoses == 0
WARN  si ungrounded_rate entre 1,0 % y 2,0 % (umbral de alerta de SOC-1)
FAIL  si ungrounded_rate > 2,0 % o new_diagnoses > 0

### Evidencia
* `summary-grounding-<run>.json` adjunto al ticket
* `run_id` de la plataforma de evals
* Versión del modelo y sha del commit bajo prueba
```

## Summary naming

Verifications currently share their Feature's summary word for word, which makes
SOC-1 and SOC-65 indistinguishable at a glance. When the fill-in pass happens,
rename to:

```
Verificar <qué> (<REQ-ID>)
```

e.g. `Verificar fundamentación del resumen (RF-M1-001)`.
