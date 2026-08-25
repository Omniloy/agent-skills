# The technical task template

Seven sections. Copy the structure verbatim: do not paraphrase the headings, do not
reorder them, do not add new ones.

The headings are Spanish because they land on a Spanish board. So is everything
below them — match the 64 Features.

**There is no `### Funcional` section.** The Feature already says what the doctor
gets, and the ticket is read by the technical team and by an AI that is going to
move code. Restating the product story in every task is noise that pushes the
useful part off the first screen.

**There is no `### Enfoque técnico` and no `### Contrato`.** Both existed to record
architecture decisions taken while designing from scratch. What replaces them is
`### Punto de partida`, which says what already exists, what this task does with it,
and what has to change on the way.

## The template

```markdown
### Requisito
SOC-1 (RF-M1-001) · SOC-31 (RF-M1-006)
Verificación: SOC-65

### Punto de partida
Porte.

De `sofia-sdk-core/core/src/modules/chat/` — vista, hooks y cliente de API. Es el
chat que los médicos usan hoy dentro del HIS.

Se trae completo: componentes, hooks y capa de red.

Cambia al traerlo:
* Los tipos salen de `packages/contracts`, no se redefinen aquí
* El tenant llega explícito, hoy es implícito
* Sin PHI hacia el backend: se anonimiza en el cliente (SOC-159)

### Alcance
IN
* lo que esta tarea trae al paquete
OUT
* lo que un lector razonable asumiría incluido y no lo está, con su SOC-n

### Destino
* `apps/assistant/src/agents/summary/` [previsto] — destino del portado
* Patrón a seguir: `apps/assistant/src/core/agent.py` [previsto]

### Criterios de aceptación
* El umbral de la Feature, copiado literal
* Paridad con lo que hoy hace el origen, y cómo se comprueba

### Comprobación
```bash
just test assistant
just lint assistant && just typecheck assistant
```

### Riesgos mitigados (ISO 14971)
* R-02 — cómo lo mitiga concretamente esta tarea
```

## Section rules

### `### Requisito`

One or more `SOC-<n> (<REQ-ID>)` separated by ` · `, then a `Verificación:` line
with the Verification that will prove this Feature.

This is the traceability anchor for ISO 14971 and IEC 62304. A task without it
cannot be created. Cite every Feature the task actually serves, not only the one
you started from.

Resolve the Verification by requirement label, never by arithmetic — the ranges are
not a constant offset:

```
project = SOC AND issuetype = Verification AND labels = RF-M1-001
```

### `### Punto de partida`

The section the whole skill exists for. It opens with **one word on its own line** —
`Porte.`, `Reescritura.` or `Nuevo.` — and then three parts, in this order:

**Where it comes from.** Repo and real paths, seen in the clone under
`~/Workspace/Omniloy/Sofia/`. One line saying what that code does today, so the
reader knows they are looking at the right thing.

**What this task does with it.** Say it plainly, and let it depend on the kind:

* `Porte` — "se trae completo", or "se trae sólo el cliente de Azure, no el reintento"
* `Reescritura` — what survives (prompts, reglas, casos) and what gets rebuilt, plus
  **the decision that says why**: the ADR, the Feature or the foundation task that
  settled the target architecture. Without that citation it is not a rewrite, it is
  this skill inventing one
* `Nuevo` — one line saying there is nothing behind it, and why the capability does
  not exist today

**What changes on the way.** The short list of monorepo rules the current code does
not meet. This is where the audit debt actually lives, and the usual suspects are:

* types from `packages/contracts` instead of hand-mirrored copies
* explicit tenancy where today it is implicit
* no PHI beyond the anonymisation boundary
* secrets out of the code
* EU-region inference and storage
* `just` as the command facade

On a `Nuevo` this part is not "what changes" but "which of these rules it has to
satisfy from day one" — the same list, applied forward.

Mark destination paths `[nuevo]` when there is no origin, so a reviewer scanning the
board can see at a glance which tasks are moving code and which are writing it.

### `### Alcance`

`IN` is what lands in this package. `OUT` is what a reasonable reader would assume
is included but is not, and every `OUT` bullet names the `SOC-n` or task that owns
it:

