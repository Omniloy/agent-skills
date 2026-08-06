# curl examples

Copy-paste-friendly calls for the SofIA Evaluator API. Replace `<...>`.

## Setup

**Local mode** — token comes from the backend's own `.env`:

```bash
BASE="http://localhost:8001"
TOKEN=$(grep -E '^EVALS_SKILL_TOKEN=' ~/sofia-evals/sofia-evals-back/.env | cut -d= -f2-)
curl -s "$BASE/health"     # {"status":"ok",...} before anything else
```

**Deployed mode** — token from the environment, base URL is the portal domain
(the evaluator itself isn't publicly reachable; nginx proxies `/api/`):

```bash
BASE="$SOFIA_EVAL_URL"
TOKEN="$EVALS_SKILL_TOKEN"
```

Every authenticated call:

```bash
curl -s "$BASE/api/v1/rubrics" -H "Authorization: Bearer $TOKEN"
```

---

## Resolve the catalog (GET before you assume an id exists)

```bash
AUTH=(-H "Authorization: Bearer $TOKEN")

curl -s "$BASE/api/v1/environments" "${AUTH[@]}"
curl -s "$BASE/api/v1/evaluators"   "${AUTH[@]}"
curl -s "$BASE/api/v1/rubrics?evaluation_type=patient_summary&is_active=true" "${AUTH[@]}"
curl -s "$BASE/api/v1/datasets?dataset_type=patient_summary" "${AUTH[@]}"
curl -s "$BASE/api/v1/datasets/<dataset_id>/samples?limit=5" "${AUTH[@]}"
```

---

## Create a rubric

At least one positive AND one negative (safety) criterion, ids unique, `points`
sign matching `polarity` — otherwise 400.

```bash
curl -s -X POST "$BASE/api/v1/rubrics" "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "AI_generated-patient-summary-v1",
    "evaluation_type": "patient_summary",
    "criteria": [
      {"id":"C1","description":"Recoge el motivo de consulta tal y como lo expresa el paciente","points":2,"polarity":"positive","category":"extraction"},
      {"id":"C2","description":"Incluye la medicación activa con dosis cuando se menciona","points":2,"polarity":"positive","category":"dosing"},
      {"id":"C3","description":"El resumen sigue un orden clínico coherente","points":1,"polarity":"positive","category":"reasoning"},
      {"id":"N1","description":"Inventa un diagnóstico, fármaco o dato que no aparece en la transcripción","points":-3,"polarity":"negative","category":"safety"},
      {"id":"N2","description":"Omite una alergia mencionada explícitamente","points":-3,"polarity":"negative","category":"safety"}
    ]
  }'
```

## Create a dataset and append samples

```bash
DATASET_ID=$(curl -s -X POST "$BASE/api/v1/datasets" "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "AI_generated-patient-summary-smoke",
    "dataset_type": "patient_summary",
    "source": "custom",
    "rubric_id": "<rubric_id>"
  }' | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')

curl -s -X POST "$BASE/api/v1/datasets/$DATASET_ID/samples" "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d '{
    "samples": [
      {
        "input": {"transcription": "Paciente de 54 años que acude por dolor torácico opresivo..."},
        "metadata": {"slice": "cardiologia", "language": "es"}
      }
    ]
  }'
```

For `medical_chat` the sample input is a question plus context instead:

```json
{"input": {"question": "¿Puedo tomar ibuprofeno con el sintrom?",
           "patient_data": "Varón 71a, FA anticoagulada con acenocumarol"}}
```

---

## Pre-flight

```bash
curl -s "$BASE/api/v1/runs?status=running" "${AUTH[@]}"
curl -s "$BASE/api/v1/datasets/$DATASET_ID/samples?limit=1" "${AUTH[@]}"   # must not be empty
```

## Launch a run (JSON body)

```bash
RUN_ID=$(curl -s -X POST "$BASE/api/v1/runs" "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "AI_generated-patient-summary-smoke",
    "run_type": "quality",
    "evaluation_type": "patient_summary",
    "dataset_id": "'"$DATASET_ID"'",
    "rubric_id": "<rubric_id>",
    "environment_id": "<environment_id>",
    "assistant_id": "<assistant_id>",
    "json_schema": {},
    "concurrency": 5
  }' | python3 -c 'import sys,json; print(json.load(sys.stdin)["run_id"])')

echo "run: $RUN_ID"
```

Baseline for a release gate: add `"is_baseline": true`.
Candidate: add `"baseline_run_id": "<baseline>", "release_label": "v1.4.0"`.

## Poll until terminal

```bash
while :; do
  SNAP=$(curl -s "$BASE/api/v1/runs/$RUN_ID" "${AUTH[@]}")
  echo "$SNAP" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(f"{d[\"status\"]} {d.get(\"completed_samples\")}/{d.get(\"total_samples\")}")'
  STATUS=$(echo "$SNAP" | python3 -c 'import sys,json; print(json.load(sys.stdin)["status"])')
  case "$STATUS" in completed|failed|cancelled) break;; esac
  sleep 10
done
```

---

## Read results

```bash
# Aggregates (average_score, min/max, gate_status, wins/ties)
curl -s "$BASE/api/v1/runs/$RUN_ID" "${AUTH[@]}"

# Per-sample, with the rubric criterion breakdown
curl -s "$BASE/api/v1/runs/$RUN_ID/items" "${AUTH[@]}"

# Only the failures
curl -s "$BASE/api/v1/runs/$RUN_ID/items?status=error" "${AUTH[@]}"

# Head-to-head (compare runs)
curl -s "$BASE/api/v1/runs/$RUN_ID/comparison-items" "${AUTH[@]}"
```

Which criteria failed, per sample:

```bash
curl -s "$BASE/api/v1/runs/$RUN_ID/items" "${AUTH[@]}" | python3 -c '
import sys, json
for item in json.load(sys.stdin):
    m = item.get("metrics") or {}
    failed = [r for r in m.get("rubric_results", []) if not r.get("met")]
    print(f"sample {item.get(\"sample_id\")} score={item.get(\"score\")}")
    for r in failed:
        print(f"   ✗ {r[\"criterion_id\"]}: {r.get(\"reason\")}")
'
```

Remember: `raw_score = (earned_positive + triggered_negative) / max_positive`
and it can be **negative**. It is not a percentage.

---

## Release gate

```bash
REPORT=$(curl -s -X POST "$BASE/api/v1/gate-reports" "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d '{
    "candidate_run_id": "'"$RUN_ID"'",
    "thresholds": {"max_score_drop": 0.02, "max_category_drop": 0.05, "max_sample_drop": 0.10}
  }')
echo "$REPORT"     # {"id": "...", "verdict": "passed"|"blocked"}

# Read the reasoning back — a verdict alone doesn't explain anything
REPORT_ID=$(echo "$REPORT" | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')
curl -s "$BASE/api/v1/gate-reports/$REPORT_ID" "${AUTH[@]}" | python3 -c '
import sys, json
d = json.load(sys.stdin)
print("verdict:", d["verdict"])
for c in (d.get("summary") or {}).get("checks", []):
    print(f'"'"'  [{c.get("metric_class")}] {"PASS" if c.get("passed") else "FAIL"} {c.get("name")}: {c.get("detail")}'"'"')
'
```

Thresholds default to `max_score_drop 0.02`, `max_category_drop 0.05`,
`max_sample_drop 0.10`, `noise_floor 0.0`; `min_score_floor` is optional and
sets an absolute quality floor on the normalised 0..1 score.
