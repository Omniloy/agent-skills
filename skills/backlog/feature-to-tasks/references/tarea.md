# The technical task template

Copy this structure verbatim and fill it. Do not paraphrase the headings, do not
reorder the sections, do not invent new ones. Across ~150 tickets, small drifts
compound into "there is no standard".

The headings are Spanish because they land in Spanish tickets on a Spanish board.
Everything below the headings is Spanish too — match the 64 Features.

## The template

```markdown
### Requisito
SOC-1 (RF-M1-001) · SOC-31 (RF-M1-006)

### Funcional
Qué obtiene el usuario. Dos o tres líneas, en lenguaje de producto.

### Enfoque técnico
El cómo y la decisión clave ya tomada. Esto NO se re-discute durante la
implementación: es el acuerdo previo.

### Alcance
IN
* lo que entra
OUT
* lo que NO entra, con el SOC-n al que pertenece

### Puntos de entrada
* `apps/api/src/routes/summary.ts` [previsto] — qué hacer aquí
* Patrón a seguir: `apps/api/src/routes/guidelines.ts` [previsto]

### Contrato
```http
POST /v1/recurso
200 -> { ... }
```

### Criterios de aceptación
* Verificables. Con el umbral numérico si el requisito lo tiene.

### Comprobación
```bash
just test api
just lint api && just typecheck api
```

### Riesgos mitigados (ISO 14971)
* R-02 — cómo lo mitiga concretamente esta tarea
```

`### Contrato` is the only optional section. Omit it entirely when the task
changes no wire shape, schema, or public type. Never leave it empty, never write
"N/A".

## Section rules

### `### Requisito`
One or more `SOC-<n> (<REQ-ID>)`, separated by ` · `. The `REQ-ID` is the Feature's
label (`RF-M1-001`, `RNF-IA-002`). This is the traceability anchor for ISO 14971 and
IEC 62304 — it is not decoration, and a task without it cannot be created.

Cite every Feature the task actually serves, not just the one you started from.
A task usually serves one; `contracts` tasks often serve several.

### `### Funcional`
Two or three lines, product language, no implementation nouns. Comes from the
manifest's `functional_md` or from the Feature's user story.

Its job: let the AI notice when its own plan has drifted from the user-facing goal.
If you cannot state the user benefit, the task is probably infrastructure that
belongs to a different Feature.

### `### Enfoque técnico`
The decision that has already been made, stated as settled. Comes from the
manifest's `technical_md`, refined by human review.

This section exists to stop the AI re-litigating architecture on every run. Write
it as a decision, not as options: "Subagente del deep agent con pasada de
verificación post-generación", not "Se podría hacer con un subagente o con…".

If the task touches more than one package, **justify it here** — that is the only
place the exception is recorded.

### `### Alcance`
`IN` is what this PR delivers. `OUT` is what a reasonable reader would assume is
included but is not — and each `OUT` bullet names the `SOC-n` that does cover it:

```
OUT
* Panel de cobertura -> SOC-6
* Codificación SNOMED de los hallazgos -> SOC-13
```

`OUT` is the highest-yield section in the whole ticket. It is what stops the AI
expanding into adjacent code it can see, and what turns 150 loose tickets into a
navigable backlog. An empty `OUT` is a Definition-of-Ready failure; if there is
genuinely nothing adjacent, write why in one line.

Never grow `IN` when refining an existing ticket — see `refinado.md`.

### `### Puntos de entrada`
Real paths from `monorepo.md`, one per line, each with what to do there. Include a
`Patrón a seguir:` line pointing at the nearest existing analogue — it is the
cheapest way to get house style right.

Mark `[previsto]` while the package does not exist. See `monorepo.md`.

This is the section that saves the most work: without it the AI sweeps the whole
tree before writing a line.

### `### Contrato`
Only when a wire shape, schema, or public type changes. Fenced block, real syntax
(`http`, `sql`, `ts`). Include the error cases — they are where implementations
diverge:

```http
422 -> contrato de contexto de paciente inválido
503 -> subagente no disponible (ver SOC-7, degradación parcial)
```

A contract change belongs in `packages/contracts`, not in its consumer.

### `### Criterios de aceptación`
Each bullet must be checkable by a command or a binary observation. Copy numeric
thresholds from the Feature **literally** — `<= 1,0 % sobre muestra >= 100`, not
"una tasa baja de alucinación". Paraphrasing a threshold loses the audit trail.

If the Feature carries no threshold and the task needs one, stop and ask. Do not
invent it.

### `### Comprobación`
A `bash` block of `just` commands only. See the command contract in `monorepo.md`.
These are the commands the AI runs before every commit — list the ones for *this*
task, not the repo's full battery.

### `### Riesgos mitigados (ISO 14971)`
The `R-nn` codes the Feature declares, each with one line on how *this* task
mitigates it. Mandatory in SOC: it is a medical product and this is the link
between a risk in the file and the code that addresses it.

If the Feature declares risks and the task cannot mitigate any of them, say so —
it usually means the decomposition put the mitigation in a different task, and
that task should cite it.

## Worked example

Feature `SOC-1 (RF-M1-001)` — *«Generar un resumen del paciente a partir de los
datos del HIS, sin diagnósticos nuevos»*, threshold: ungrounded content ≤ 1,0 % over
a gold sample of ≥ 100 summaries. Decomposes into three tasks, one per package.