```
OUT
* Codificación CIE-10 de los hallazgos -> SOC-154
* La comprobación end-to-end de que funciona -> SOC-65, verificación
```

`OUT` is the highest-yield section in the ticket. It is what stops an AI expanding
into adjacent code it can see in the origin repo, which is a live risk when the
origin module does more than this Feature needs.

An empty `OUT` is a Definition-of-Ready failure. If there is genuinely nothing
adjacent, write why in one line.

### `### Destino`

Monorepo paths from `repos.md`, one per line, each with what to do there. Include a
`Patrón a seguir:` line pointing at the nearest analogue already in the tree.

`[previsto]` while the package does not exist yet.

A path whose root is not in the `repos.md` table is always an error, marked or not.

### `### Criterios de aceptación`

Two kinds:

**1. The Feature's threshold, copied literally.** `<= 1,0 % sobre muestra gold >= 100
resúmenes`, not "una tasa baja". Paraphrasing loses the audit trail.

Copy it into the task where it is **observable**. The ≤ 1,0 % of SOC-1 cannot be
measured from `sdk`; there, name the task that carries it instead of restating a
number nobody can check from that package. A threshold copied where it cannot be
evaluated is a criterion that will be ticked without evidence.

**2. Parity with what already runs** — and this one depends on the kind:

| Kind | What parity means |
| --- | --- |
| `Porte` | Structural. The origin's own suites pass against the ported code **without changing their expectations**. Name the suite. |
| `Reescritura` | Behavioural only. The clinical cases, prompts and gold fixtures of the origin keep producing equivalent output. The origin's tests do **not** apply: the structure changed on purpose, so they are ported as cases, not as a suite. |
| `Nuevo` | There is nothing to be equal to. Drop parity and lean on the Feature's threshold plus its own tests. |

Parity is what makes a migration ticket checkable at all — without it, "portar el
resumen" has no failure condition. Demanding structural parity from a `Reescritura`
is the opposite failure: an unmeetable criterion, which is how a standard gets
quietly ignored.

What does **not** go here: the end-to-end proof that the whole capability works for
a doctor. That is the Verification, and putting it here duplicates evidence the
auditor will read in two places.

### `### Comprobación`

A `bash` block of `just` commands only, for *this* task — not the repo's full
battery. See the command contract in `repos.md`.

### `### Riesgos mitigados (ISO 14971)`

The `R-nn` codes the Feature declares, each with one line on how this task mitigates
it. Mandatory: this is the link between a risk in the file and the code that
addresses it.

If the Feature declares risks and this task mitigates none, say so — it usually
means the mitigation landed in a sibling task, and that one should cite it.

## Worked example — SOC-1

Feature `SOC-1 (RF-M1-001)`, *«Generar un resumen del paciente a partir de los datos
demográficos y clínicos del HIS, sin diagnósticos nuevos»*. Threshold: ungrounded
content ≤ 1,0 % over a gold sample of ≥ 100. Verification: `SOC-65`.

It decomposes into **four** tasks — `db`, `api`, `assistant`, `sdk` — because those
are the packages the capability lives in. Not thirty. Two are shown in full.

### `assistant: subagente de resumen sobre el deep agent`

The one that is **not** a port. Note the kind, the citation that authorises it, and
how parity changes shape because of it.

