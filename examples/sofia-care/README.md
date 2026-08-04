# Worked example — Sofia Care (Jira backlog + repo review → issues plan)

This folder is a `/prd-to-issues` run for **Sofia Care**, the next generation of Omniloy's
clinical-assistant product — built **greenfield** in a monorepo (SDK · API · DB · transcriber ·
single assistant · tests/evals). Nothing from the current codebase is ported: everything is
rethought and rebuilt from zero.

Unlike [`../sonia/`](../sonia/), the "PRD" here is two sources combined:

1. **The Jira SOC backlog** (`omniloy.atlassian.net`, project SOC, board 269) — **64 Features**
   in priority order (SOC-1 → SOC-64) plus **64 mirror Verifications** (SOC-65…128). They are
   written as medical-product requirements: each carries a user story, **quantitative acceptance
   criteria** (e.g. "ungrounded content ≤ 1 % over a gold sample of ≥ 100 summaries") and
   **ISO 14971 risk references**. This is the source of truth for scope and priority.
2. **A technical review of the 5 existing repos** (five parallel exploration agents) —
   `sofia-sdk-core`, `sofia-api`, `sofia-transcriber`, `sofia-sdk-db`,
   `sofia-assistants`. These serve as the **conceptual base only**: which designs to keep as
   reference (the STT provider/fallback chain, the client/server anonymization mirror, the
   tenancy model), and which debt not to repeat (copy-based builds, flat-root layouts,
   duplicated wire contracts).

Decisions fixed by the team before authoring:

- **Everything is built from 0** — no code migration, no git-history import.
- **One assistant, built with deepagents** — the four LangGraph graphs are replaced by a single
  deep agent; clinical guardrails (input classification, diagnosis OFF, EU-only inference,
  PHI-free traces) live in its core. **PII anonymization runs client-side in the SDK** (E6.6,
  SOC-21) — the backend only ever sees anonymized data, with a defensive edge check (E3.4).
- **The SDK keeps its current design** (look & feel, react + web-component pattern) but with new
  code and targeted improvements — starting with a **redesigned insertion/verification pop-up**
  (SOC-18, SOC-23).
- **SDK source stays private**: development happens in the monorepo; a **new repo created only
  for SDK releases** receives builds + changelog + README + dependency manifest via an
  **automated PR on each release build**; **publication is manual** after human review (E6.5).
- **Jira breakdown, no subtasks**: each Feature (SOC-1…64) gets linked technical **"Tarea"**
  issues and **"Verificación"** issues (`Relates` links, never subtasks — the team's
  `create-jira-work-items` pattern). This plan's sub-issues are the source for the technical
  Tareas (each cites the SOC Features it implements); the existing Verifications (SOC-65…128)
  link to their Feature and are executed by the E11 evals machinery.
- **The database is new, on self-hosted Supabase** (decided); the E1.1 spike only validates
  that all functions and crons migrate cleanly (edge functions, pg_cron/pg_net) and defines the
  self-hosted operating model (deploy, backups, upgrades, EU residency).
- **User login via JWT or SSO (OIDC)** is added — open spike `[needs decision]`.

## The files

| File | Stage | What it is |
| --- | --- | --- |
| `manifest.json` | **plan (structured)** | `labels`, 5 `milestones` (Fase 0–4), 12 `epics` (E0–E11), 50 sub-issues with `functional_md`, `technical_md`, `acceptance[]` (reusing the Jira quantitative thresholds) and `prd_refs[]` citing `SOC-nn (RF/RNF-…)` keys plus reference-repo paths. |
| `plan.html` | **plan (visual)** | The self-contained visual plan: architecture diagram, Jira-block → epic mapping table, target file-tree, new data model, deep-agent mermaid, and every sub-issue expanded. **The artifact a human reviews and approves before anything is created on GitHub.** |
| *(created.json)* | **output — not yet** | Written by the create step after approval (idempotency map titles → issue numbers/URLs). |

## The plan shape

```
Fase 0 — Fundación                E0 monorepo/contracts/CI · E1 new DB + tenancy + retention · E2 auth (JWT/SSO spike)
Fase 1 — Resumen y asistente      E3 deep-agent core+guardrails · E4 patient summary (SOC-1!) · E5 clinical safeguards · E6 SDK widget
Fase 2 — Documentación y guías    E7 note editing + redesigned pop-up · E8 clinical-guidelines chat
Fase 3 — Transcripción            E9 new realtime transcription (roles, disconnects, final version, consent, quality gates)
Fase 4 — Integración y vigilancia E10 HIS contract + orders + staging validation · E11 evals/bias/post-market (runs the 64 Verifications)
```

12 epics · 52 sub-issues · 5 milestones · 20 labels.

## The `.claude/` harness (loops-first development)

[`.claude/`](.claude/) is the **team-visible Claude Code harness** authored for the monorepo —
copy it as-is to the root of `Omniloy/sofia-care` when the repo exists. It is designed so the
monorepo is **programmed with loops**, composing with this repo's delivery skills
(`/ship`, `/epic-loop`, `/review-pr`, `/prd-to-issues`, `/create-jira-work-items`,
`/visual-recap`, `/release-title-changelog`, `/live-testing-plan`, `/agent-eval-api`):

- **8 project skills** — `quality-gate` (affected-packages static gate), `verify-change`
  (real smoke, required by the Stop gate), `run-stack` (full local stack with mocks),
  `add-contract` (contract-first change flow), `new-migration` (Supabase + mandatory RLS test),
  `soc-task` (SOC Feature → linked Tarea → `/ship`), `sdk-release` (automated PR to the
  releases repo, manual publication), `isolated-run` (disposable worktree for bypass runs).
- **4 hooks** — `guard.py` (deny secrets/PHI-fixtures/push-to-main · ask external/destructive),
  `format_fix.sh` (per-language auto-format), `contracts_codegen.sh` (regenerate types on
  schema edits), `require_checks.sh` (Stop gate on affected packages, loop-safe via
  `mark_verified.sh`).
- **6 agents** — `change-reviewer`, `medical-safety-reviewer` (SOC/ISO-14971 checklist),
  `deepagent-dev`, `sdk-dev`, `transcriber-dev`, `test-author` — the delegates `/ship` and
  `/epic-loop` use for implementation and pre-PR review.

See [`.claude/README.md`](.claude/README.md) for the full workflow.

## Reproduce / continue it

```bash
# 1. re-render the plan from the manifest (local, safe) and review it
python3 ../../skills/backlog/prd-to-issues/scripts/prd_issues.py render \
  --manifest manifest.json --out plan.html

# 2. dry-run the GitHub creation (prints exactly what it would create)
python3 ../../skills/backlog/prd-to-issues/scripts/prd_issues.py create \
  --manifest manifest.json --repo Omniloy/sofia-care --dry-run

# 3. (with approval, once the target repo exists) create milestones + epics + sub-issues + labels
python3 ../../skills/backlog/prd-to-issues/scripts/prd_issues.py create \
  --manifest manifest.json --repo Omniloy/sofia-care --apply

# 4. then drive the build:  /epic-loop   (and audit hygiene anytime)
python3 ../../skills/backlog/epic-loop/scripts/backlog.py audit --repo Omniloy/sofia-care
```

> Open questions (DB platform, JWT vs SSO, deep-agent serving, EU STT providers, HIS integration
> levels, regulatory framing…) are in the manifest's `open_questions` and rendered at the bottom
> of `plan.html` — resolve the `[needs decision]` ones during Fase 0.
