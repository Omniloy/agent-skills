---
name: run-stack
description: Bring up the full sofia-care product locally - api, transcriber, assistant, self-hosted Supabase with seeds, and the SDK demo - with STT/LLM mocks and no production keys. Use to run the app, demo a flow, or debug cross-service behavior.
user-invocable: true
---

# Run the stack

Levanta el producto completo en local. Nunca requiere claves de producción: los
proveedores externos (STT, LLM) van mockeados por defecto.

## Arrancar

```bash
just dev            # compose completo: db + api + transcriber + assistant + mocks
just dev-sdk-demo   # la demo del widget apuntando al stack local
```

Perfiles útiles: `just dev --profile real-llm` (LLM real con tu clave de dev,
STT sigue mockeado) · `just dev-min` (solo db + api).

## Qué queda arriba

| Servicio | Puerto | Health |
|----------|--------|--------|
| Supabase local (db+auth+rest+storage) | 54321/54322 | `supabase status` |
| apps/api | 3000 | `curl localhost:3000/health` |
| apps/transcriber (WS) | 8080 | `curl localhost:8080/health` |
| apps/assistant | 2024 | `curl localhost:2024/ok` |
| mocks STT/LLM | 9090 | `curl localhost:9090/health` |
| demo del SDK | 5173 | navegador |

Los seeds crean tenants demo con API keys de desarrollo (impresas al arrancar).

## Sesión de prueba manual

1. Abre la demo (5173) y entra con la key del tenant demo.
2. Abre un paciente sintético → se genera el resumen (streaming).
3. Graba con el audio de fixture (`just play-fixture two-speakers`) → segmentos
   diarizados en vivo → cierra → transcripción final.
4. Pide un cambio de nota por chat → verifica el pop-up (solo campos cambiados)
   → Apply.

## Problemas típicos

- Puerto ocupado → `just dev-down` primero (o `docker compose down -v` para
  resetear también la BD local).
- Migración nueva no aplicada → `supabase db reset`.
- El widget no conecta al WS → revisa que el seed apunta `transcriber_url` a
  `ws://localhost:8080`.
- Nada de esto toca entornos remotos: si un comando pide credenciales cloud,
  estás en el sitio equivocado.
