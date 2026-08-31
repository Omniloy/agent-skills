# Contrato de datos — resolución contra el esquema real

El skill **nunca hardcodea nombres de tabla ni de columna**. El mapeo se resuelve una vez (fase 1 del SKILL.md), se confirma con el usuario y se cachea en la config bajo `data_contract`.

## 1. Los seis roles de columna

| Rol | Para qué | Requerido |
|---|---|---|
| `id` | Clave de la nota / del run; semilla del muestreo por hash | Sí |
| `created_at` | Define la ventana de 24 h de cada ejecución | Sí |
| `assistant_id` | La agrupación del reporte — asistente / médico / tenant | Sí |
| `template_id` | Atribuir un fallo recurrente a una plantilla concreta | Sí |
| `note` | El texto generado: el objeto del análisis | Sí |
| `input` | Transcripción o prompt de origen — habilita el eje de fidelidad (mejora futura) | Opcional |

## 2. Cómo resolverlo por MCP (Supabase, solo lectura)

> La conexión la maneja el **Supabase MCP server** (configurado con `read_only=true` en el `.mcp.json` — ver `references/auth.md`). El skill solo llama `execute_sql`; nunca maneja credenciales.

### 2.1 Encontrar la tabla

```sql
SELECT table_schema, table_name
FROM information_schema.tables
WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
  AND (table_name ILIKE '%run%' OR table_name ILIKE '%note%' OR table_name ILIKE '%langsmith%')
ORDER BY table_schema, table_name;
```

Candidatos típicos: tablas de runs de Langsmith persistidos (la nota generada vive en una columna de texto). Si hay varias, elegir la que tenga el volumen de la ventana y la nota; en caso de duda, preguntar al usuario.

### 2.2 Encontrar las columnas

```sql
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = '<tabla_elegida>'
ORDER BY ordinal_position;
```

### 2.3 Asignar los roles

Mapear columnas reales → roles con criterio, **no por nombre exacto**:

- `id` → la PK de la tabla (suele llamarse `id`, `run_id`, `note_id`…).
- `created_at` → timestamp de creación (puede ser `created_at`, `inserted_at`, `ts`…).
- `assistant_id` → quién generó la nota (`assistant_id`, `agent_id`, `medico_id`, `tenant_id`…).
- `template_id` → qué plantilla se usó (`template_id`, `template`, `prompt_id`…).
- `note` → el texto generado (`note`, `output`, `result`, `content`, `text`…). Debe ser texto con `length(note) > 0`.
- `input` → transcripción/prompt de origen (`input`, `transcription`, `prompt`, `source_text`…). **Opcional.**

### 2.4 Confirmar y cachear

Presentar el mapeo al usuario antes de guardarlo:

```json
{
  "data_contract": {
    "table": "langsmith_runs",
    "columns": {
      "id": "id",
      "created_at": "created_at",
      "assistant_id": "assistant_id",
      "template_id": "template_id",
      "note": "output",
      "input": null
    },
    "input_available": false
  }
}
```

Guardar en `~/.claude/note-quality-loop/config.json`. Si el usuario corrige un rol, actualizar y re-confirmar.

## 3. Notas

- Si `input` no existe → `input: null` y `input_available: false`. El modo rúbrica funciona igual (nunca necesita `input`); solo queda sin el eje de fidelidad futuro.
- El mapeo vive en la config, no en el código: sobrevive a un renombrado de tabla. Si el esquema cambia y el SQL falla, re-resolver el contrato (fase 1 otra vez) antes de tocar nada más.
- La regla de solo lectura aplica también aquí: solo `SELECT` / `information_schema`. Nunca DDL ni escrituras. Y está enforced por el MCP (`read_only=true`), no por memoria del modelo.
