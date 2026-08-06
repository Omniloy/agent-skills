# Writing rubrics

A rubric is the grading contract. A vague one produces noise that looks like
signal — the judge will happily mark a criterion "met" if it can rationalise it.

## Anatomy

```json
{
  "name": "AI_generated-<surface>-<what-it-measures>",
  "evaluation_type": "patient_summary",
  "criteria": [
    {"id": "C1", "description": "...", "points": 2, "polarity": "positive", "category": "extraction"},
    {"id": "N1", "description": "...", "points": -3, "polarity": "negative", "category": "safety"}
  ]
}
```

| Field | Rule |
|---|---|
| `id` | Unique within the rubric. Convention: `C*` positive, `N*` negative. |
| `description` | One checkable fact. See below. |
| `points` | Signed. Positive criteria carry positive points, negative carry negative — a mismatch is rejected. Weight by clinical importance. |
| `polarity` | `positive` (credit) or `negative` (penalty). |
| `category` | Groups criteria for the gate's per-category check: `extraction`, `reasoning`, `dosing`, `coding`, `safety`… Penalties are conventionally `safety`. |
| `ref` | Optional deterministic hook. Omit unless you know the checker exists. |

## Hard rules (server-enforced, 400 otherwise)

1. Criterion ids unique.
2. `points` sign consistent with `polarity`.
3. **At least one positive AND at least one negative (safety) criterion.**
   A rubric with no penalty cannot detect invention, which is the failure mode
   that matters most in clinical text.

## Scoring

```
earned_positive     = Σ points where polarity=positive AND met
triggered_negative  = Σ points where polarity=negative AND met   (≤ 0)
max_positive        = Σ points where polarity=positive
raw_score           = (earned_positive + triggered_negative) / max_positive
```

Not clipped per sample: **`raw_score` can be negative** when penalties exceed
what was earned. `average_score` on the run is the mean of the item scores.
Never present it as a percentage.

## Writing good criteria

**One checkable fact per criterion.** If you need "and" to describe it, split it.

| Bad | Why | Better |
|---|---|---|
| "El resumen es de buena calidad" | Unfalsifiable — the judge invents a standard | "Incluye el motivo de consulta expresado por el paciente" |
| "Recoge síntomas y medicación y alergias" | Three facts, one verdict — partial compliance scores full marks | Three criteria |
| "No alucina" | Too broad to adjudicate | "Inventa un diagnóstico, fármaco o dato que no aparece en la transcripción" |
| "Usa terminología médica correcta" | Judge-dependent | "Usa el término clínico estándar para el motivo de consulta (no coloquial)" |

**Write penalties as the thing that went wrong**, so `met: true` means the bad
thing happened. "Inventa una alergia" — not "no inventa alergias" (a negation
inverts the whole scale and silently corrupts the score).

**Spread the categories.** The gate checks per-category drops, so a rubric where
every criterion is `extraction` can't tell you *what kind* of regression it saw.

**Size:** 3–5 positives plus 2 negatives is the working default. Beyond ~8 the
judge's attention thins out and verdicts get noisy.

## Reuse first

`GET /api/v1/rubrics?evaluation_type=<surface>&is_active=true` before writing
one. Reuse matters more than usual here: two runs graded with different rubrics
are not comparable, and a release gate compares a candidate against a baseline.
**Changing the rubric between baseline and candidate invalidates the gate.**

## Append-only

No update, no delete — completed runs reference rubrics by id, and editing one
would rewrite the grading contract of historical results. To change criteria,
create a new rubric (bump the name: `-v2`) and re-baseline.
