---
name: soc-task
description: Prepare a unit of work from the Jira SOC backlog - read the Feature (SOC-nn), extract its quantitative acceptance criteria and ISO 14971 risks, create the linked technical Tarea (via create-jira-work-items, Relates - never subtasks), link its mirror Verification, and hand off to /ship. Use to start work on any SOC Feature.
user-invocable: true
---

# SOC task (Feature → Tarea técnica → /ship)

La unidad de trabajo de sofia-care nace en el Jira SOC (proyecto SofIA Care,
board 269). Esta skill prepara el trabajo; la implementación la lleva
`/loop /ship <tarea>`.

## Estructura del backlog (contrato del equipo)

- **Features** SOC-1…64, en orden de prioridad. Cada una: user story, criterios
  de aceptación **cuantitativos** (Gherkin con umbrales) y riesgos ISO 14971.
- **Verifications** SOC-65…128 — espejo 1:1 (la de SOC-nn es **SOC-(nn+64)**).
- Cada Feature lleva **Tareas técnicas** (tipo *Tarea*) y de **Verificación**
  enlazadas con `Relates` — **nunca subtareas**.

## Flujo

1. **Lee la Feature** (Atlassian MCP: `getJiraIssue SOC-nn`). Extrae: user
   story, el criterio cuantitativo exacto (p. ej. «recall ≥ 98 % y FP ≤ 5 %
   sobre ≥ 100 resúmenes»), los riesgos (R-nn) y el "Fuera de alcance".
2. **Localiza el plan**: busca en `examples/sofia-care/manifest.json` (o el
   backlog GitHub creado desde él) las sub-issues cuyo `prd_refs` citan SOC-nn
   — ahí está el cómo técnico (paquetes, invariantes, referencias).
3. **Crea la Tarea técnica** delegando en el skill `create-jira-work-items`
   (respeta el patrón Feature + Tarea + Verificación con `Relates`):
   - Título: `[SOC-nn] <qué se construye>`.
   - Cuerpo: sección Funcional (de la user story) + Técnica (del plan) +
     **el umbral cuantitativo como criterio de aceptación literal** + riesgos.
   - Enlaza `Relates` → SOC-nn y verifica que la Verification SOC-(nn+64)
     también queda enlazada a la Feature.
   - **Confirma con el humano antes de crear** (creación en Jira es outward).
4. **Lanza el trabajo**: `/loop /ship <clave-de-la-tarea>` — ship usará los
   subagentes del harness (deepagent-dev / sdk-dev / transcriber-dev) y los
   gates (quality-gate → verify-change → change-reviewer → review-pr).
5. **Al cerrar**: mapea la evidencia contra la Verification espejo — qué test /
   eval asserta cada umbral (los nombra `test_socNN_*`) — y comenta el enlace
   PR + evidencia en la Tarea. La ejecución formal de la Verification va con
   `/live-testing-plan` (+ `/agent-eval-api` si es por evals).

## Guardrails

- Nada se crea en Jira sin aprobación explícita del humano.
- Un umbral cuantitativo sin test/eval que lo asserte = tarea NO terminada.
- Si la Feature es condicional al integrador («cuando el integrador comparte…»),
  la Tarea lo modela como nivel de integración, no lo asume.
