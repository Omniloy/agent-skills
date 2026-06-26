# Agent Skills — autonomous, issue-driven software delivery

A small, battle-tested set of **[Claude Code](https://claude.com/claude-code) skills** for turning a ticket (or a whole spec) into shipped, reviewed code — with a backlog that always reflects reality. These are the skills we used to build the **SonIA** tax-assistant end-to-end (epics **E10–E14**, every PR driven through a Greptile 5/5 gate). The worked example that generated those issues is included under [`examples/sonia/`](examples/sonia/).

> A **skill** is a Markdown playbook (`SKILL.md`) — plus optional helper scripts and reference docs — that Claude Code loads on demand when you invoke it (`/skill-name`) or when a request matches its trigger. It encodes *how* to do a recurring job so the model does it the same, careful way every time.

## How the skills are organized

The skills live in three tiers under [`skills/`](skills/), by their role in the delivery lifecycle:

```
skills/
  core/      ← the single-ticket loop every developer uses
    ship             implement a ticket end-to-end, drive its PR to 5/5, recap it
    review-pr        take any PR to green: Greptile 5/5 + CI/CD + human/Supabase comments
    visual-recap     publish an interactive recap of what shipped
  test/      ← verification
    live-testing-plan   design + run a live/QA verification plan for a ticket
    agent-eval-api      run agent evals on the Omniloy Agent Testing Platform (optional)
  backlog/   ← creating & orchestrating work (tech-lead / planning)
    create-jira-work-items   file the team's Feature + Dev + Verification triad in Jira
    prd-to-issues            turn a PRD into GitHub Epics + sub-issues
    epic-loop                build a whole backlog Epic-by-Epic, autonomously
```

**`core/` is the must-have set for any developer.** `test/` you add when you own verification (`agent-eval-api` only if you run agent evals). `backlog/` is for whoever plans or drives the project.

## The single-ticket flow (the everyday path)

```
   Jira KEY / idea
        │
        ▼
     /ship  (+ /loop)
        │
        ├─ ACT 1  kickoff (attended) ── close gaps (visual questionnaire)
        │                            └─ write update / create the ticket (create-jira-work-items)
        │                            └─ visual plan (approve)  ← last human gate
        ├─ ACT 2  build + gate (unattended) ── implement → ONE PR vs BASE
        │                                    └─ review-pr loop → Greptile 5/5 + CI/CD green + comments cleared
        └─ ACT 3  closeout ── squash → /visual-recap → PR body carries plan + recap links → stop (no merge)
                                    │
                                    ▼  (verify the Verification ticket)
                            /live-testing-plan
```

- **`ship`** is the "approve the plan and walk away" path. It composes `create-jira-work-items` (when it has to create a ticket), `visual-plan` (the approval surface), the **`review-pr` loop** (the 5/5 + CI/CD gate), and `visual-recap` (the closeout) into one autonomous run.
- **`review-pr`** is also a standalone skill: point it at any PR and it resolves everything that PR carries — Greptile score, failing CI/CD checks, and human/Supabase comments.
- **`live-testing-plan`** proves the change works against a running system — and it's how the **Verification** ticket that `create-jira-work-items` files gets executed.

## The backlog flow (planning & orchestration)

```
        PRD / spec
            │
            ▼
   /prd-to-issues ─────────► GitHub:  Milestone ─ Epic ─ sub-issues   (the standard structure)
            │                          (visual plan → approve → create + native-link)
            ▼
     /epic-loop ────────────► for each Epic:  branch → delegate impl to a subagent
            │  (+ /loop)                       → open ONE PR (Closes #…) → review gate
            │                                  → /review-pr until it passes → merge
            │                                  → KEEP THE BACKLOG TRUTHFUL (close subs, tick
            │                                    the Epic, close the Epic, advance the Milestone)
            ▼
   /visual-recap ──────────► publish an interactive architecture/feature recap of what shipped
```

## The skills

| Tier | Skill | What it does | Invoke |
| --- | --- | --- | --- |
| core | **[`ship`](skills/core/ship/)** | The **single-ticket, end-to-end conductor**. Front-loads every human decision (close information gaps with a **visual questionnaire**, write the update into the Jira ticket — or **create** the Feature+Dev+Verification triad via `create-jira-work-items` — with approval, then a **visual plan** you sign off), then runs **unattended**: implements the plan, opens one PR against the integration branch, and drives it to **Greptile 5/5 + CI green** (reusing the `review-pr` loop), squashes to one commit, publishes a **visual recap**, and threads the **plan + recap links** into the PR body. Never merges. | `/ship <KEY-or-text>` |
| core | **[`review-pr`](skills/core/review-pr/)** | The comprehensive, **autonomous** PR resolver. Resolves *everything* a PR carries: drives a **Greptile** review to 5/5, fixes failing **CI/CD checks (GitHub Actions)**, and addresses **human reviewers + other bots (incl. Supabase advisors/lints)** — verifying each against the actual code, applying minimal fixes, replying per comment, resolving threads, re-requesting review. **single** (one pass) or **loop** (self-paced to green, capped at N rounds). Never asks for fix-approval; an ambiguous item is the only escalation. | `/review-pr <n> [single\|loop]` |
| core | **[`visual-recap`](skills/core/visual-recap/)** | Builds an interactive, annotatable **Agent-Native Plan** from work — diagrams, wireframes, `data-model`/ERD, `api-endpoint` specs, file-tree, annotated diffs — and publishes it (never inline). Great for architecture reviews and handoffs. | `/visual-recap` |
| test | **[`live-testing-plan`](skills/test/live-testing-plan/)** | Designs a live/QA **verification plan** for a Jira issue: locks the acceptance criteria, asks who runs the tests (Claude against a live server / the Omniloy **evals platform** / the user manually), drafts a mode-tailored plan with setup + step-by-step cases each carrying its own AC, executes when Claude is the runner, and delivers a ✅/⛔ results table with per-AC coverage. Executes the **Verification** ticket that `create-jira-work-items` files. | `/live-testing-plan <KEY-or-URL>` |
| test | **[`agent-eval-api`](skills/test/agent-eval-api/)** | Operates the Omniloy **Agent Testing Platform** API end-to-end: authenticates, resolves or creates agents / personas / evaluators / test configs (reuse-before-create, `AI_generated-` prefix, shared-vs-owned hygiene), runs pre-flight checks, launches a test run, polls it to a terminal state, and reads back transcripts + `score`/`passed`. The **execution half** that `/live-testing-plan` (evals mode) hands its spec off to. *(Optional — only if you run agent evals.)* | `/agent-eval-api` |
| backlog | **[`create-jira-work-items`](skills/backlog/create-jira-work-items/)** | Files the team's **mandatory Jira structure** for any new work: a **Feature** (pinned to an existing Epic) + a linked **Dev task** (`Tarea`) + an **independent Verification** (assigned to someone other than the dev), children linked with `Relates` (not sub-tasks). Resolves site/project/epics/people at runtime, checks for duplicates, drafts everything for approval before creating, then creates Feature → Dev → Verification in order. The Jira intake standard a lone `createJiraIssue` would violate. | `/create-jira-work-items` |
| backlog | **[`prd-to-issues`](skills/backlog/prd-to-issues/)** | Reads a PRD, authors a structured `manifest.json`, renders a **visual plan** for human approval, then creates the Milestones + Epics + sub-issues + labels + **native sub-issue links** on GitHub via `gh` (idempotent). Every sub-issue carries a **Functional** and **Technical** section + acceptance criteria. | `/prd-to-issues` |
| backlog | **[`epic-loop`](skills/backlog/epic-loop/)** | Orchestrates an autonomous, Epic-by-Epic build on the **standard issue structure** (Milestone → Epic → sub-issues). Delegates each Epic's code to a subagent, opens one PR, drives the review-bot/CI gate to green (via `/review-pr`), merges, and **keeps the backlog truthful** (closes sub-issues, ticks the Epic task-list, closes the Epic, advances the Milestone). Includes `scripts/backlog.py` (`epics` / `next` / **`audit`** / `review-status`). | `/epic-loop` |
| — | **`loop`** *(built-in)* | `/loop [interval] <prompt>` — schedules a recurring or **self-paced** prompt. In dynamic mode it runs the task now, then uses `ScheduleWakeup` to re-fire (short while polling a review, long while a background agent works). This is what lets `ship`, `review-pr` (loop mode), and `epic-loop` run autonomously. Built into Claude Code; documented here for completeness. | `/loop` |

## The issue structure (the contract)

`prd-to-issues` and `epic-loop` model every body of work with the **same** three levels, so the backlog is consistent and machine-readable:

```
Milestone  "E<N> — <theme>"          ← one per batch/phase; carries the due-date / ramp intent
└─ Epic    issue, label: epic        ← "E<N> · Epic — <title>"; body has a - [ ] #n task-list
   ├─ Sub-issue  "E<N>.1 · <title>"   ← a real GitHub sub-issue (native link + task-list box)
   └─ Sub-issue  "E<N>.2 · <title>"
```

- Every Epic belongs to **exactly one** Milestone; every sub-issue is on the **same** Milestone as its Epic.
- Sub-issues are linked **natively** (`repos/{o}/{r}/issues/{epic}/sub_issues`) **and** kept as a `- [ ] #n` task-list in the Epic body.
- The `E<N>.<M>` numbering, the `epic` label, and the Milestone assignment are load-bearing — the tooling relies on them.

(For **Jira** work the contract is different — the Feature + Dev + Verification triad — and `create-jira-work-items` owns it.)

## Keeping the backlog truthful

`epic-loop` updates issues at **every** transition, not "later":

1. Merged PR `Closes #<n>` → **verify** each sub-issue closed.
2. **Tick** the Epic's task-list boxes (`- [ ] #n` → `- [x] #n`).
3. **Close the Epic** when all its sub-issues are done.
4. **Close/advance the Milestone** when its Epics are done.

Run a mechanical audit anytime:

```bash
python3 skills/backlog/epic-loop/scripts/backlog.py audit --repo <owner/name>
```

It flags exactly the "forgot to update it" cases — Epics done-but-still-open, Milestones done-but-still-open, and closed sub-issues whose Epic checkbox is unticked — each with the `gh` command to fix it.

## Installing the skills

Claude Code loads skills from `~/.claude/skills/` (user-level) or `.claude/skills/` (project-level), where each skill is a directory containing a `SKILL.md`. The `core/` `test/` `backlog/` folders here are for **organizing the repo** — install the skills **flat** into `~/.claude/skills/`:

```bash
git clone https://github.com/Omniloy/agent-skills
cp -R agent-skills/skills/core/*    ~/.claude/skills/
cp -R agent-skills/skills/test/*    ~/.claude/skills/
cp -R agent-skills/skills/backlog/* ~/.claude/skills/
# then in Claude Code:  /ship   /review-pr   /visual-recap   /live-testing-plan   /create-jira-work-items   /prd-to-issues   /epic-loop
```

Or install just one tier (e.g. the everyday developer set):

```bash
cp -R agent-skills/skills/core/* ~/.claude/skills/
```

### Prerequisites

- **Claude Code** with the Skill tool.
- **`gh`** (GitHub CLI) authenticated — `gh auth status` — for `ship`, `review-pr`, `prd-to-issues`, and `epic-loop`.
- A **review bot** (e.g. Greptile / CodeRabbit) and/or CI on the repo if you want `review-pr` / `ship` / `epic-loop` to drive a quality gate. The default gate detection is Greptile (see `skills/backlog/epic-loop/references/review-gates.md`).
- For `ship`, `live-testing-plan`, and `create-jira-work-items`: the **Atlassian MCP** connector connected (`getAccessibleAtlassianResources`, `getJiraIssue`, `createJiraIssue`, `createIssueLink`, etc.) — used to fetch/create issues, linked issues, comments, and optionally post a wrap-up comment.
- For `live-testing-plan` evals mode: the **Omniloy `agent-eval-api`** skill installed and reachable — `live-testing-plan` only produces the spec; `agent-eval-api` executes it.
- For `visual-recap` (and `ship`'s plan/recap): the **Agent-Native Plan** MCP connector (`plan`) connected.
- For `ship`: all of the above together, plus a **Greptile** review bot on the repo. Run it under **`/loop`** (`/loop /ship <ticket>`) so the Act-2 gate can self-pace.

## Worked example — SonIA

[`examples/sonia/`](examples/sonia/) is the real input/output of one `prd-to-issues` run on the SonIA project:

- `Memoria_tecnica_SUMA.docx` — the source PRD (a public-tender technical memoria, Expte. 2/PA/SER/26).
- `manifest.json` — the structured plan `prd-to-issues` authored from it (labels, milestones, epics, sub-issues with Functional/Technical/acceptance).
- `plan.html` — the rendered visual plan a human approved before any issue was created.
- `created.json` — the idempotency map of what was created (titles → issue numbers/URLs).

See [`examples/sonia/README.md`](examples/sonia/README.md) for how it maps end-to-end.

## License

MIT — see [`LICENSE`](LICENSE).

---

*Built with [Claude Code](https://claude.com/claude-code). The skills here are productized versions of loops we ran by hand; the SonIA example shipped E10–E14 to `main` through a Greptile 5/5 gate.*