### `assistant: subagente de resumen con verificación de fundamentación`

```markdown
### Requisito
SOC-1 (RF-M1-001) · SOC-34 (RNF-SEG-002)

### Funcional
El resumen no contiene ninguna afirmación que no esté en la historia clínica, ni
emite diagnósticos nuevos o recomendaciones terapéuticas.

### Enfoque técnico
Subagente del deep agent. Generación por secciones y, después, una pasada de
verificación de fundamentación: cada afirmación debe anclar a un dato fuente; las
no ancladas se eliminan o se marcan. Hereda el modo neutro (E3.3), que mantiene la
capacidad de diagnóstico desactivada.

### Alcance
IN
* Filtro de relevancia sobre el historial
* Generación por secciones + anclaje afirmación -> fuente
* Pasada de verificación post-generación
OUT
* La suite gold que mide el umbral -> E11.1, tarea aparte
* Detección de contradicciones entre fuentes -> SOC-9
* Panel de cobertura -> SOC-6

### Puntos de entrada
* `apps/assistant/src/subagents/summary.py` [previsto] — subagente nuevo
* `apps/assistant/src/core/neutral_mode.py` [previsto] — modo neutro a heredar (E3.3)
* Patrón a seguir: `apps/assistant/src/subagents/guidelines.py` [previsto]

### Criterios de aceptación
* Contenido no fundamentado <= 1,0 % sobre muestra gold >= 100 resúmenes
* 0 diagnósticos nuevos ni recomendaciones terapéuticas (suite adversarial)
* Toda afirmación superviviente tiene >= 1 ancla a dato fuente

### Comprobación
```bash
just test assistant
just lint assistant && just typecheck assistant
```

### Riesgos mitigados (ISO 14971)
* R-01 — el filtro de relevancia nunca descarta secciones críticas (alergias,
  medicación activa, antecedentes): lista fija que siempre pasa
* R-02 — la pasada de verificación elimina toda afirmación no anclada
```

### `api: endpoint de resumen del paciente`

```markdown
### Requisito
SOC-1 (RF-M1-001)

### Funcional
El médico obtiene un resumen estructurado del paciente construido solo con datos
del HIS y del historial filtrado por relevancia.

### Enfoque técnico
Endpoint delgado: valida el contrato de contexto de paciente (E0.2), delega en el
subagente de resumen y devuelve secciones con sus fuentes. Sin lógica de generación
aquí — vive en el asistente.

### Alcance
IN
* Endpoint `POST /v1/summary` + validación del contrato de entrada
* Propagación de las fuentes por sección en la respuesta
OUT
* La generación en sí -> tarea de `assistant`
* Codificación SNOMED de los hallazgos -> SOC-13

### Puntos de entrada
* `apps/api/src/routes/summary.ts` [previsto] — endpoint nuevo
* `packages/contracts/src/patient-context.ts` [previsto] — contrato de entrada (E0.2)
* Patrón a seguir: `apps/api/src/routes/guidelines.ts` [previsto]

### Contrato
```http
POST /v1/summary
{ "patientId": string, "sections"?: string[] }
200 -> { "sections": [{ "id", "text", "sources": [{"ref","type"}] }] }
422 -> contrato de contexto de paciente inválido
503 -> subagente no disponible (ver SOC-7, degradación parcial)
```

### Criterios de aceptación
* Toda sección devuelta incluye al menos una `source` resoluble
* Contexto inválido devuelve 422 sin invocar al modelo
* Indisponibilidad del subagente devuelve 503, no 500

### Comprobación
```bash
just test api
just lint api && just typecheck api
```

### Riesgos mitigados (ISO 14971)
* R-02 — una respuesta sin `sources` no es representable en el tipo
```

### `sdk: render del resumen por secciones con procedencia`

```markdown
### Requisito
SOC-1 (RF-M1-001) · SOC-31 (RF-M1-006)

### Funcional
El médico ve el resumen dividido en secciones y puede consultar el origen de cada
afirmación sin salir del widget.

### Enfoque técnico
Componente de resumen del widget, patrón react + web-component ya existente. Cada
afirmación renderiza su procedencia inline; el diseño se mantiene respecto al SDK
actual, el código es nuevo.

### Alcance
IN
* Render por secciones + affordance de procedencia por afirmación
* Estados de carga, vacío y error
OUT
* Rediseño del pop-up de inserción -> SOC-18 / SOC-23
* Aviso de modo degradado -> SOC-8

### Puntos de entrada
* `packages/sdk/core/src/components/Summary/` [previsto] — componente nuevo
* Patrón a seguir: `packages/sdk/core/src/components/Guidelines/` [previsto]

### Criterios de aceptación
* Cada afirmación expone su fuente en <= 1 interacción
* Los tres estados (carga, vacío, error) tienen render propio
* 0 regresiones de accesibilidad respecto al baseline del SDK

### Comprobación
```bash
just test sdk
just build sdk && just lint sdk
```

### Riesgos mitigados (ISO 14971)
* R-02 — la procedencia visible permite al médico detectar una alucinación
```

Note the pattern across the three: every `OUT` bullet names the `SOC-n` or task
that does own it, and `api` blocks nothing while `contracts` would block both
consumers. Those two habits are what make the batch navigable.
