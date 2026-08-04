---
name: new-migration
description: Create a Supabase migration for sofia-care the right way - single naming convention, idempotent SQL, apply-from-scratch locally, and the mandatory cross-tenant RLS isolation test. Use whenever db/ schema changes, including pg_cron jobs and RLS policies.
user-invocable: true
---

# New migration (Supabase self-hosted)

El esquema vive como código en `db/supabase/migrations/`. Reglas no negociables
(vienen de la deuda del repo anterior: dos convenciones de nombre mezcladas y
cero tests):

## 1. Crear

```bash
supabase migration new <verbo_objeto>     # p. ej. add_assistant_config_history
```

- **Convención única**: prefijo timestamp de fecha (`YYYYMMDDHHMMSS_`) que el
  CLI genera — nunca epochs a mano.
- **Idempotente siempre**: `CREATE TABLE IF NOT EXISTS`, `DROP POLICY IF EXISTS`
  antes de `CREATE POLICY`, `ALTER TABLE … ADD COLUMN IF NOT EXISTS`.
- Una migración = un cambio coherente. Nada de mega-migraciones.

## 2. Lo que toda tabla clínica lleva

- `tenant_id` + **política RLS** por tenant (SOC-61). Sin excepciones.
- Si guarda audio/transcripción: `purge_at` y su job de purga (pg_cron +
  pg_net, programado POR MIGRACIÓN — nunca por dashboard; SOC-57).
- Si es auditable: trigger hacia `audit_log` (hash-chained).

## 3. Aplicar y probar en local

```bash
supabase db reset          # aplica TODAS las migraciones desde cero + seeds
cd db && uv run pytest tests/ -q
```

`db reset` en verde es obligatorio: si tu migración solo funciona
incrementalmente sobre tu BD local sucia, está mal.

## 4. El test RLS es obligatorio

Toda tabla nueva con `tenant_id` añade un caso a la suite de aislamiento:

- Con el contexto del tenant A, un `SELECT`/`UPDATE`/`DELETE` sobre filas del
  tenant B devuelve **0 filas** (no error silenciado — cero filas).
- El intento queda registrado en `audit_log` cuando aplica.

Sin ese test, el cambio no pasa el Stop gate ni la review.

## 5. Despliegue

- CI aplica el esquema desde cero en cada PR (job de `db/`).
- A entornos reales: `supabase db push` — **siempre pide confirmación** (el
  guard lo marca como `ask`) y va por la pipeline, no a mano.
