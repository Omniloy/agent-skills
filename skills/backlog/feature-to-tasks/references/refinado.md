# Refining a live ticket

A task written before `apps/api` exists will not be its own final version. It gets
enriched when the package appears, when the implementation plan finds something,
when a PR review shows the approach was wrong. The standard has to support that or
it gets abandoned at the third stale ticket.

Two rules make it work.

## 1. The description is the current agreed state, not a log

Rewrite the nine sections to reflect today's truth. Do not append.

A description that grows by accumulation — `Update 12/03`, `Update 19/03`,
`Nota: lo de arriba ya no aplica` — stops being readable for the AI, which is
exactly what the standard exists to prevent. The reader (human or model) should
never have to reconstruct the current spec from a diff of past states.

Never add a `### Historial` section. There are nine sections and that is not one.

## 2. The history lives in Jira comments

Comments already carry author and timestamp for free. Every description change
leaves one:

```
Refinado: Puntos de entrada

* apps/api/src/routes/summary.ts — [previsto] retirado, ruta verificada contra el árbol
* añadido apps/api/src/middleware/tenant.ts — el endpoint necesita el guard multi-tenant

Motivo: E0 mergeado (PR #12); el paquete ya existe.
```

Short, specific, says what moved and why.

## The guardrail: enrich detail, never widen scope

This is the failure mode of live tickets. *"Since we're updating it anyway, let's
also add this other thing."* The ticket grows, stops fitting in one PR, and the AI
ends up chasing a moving target.

**Operational rule: if the change adds a bullet to `IN`, it is not an update — it is
a new task.**

Refining means the nine sections describe *the same work* better:

| Allowed | Not allowed |
| --- | --- |
| Sharper paths; `[previsto]` removed | A new deliverable in `IN` |
| A contract closed that was open | A second package pulled in without justification |
| A corrected `Enfoque técnico` | A threshold loosened to match what got built |
| A threshold inherited from the Feature | Acceptance criteria deleted because they failed |
| `Blocks` links that were missing | Moving an `OUT` bullet into `IN` |

Moving an item from `OUT` to `IN` is the same violation wearing a hat: `OUT` bullets
name the `SOC-n` that owns them, so promoting one silently steals another ticket's
scope.

When you hit a genuine widening, say so and offer the alternative:

> Esto amplía el `IN` de SOC-201 (añade la codificación SNOMED). No lo meto ahí —
> corresponde a SOC-13. ¿Creo una tarea nueva `assistant: codificación CIE-10 de
> hallazgos` enlazada a SOC-13?

Loosening a threshold to match the implementation is the most damaging version in a
regulated product: the threshold came from the Feature, the Feature came from the
requirement, and the requirement is what gets audited. If the implementation cannot
meet it, that is a finding for product, not an edit to the ticket.

## Triggers

| Trigger | Section touched | Detected by |
| --- | --- | --- |
| The package now exists (E0 merged) | `Puntos de entrada` — drop `[previsto]`, validate paths | `check_ready.py --audit` |
| The implementation plan finds a file or dependency that was missing | `Puntos de entrada`, sometimes `Alcance` | The AI, during planning — back to the ticket before coding |
| PR review shows the approach was wrong | `Enfoque técnico` | Human, at PR review |
| A shape in `packages/contracts` changes | `Contrato` of consumers, plus new `Blocks` | Whoever changes the contract |
| The Feature is clarified or its threshold moves | `Requisito`, `Criterios de aceptación` | Product — propagate to every task citing it |
| The `justfile` gains or renames recipes | `Comprobación` | `check_ready.py --audit` |

## How the skill applies a refine

1. Fetch the issue (`description`, `labels`, `issuelinks`, `parent`, `comment`).
2. Parse the nine sections. A ticket that does not match the template is reported,
   not silently reformatted — it may predate the standard and deserves a human look.
3. Build the new version from what has been learned.
4. **Diff it section by section** and show only the sections that changed.
5. Ask for approval. One `AskUserQuestion`: Apply / Edit / Cancel.
6. On approval: `editJiraIssue` with the full new description, then
   `addCommentToJiraIssue` with the change note.

Never step 6 without step 5. A silent description rewrite destroys the reviewer's
ability to trust the board, which is worth more than any single correction.
