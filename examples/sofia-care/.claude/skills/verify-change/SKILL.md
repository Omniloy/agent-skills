---
name: verify-change
description: Smoke-test the current change for real - run the touched flow end-to-end against the affected service (or the compose stack with mocks) and assert it completes. Required by the Stop gate before a change can be considered done. Use after quality-gate passes.
user-invocable: true
---

# Verify change (smoke real)

El gate estático dice que compila; esta skill demuestra que **funciona**. Elige
el smoke según el paquete tocado, ejecútalo, y si pasa registra el estado.

## Smokes por paquete

| Tocaste | Smoke |
|---------|-------|
| `apps/api` | Arranca la API (`just dev-api` o compose) → `curl localhost:3000/health` → ejercita el endpoint cambiado con un payload válido del contrato y verifica la respuesta. |
| `apps/transcriber` | Sesión WS de fixture completa: `uv run python -m tests.smoke.ws_session` (handshake → audio de fixture → segmentos → `graceful_disconnect` → transcripción final). Con providers mockeados. |
| `apps/assistant` | Invoca el flujo tocado en local (`langgraph dev` / runner local) con un caso real del golden set y verifica que completa sin error y respeta el contrato de salida. |
| `packages/sdk` | `pnpm --filter <variant> build` de ambas variantes + monta la demo (`just dev-sdk-demo`) y ejercita la pantalla tocada; para lógica pura, la suite vitest del módulo. |
| `packages/contracts` | `just contracts-codegen` + tests de contrato de TODOS los consumidores en verde. |
| `db/` | `supabase db reset` (aplica todo desde cero) + seeds + test RLS de aislamiento. |
| Cross-servicio | `tests/integration/` del escenario afectado sobre el compose con mocks (`just test-integration <escenario>`). |

## Reglas

1. El smoke ejercita **el flujo cambiado**, no un hello-world: si tocaste la
   diarización, la sesión de fixture tiene dos hablantes; si tocaste el pop-up,
   la demo abre el pop-up.
2. Con mocks de STT/LLM — el smoke nunca llama servicios externos reales ni usa
   claves de producción.
3. Falla → diagnostica y arregla antes de seguir (vuelve a quality-gate si
   tocaste código).

## Al terminar

Smoke verde + review del `change-reviewer` hechos →

```bash
bash .claude/hooks/mark_verified.sh
```

(El Stop gate no dejará cerrar el turno sin esto; `skip "razón"` solo para
cambios docs-only.)
