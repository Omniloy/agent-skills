---
name: add-contract
description: Add or change a contract in packages/contracts the right way - edit the JSON Schema source, regenerate TS+Pydantic types, run every consumer's contract tests, and check versioning/compatibility. Use whenever a payload that crosses a service boundary changes.
user-invocable: true
---

# Add / change a contract (contract-first)

Los contratos (`packages/contracts`) son la única fuente de verdad de todo lo
que cruza una frontera: contexto de paciente del HIS, protocolo WS, esquema de
extracción, roles, auth. **Nunca** se edita un tipo generado ni se duplica a
mano un tipo de wire.

## Flujo

1. **Edita el schema fuente** (`packages/contracts/<área>/*.json`). Reglas:
   - Campos condicionales del integrador → marcados por nivel de integración.
   - Enums cerrados con los valores canónicos (p. ej. roles con
     `"unidentified"` explícito — SOC-49).
   - Nada de `additionalProperties: true` en payloads clínicos.
2. **Regenera** (el hook `contracts_codegen` lo hace al guardar; a mano):
   ```bash
   just contracts-codegen
   ```
   Genera TS (sdk, api) y Pydantic (transcriber, assistant). Los generados van
   marcados y NO se editan.
3. **Tests de contrato de TODOS los consumidores**:
   ```bash
   pnpm exec turbo run test --filter='...@sofia-care/contracts'
   cd apps/transcriber && uv run pytest tests/contracts -q
   cd apps/assistant   && uv run pytest tests/contracts -q
   ```
   Payloads de ejemplo nuevos → añádelos a la carpeta de ejemplos del schema
   (validados en CI).
4. **Compatibilidad y versionado** — checklist obligatoria:
   - ¿El cambio es aditivo (campo opcional) o rompe? Si rompe: versiona el
     schema y documenta la ventana de convivencia (cohortes `client_version` —
     SDKs viejos desplegados en HIS reales siguen hablando la versión anterior).
   - ¿Afecta al contrato de integración del HIS? → actualiza la IFU de
     integración (SOC-58) y márcalo en el PR.
   - ¿Toca el formato de anonimización? → cliente (SDK) y verificación de borde
     (E3.4) cambian JUNTOS, mismo PR.

## Definition of done

Schema + generados + ejemplos + tests de los 4 consumidores en verde en el
mismo cambio. Un schema editado sin sus consumidores es un cambio incompleto —
el Stop gate y `change-reviewer` lo bloquearán.
