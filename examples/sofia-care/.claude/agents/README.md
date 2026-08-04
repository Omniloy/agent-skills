# Agents

Subagentes del monorepo. `/ship` y `/epic-loop` delegan la implementación en el
especialista del paquete tocado y la revisión pre-PR en los revisores.

| Agent | Rol | Cuándo |
|-------|-----|--------|
| `change-reviewer` | Revisor general del diff (contratos cross-package, tenancy, degradación, convenciones) | Tras CUALQUIER cambio, antes de `mark_verified` |
| `medical-safety-reviewer` | Lente clínica/PHI con las reglas duras del backlog SOC (ISO 14971) | Diffs que tocan assistant, transcriber, flujos de extracción/nota, data-path del SDK, o esquema clínico en db/ — lo invoca change-reviewer o directo |
| `deepagent-dev` | Especialista de `apps/assistant` (deepagents/LangGraph) | Implementación de subagentes, tools, guardrails, config por tenant |
| `sdk-dev` | Especialista de `packages/sdk` (widget React/web-component) | UI, clientes WS/REST, anonimización cliente, pop-up de verificación |
| `transcriber-dev` | Especialista de `apps/transcriber` (aiohttp/WS/STT) | Providers, protocolo, diarización, desconexiones, pase batch |
| `test-author` | Autor de tests: unit + contrato + umbrales SOC como aserciones | Cuando falta cobertura o al TDD-ear un módulo |

Regla de composición: **implementa el especialista → testea test-author (si el
especialista no cubrió) → revisa change-reviewer (→ medical-safety-reviewer si
aplica) → `mark_verified.sh` → el PR lo lleva `/review-pr`**.
