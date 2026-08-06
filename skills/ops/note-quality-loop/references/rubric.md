# Criterio de calidad — rúbricas de los evals de SofIA

**Decisión de los seniors (Opción A):** el juez evalúa cada nota contra la **rúbrica activa de los evals de SofIA**, cargada desde la base de datos. No hay ejes hardcodeados en el skill: los criterios viven en la BD y se resuelven en cada ejecución (o se cachean en la config).

## 1. De dónde sale la rúbrica

Dos vías equivalentes; usa la que esté disponible en la sesión:

| Vía | Cómo | Cuándo |
|---|---|---|
| **SofIA evals API** | `GET /api/v1/rubrics?evaluation_type=note_generation&is_active=true` (skill `sofia-eval-api`, token `EVALS_SKILL_TOKEN`) | Preferida si la sesión tiene el token |
| **Supabase MCP (read)** | Consultar la tabla de rubrics por `evaluation_type` y `is_active` | Si no hay token pero sí MCP |

Reglas:

1. **Reusa antes de crear.** Si existe una rúbrica activa para `note_generation`, úsala. Nunca crees una rúbrica nueva solo para este loop.
2. Si hay **más de una** rúbrica activa → pregúntale al usuario cuál (o usa la más reciente, y registra la decisión en la config).
3. Cachea la rúbrica resuelta en la config (`rubric.resolved_id`) para no re-preguntar.
4. **Append-only:** nunca edites una rúbrica. Si los criterios cambian, se crea una nueva (`-v2`) y se re-baselinea.

### Anatomía de un criterio (contrato del evaluador)

```json
{
  "id": "C1",
  "description": "Incluye el motivo de consulta expresado por el paciente",
  "points": 2,
  "polarity": "positive",
  "category": "extraction"
}
```

| Campo | Significado para el juez |
|---|---|
| `id` | Referencia estable del criterio (`C*` positivo, `N*` negativo) |
| `description` | Un hecho comprobable — **esto es lo que el juez verifica** |
| `points` | Peso clínico (firmado; los negativos son penalizaciones) |
| `polarity` | `positive` (crédito) o `negative` (penalización: algo malo pasó) |
| `category` | Grupo para el gate: `extraction`, `reasoning`, `dosing`, `coding`, `safety`… |

La rúbrica **siempre** tiene al menos un positivo y un negativo (regla del servidor). El negativo suele ser `safety` — es lo que detecta invención.

## 2. De criterio a hallazgo

Un **hallazgo** (finding) nace cuando un criterio falla:

| Polaridad | Fallo | Ejemplo |
|---|---|---|
| `positive` | El criterio **no se cumple** | Falta el motivo de consulta → hallazgo |
| `negative` | El criterio **se cumple** (la cosa mala pasó) | «Inventa una alergia» → hallazgo |

**Una nota puede tener varios hallazgos.** La nota cuenta como `major+` (fallando) si tiene **al menos un** hallazgo de severidad `major` o `blocking`.

## 3. Severidad — la asigna el juez

La rúbrica **no trae severidad** (solo polarity/points/category). El juez asigna `minor`/`major`/`blocking` por hallazgo, usando la rúbrica como guía:

| Guía | Severidad sugerida |
|---|---|
| Penalización (`negative`) en categoría `safety` | 🔴 `blocking` |
| Penalización (`negative`) en otra categoría (`dosing`, `coding`, `reasoning`…) | 🟠 `major` |
| Positivo no cumplido: sección exigida por la plantilla ausente | 🟠 `major` |
| Positivo no cumplido: contenido presente pero genérico | 🟡 `minor` |
| Ruido de formato / idioma / registro | 🟡 `minor` |

Estas son **guías**, no reglas rígidas: el juez puede subir o bajar un nivel cuando el contexto lo justifique, y debe explicar el porqué en `reasoning`. Un `blocking` requiere que la nota no sea usable clínicamente (invención clínica, PII, dato peligroso).

## 4. JSON Schema del juez (salida estructurada obligatoria)

Cada nota → **un** objeto de veredicto. La agregación valida que cada `quote` exista literalmente en la nota original (≤ 200 caracteres); si no existe, el hallazgo se descarta.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "NoteVerdict",
  "type": "object",
  "additionalProperties": false,
  "required": ["note_id", "assistant_id", "template_id", "overall", "findings"],
  "properties": {
    "note_id": {"type": "string", "description": "id de la nota (rol `id` del contrato)"},
    "assistant_id": {"type": "string"},
    "template_id": {"type": "string"},
    "overall": {"enum": ["pass", "fail"]},
    "findings": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["criterion_id", "severity", "quote", "reasoning"],
        "properties": {
          "criterion_id": {"type": "string", "description": "id del criterio de la rúbrica que falló (C*/N*)"},
          "severity": {"enum": ["minor", "major", "blocking"]},
          "quote": {"type": "string", "maxLength": 200, "description": "Cita literal de la nota (enmascarada)"},
          "reasoning": {"type": "string", "description": "Por qué falla el criterio, en 1-2 frases"}
        }
      }
    }
  }
}
```

Reglas del juez:

- **`overall: "fail"`** ⇔ al menos un hallazgo `major+` (es lo que alimenta el umbral del 15 %).
- **Todo hallazgo con cita ≤ 200 caracteres.** Sin cita verificable → se descarta el hallazgo.
- **Cita enmascarada** (PII): nombres → `[Nombre]`, NHC → `[NHC]`, teléfonos → `[Teléfono]`, fechas de nacimiento → `[FechaNac]`. El gate de PII del render falla si detecta un identificador sin enmascarar.
- Trabaja en **lotes de ~10 notas** por asistente, y guarda cada lote como JSONL antes de continuar (si el run se corta, se retoma por lote).

## 5. Cache y comparabilidad

- La rúbrica resuelta se guarda en la config (`rubric.resolved_id`). Un cambio de rúbrica activa entre runs **invalida la comparación de deltas** — el reporte debe anotarlo.
- Los deltas se calculan contra el run anterior **con la misma rúbrica**. Si la rúbrica cambió, el delta se marca `n/a` en lugar de comparar peras con manzanas.
