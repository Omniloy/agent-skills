# Worked example — SonIA (PRD → issues → shipped)

This folder is the **real input and output** of one `/prd-to-issues` run on the SonIA tax-assistant, so you can see the whole pipeline grounded in actual artifacts.

## The files

| File | Stage | What it is |
| --- | --- | --- |
| `Memoria_tecnica_SUMA.docx` | **input** | The source PRD — a public-tender technical *memoria* (Expte. 2/PA/SER/26) describing the SonIA system: the verifiable-answers RAG agent, multilingual chat/voice, the admin console, supervised learning, security/RGPD, deployment phases. |
| `manifest.json` | **plan (structured)** | What `prd-to-issues` authored *from* the PRD: `labels`, `milestones` (the project phases), `epics[]`, and each Epic's `issues[]` carrying `functional_md`, `technical_md`, `acceptance[]`, and `prd_refs[]` back to the doc. This is the single source of truth that drives both the visual plan and the GitHub issues. |
| `plan.html` | **plan (visual)** | The self-contained visual plan rendered from the manifest — diagrams + per-Epic narrative + every sub-issue's Functional/Technical sections. This is the artifact a human **approved** before anything was created on GitHub. |
| `created.json` | **output** | The idempotency map written after creation: issue **titles → numbers/URLs**, so re-running is safe and the hierarchy is recoverable. |

## How it maps to the structure

`prd-to-issues` turned the PRD into the standard three-level shape (see the repo root README):

```
Milestone  "Fase 1 — Planificación y conocimiento"   ← a PRD phase
└─ Epic    "E1 · Epic — …"   (label: epic, body = - [ ] #n task-list)
   ├─ Sub-issue  "E1.1 · …"   (Functional + Technical + acceptance, native-linked)
   └─ …
```

…and the later feedback batches (during the build) added milestones **E10–E14** with their epics and sub-issues the same way.

## What happened next

`/epic-loop` (paced by `/loop`) then implemented these Epics one at a time — delegating each Epic's code to a subagent, opening one PR per Epic with `Closes #<n>` for every sub-issue, driving the **Greptile 5/5** gate via `/review-pr`, merging, and keeping the backlog updated. Epics **E10–E14** shipped to `main` this way.

## Reproduce it

```bash
# 1. render the plan from the manifest (local, safe) and review it
python3 ../../skills/prd-to-issues/scripts/prd_issues.py render \
  --manifest manifest.json --out plan.html

# 2. dry-run the GitHub creation (prints exactly what it would create)
python3 ../../skills/prd-to-issues/scripts/prd_issues.py create \
  --manifest manifest.json --repo <owner/name> --dry-run

# 3. (with approval) create the milestones + epics + sub-issues + labels + links
python3 ../../skills/prd-to-issues/scripts/prd_issues.py create \
  --manifest manifest.json --repo <owner/name> --apply

# 4. then drive the build:  /epic-loop   (and audit hygiene anytime)
python3 ../../skills/epic-loop/scripts/backlog.py audit --repo <owner/name>
```

> The PRD is included with the project owner's permission as an illustrative example of the kind of spec these skills consume.
