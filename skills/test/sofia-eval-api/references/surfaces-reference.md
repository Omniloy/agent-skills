# Surfaces and the agent input contract

Two orthogonal axes describe a run:

- **`run_type`** — `quality` (one assistant), `compare` (A/B, needs
  `assistant_id_2`), `consistency` (same sample N times, `num_runs` 2–20).
- **`evaluation_type`** — the *surface*, which selects the scoring engine.

## Rubric surfaces (current design — prioritise these)

| Surface | What it grades |
|---|---|
| `coding` | Clinical coding suggestions |
| `patient_summary` | Patient summary generation |
| `medical_chat` | Conversational clinical Q&A |
| `note_generation` | Clinical note generation (legacy alias kept for older runs) |

These grade against a rubric and produce
`raw_score = (earned_positive + triggered_negative) / max_positive`, which **can
be negative**.

## Legacy surfaces (0–100 judge)

`extraction` (the fallback when no surface is given), `patient_data`,
`transcription` / `transcription_agent`. Scored by the structure/semantic/
hallucination judge on a 0–100 scale, normalised by dividing by 100 when the
gate compares them.

If asked for one of these, say it's the legacy path and offer the rubric surface
that fits. Never present a 0–100 legacy score and a rubric score in the same
table without labelling the scale.

## Agent input per surface

The background task assembles each sample's agent input from the sample's
`input` field. Shape samples accordingly.

**`medical_chat`** — no `json_schema` needed:

```json
{"question": "¿Puedo tomar ibuprofeno con el sintrom?",
 "patient_data": "Varón 71a, FA anticoagulada con acenocumarol"}
```

becomes `{messages: [{type: "human", content: question}], patient_data,
medical_practice, doctor_id, current_datetime}`. `patient_data` also accepts the
keys `history` or `context`; a plain string sample is treated as the question.

**Extraction-style surfaces** (`patient_summary`, `note_generation`, `coding`,
`extraction`) — **`json_schema` is effectively required on the run**; without it
the sample input passes through raw and the agent has no contract:

```json
{"transcription": "Paciente de 54 años que acude por dolor torácico..."}
```

becomes `{doctor_id, json_schema, transcription, target_language,
current_datetime}`. The transcription is read from `transcription`, `transcript`
or `text`; a plain string sample is treated as the transcription itself.

Where to get the `json_schema`: from the user, or copy it from an existing run's
`config.json_schema` for the same assistant (`GET /api/v1/runs` to find one).

**Transcription datasets** (`agent_type: "transcription"`) run a multi-turn
chunked pipeline; `chunk_size` (50–5000, default 300) controls chars per turn.

## Rubric resolution precedence

Most specific wins:

1. `rubric_id` on the run
2. the sample's `metadata.rubric` (legacy per-sample rubrics)
3. the dataset's `rubric_id`

Pass `rubric_id` on the run to grade an existing dataset against different
criteria without touching the dataset.

## Timeouts

420s per sample (scaled by chunk count for transcription, capped at 3600s). If
every sample errors, the run is marked `failed` with "All samples failed". Runs
stuck `running` past 30 minutes are reaped by the orphan sweep.