```markdown
### Requisito
SOC-1 (RF-M1-001)
Verificación: SOC-65

### Punto de partida
Reescritura.

De `sofia-assistants/src/agents/scribe/` — `graph.py`, `prompts.py`, `state.py`,
`tools.py`. Es lo que hoy genera el resumen en producción y es la referencia de
comportamiento.

Sobrevive el comportamiento: los prompts, las reglas de filtrado por relevancia, el
anclaje afirmación -> fuente y los casos clínicos de `tests/`. Se rehace la
estructura: el destino es un subagente del deep agent, no un grafo suelto, según la
arquitectura decidida en SOC-139. Sin esa decisión esto sería un porte.

Cambia al reescribirlo:
* Los tipos de contexto de paciente y de nota salen de `packages/contracts`
* El tenant viaja explícito en el estado, hoy va implícito en el cliente
* El núcleo del agente rechaza en frontera lo que llegue sin anonimizar (SOC-142)
* Sin claves en código: credenciales por configuración del despliegue

### Alcance
IN
* Subagente de resumen sobre el núcleo del deep agent
* Prompts y reglas de filtrado por relevancia traídos del scribe actual
* Generación por secciones con anclaje afirmación -> fuente
OUT
* Núcleo del deep agent y su serving -> SOC-139
* Codificación CIE-10 de los hallazgos -> SOC-154
* Detección de contradicciones entre fuentes -> SOC-152
* Suite gold que mide el umbral -> `evals`, tarea aparte
* La comprobación end-to-end con médico -> SOC-65, verificación

### Destino
* `apps/assistant/src/subagents/summary/` [previsto] — subagente nuevo
* `apps/assistant/src/core/agent.py` [previsto] — núcleo del que cuelga (SOC-139)
* Patrón a seguir: `apps/assistant/src/subagents/` [previsto]

### Criterios de aceptación
* Contenido no fundamentado <= 1,0 % sobre muestra gold >= 100 resúmenes
* 0 diagnósticos nuevos ni recomendaciones terapéuticas
* Paridad de comportamiento con `sofia-assistants`: los casos clínicos de su `tests/`
  se portan como casos y producen salida equivalente. La suite del origen no aplica
  tal cual: la estructura cambia a propósito
* Toda afirmación superviviente conserva su ancla a dato fuente

### Comprobación
```bash
just test assistant
just lint assistant && just typecheck assistant
```

### Riesgos mitigados (ISO 14971)
* R-01 — la lista fija que nunca descarta alergias, medicación activa ni
  antecedentes sobrevive a la reescritura: es una de las reglas que se traen
* R-02 — el anclaje afirmación -> fuente es condición del subagente, no un paso
  posterior que se pueda omitir
```

### `sdk: chat y resumen en el widget`

```markdown
### Requisito
SOC-1 (RF-M1-001) · SOC-31 (RF-M1-006)
Verificación: SOC-65

### Punto de partida
Porte.

De `sofia-sdk-core/core/src/modules/` — `chat`, `header`, `layout` e
`insertionPreview`. Es el widget que hoy usan los médicos dentro del HIS.

Se trae el render del chat y del resumen por secciones, con su procedencia y el
pop-up de inserción.

Cambia al traerlo:
* Los tipos de la respuesta salen de `packages/contracts`
* El transporte pasa al canal único de streaming (SOC-145), no un fetch por capacidad
* La anonimización y su restauración ocurren en el cliente (SOC-159)

### Alcance
IN
* Módulos de chat, cabecera, layout y previsualización de inserción
* Render del resumen por secciones con procedencia por afirmación
* Estados de carga, vacío y error
OUT
* Aviso de modo degradado y disclaimer -> SOC-155
* Panel de cobertura -> SOC-149
* La comprobación end-to-end con médico -> SOC-65, verificación

### Destino
* `packages/sdk/core/src/modules/chat/` [previsto] — destino del módulo de chat
* `packages/sdk/core/src/modules/summary/` [previsto] — render por secciones
* Patrón a seguir: `packages/sdk/core/src/modules/layout/` [previsto]

### Criterios de aceptación
* Cada afirmación expone su origen en <= 1 interacción
* Los tres estados (carga, vacío, error) tienen render propio y test
* Paridad con `sofia-sdk-core`: la suite `e2e/` del origen pasa contra el widget
  portado
* 0 regresiones de accesibilidad respecto al baseline del SDK actual

### Comprobación
```bash
just test sdk
just build sdk && just lint sdk
```

### Riesgos mitigados (ISO 14971)
* R-02 — la procedencia visible por afirmación es lo que permite al médico detectar
  una alucinación antes de actuar sobre ella
```

### The other two, in one line each

```
db:  esquema de chat, plantillas y fuentes  — de sofia-sdk-db/supabase/migrations
api: módulos de chat, plantillas y notas    — de sofia-api/src/app/modules/{chat-settings,
                                              chat-sources,lang-graph,templates,clinical-notes}
```

Note the pattern across the four: every `OUT` names the ticket that owns it, the
Verification appears in all of them because they serve the same Feature, and each
one has a parity criterion pointing at the origin repo's own tests. Those three
habits are what make a migration backlog auditable.
