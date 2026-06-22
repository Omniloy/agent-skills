# Agent Skills — autonomous, issue-driven software delivery

A small, battle-tested set of **[Claude Code](https://claude.com/claude-code) skills** for turning a product spec into shipped, reviewed code — with a backlog that always reflects reality. These are the skills we used to build the **SonIA** tax-assistant end-to-end (epics **E10–E14**, every PR driven through a Greptile 5/5 gate). The worked example that generated those issues is included under [`examples/sonia/`](examples/sonia/).

> A **skill** is a Markdown playbook (`SKILL.md`) — plus optional helper scripts and reference docs — that Claude Code loads on demand when you invoke it (`/skill-name`) or when a request matches its trigger. It encodes *how* to do a recurring job so the model does it the same, careful way every time.

## The workflow these skills compose

```
        PRD / spec
            │
            ▼
   /prd-to-issues ─────────► GitHub:  Milestone ─ Epic ─ sub-issues   (the standard structure)
            │                          (visual plan → approve → create + native-link)
            ▼
     /epic-loop ────────────► for each Epic:  branch → delegate impl to a subagent
            │  (+ /loop)                       → open ONE PR (Closes #…) → review gate
            │                                  → /review-pr until 5/5 → merge
            │                                  → KEEP THE BACKLOG TRUTHFUL (close subs, tick
            │                                    the Epic, close the Epic, advance the Milestone)
            ▼
   /visual-recap ──────────► publish an interactive architecture/feature recap of what shipped
```

- **`prd-to-issues`** creates the structure. **`epic-loop`** (paced by **`loop`**) implements it and keeps it updated. **`review-pr`** closes each review round. **`visual-recap`** documents the result.

## The skills

| Skill | What it does | Invoke |
| --- | --- | --- |
| **[`epic-loop`](skills/epic-loop/)** | Orchestrates an autonomous, Epic-by-Epic build on a **standard issue structure** (Milestone → Epic → sub-issues). Delegates each Epic's code to a subagent, opens one PR, drives the review-bot/CI gate to green, merges, and **keeps the backlog truthful** (closes sub-issues, ticks the Epic task-list, closes the Epic, advances the Milestone). Includes `scripts/backlog.py` (`epics` / `next` / **`audit`** / `review-status`). | `/epic-loop` |
| **[`jira-action-plan`](skills/jira-action-plan/)** | The deliberate, single-ticket counterpart to `epic-loop`, driven from **Jira** (via the Atlassian MCP). Fetches the issue + linked issues + comments, scans the current repo, surfaces real decisions with pros/cons via `AskUserQuestion`, drafts a step-by-step plan (always including tests) for approval, then implements pausing after each step so you can review the working tree. Never commits or pushes without explicit permission. Hands off to `/live-testing-plan` at the end. | `/jira-action-plan <KEY-or-URL>` |
| **[`prd-to-issues`](skills/prd-to-issues/)** | Reads a PRD, authors a structured `manifest.json`, renders a **visual plan** for human approval, then creates the Milestones + Epics + sub-issues + labels + **native sub-issue links** on GitHub via `gh` (idempotent). Every sub-issue carries a **Functional** and **Technical** section + acceptance criteria. | `/prd-to-issues` |
| **[`review-pr`](skills/review-pr/)** | Fetches a PR's inline + summary review comments, classifies each (valid / partial / not valid), presents a plan, applies minimal fixes, replies on GitHub per comment with the fix commit, and re-requests review. | `/review-pr <n>` |
| **[`visual-recap`](skills/visual-recap/)** | Builds an interactive, annotatable **Agent-Native Plan** from work — diagrams, wireframes, `data-model`/ERD, `api-endpoint` specs, file-tree — and publishes it (never inline). Great for architecture reviews and handoffs. | `/visual-recap` |
| **`loop`** *(built-in)* | `/loop [interval] <prompt>` — schedules a recurring or **self-paced** prompt. In dynamic mode it runs the task now, then uses `ScheduleWakeup` to re-fire (short while polling a review, long while a background agent works). This is what lets `epic-loop` run autonomously. Built into Claude Code; documented here for completeness. | `/loop` |

## The issue structure (the contract)

Every body of work is modelled with the **same** three levels, so the backlog is consistent and machine-readable:

```
Milestone  "E<N> — <theme>"          ← one per batch/phase; carries the due-date / ramp intent
└─ Epic    issue, label: epic        ← "E<N> · Epic — <title>"; body has a - [ ] #n task-list
   ├─ Sub-issue  "E<N>.1 · <title>"   ← a real GitHub sub-issue (native link + task-list box)
   └─ Sub-issue  "E<N>.2 · <title>"
```

- Every Epic belongs to **exactly one** Milestone; every sub-issue is on the **same** Milestone as its Epic.
- Sub-issues are linked **natively** (`repos/{o}/{r}/issues/{epic}/sub_issues`) **and** kept as a `- [ ] #n` task-list in the Epic body.
- The `E<N>.<M>` numbering, the `epic` label, and the Milestone assignment are load-bearing — the tooling relies on them.

## Keeping the backlog truthful

`epic-loop` updates issues at **every** transition, not "later":

1. Merged PR `Closes #<n>` → **verify** each sub-issue closed.
2. **Tick** the Epic's task-list boxes (`- [ ] #n` → `- [x] #n`).
3. **Close the Epic** when all its sub-issues are done.
4. **Close/advance the Milestone** when its Epics are done.

Run a mechanical audit anytime:

```bash
python3 skills/epic-loop/scripts/backlog.py audit --repo <owner/name>
```

It flags exactly the "forgot to update it" cases — Epics done-but-still-open, Milestones done-but-still-open, and closed sub-issues whose Epic checkbox is unticked — each with the `gh` command to fix it.

## Installing the skills

Claude Code loads skills from `~/.claude/skills/` (user-level) or `.claude/skills/` (project-level). To install all of them at the user level:

```bash
git clone https://github.com/Omniloy/agent-skills
cp -R agent-skills/skills/* ~/.claude/skills/
# then in Claude Code:  /epic-loop   /prd-to-issues   /review-pr   /visual-recap
```

Or copy a single skill (e.g. just the issue workflow):

```bash
cp -R agent-skills/skills/{prd-to-issues,epic-loop,review-pr} ~/.claude/skills/
```

### Prerequisites

- **Claude Code** with the Skill tool.
- **`gh`** (GitHub CLI) authenticated — `gh auth status` — for `prd-to-issues`, `epic-loop`, and `review-pr`.
- A **review bot** (e.g. Greptile / CodeRabbit) installed on the repo, or CI, if you want `epic-loop` to drive a quality gate. The default gate detection is Greptile (see `skills/epic-loop/references/review-gates.md`).
- For `jira-action-plan`: the **Atlassian MCP** connector connected (`getAccessibleAtlassianResources`, `getJiraIssue`, etc.) — used to fetch the issue, linked issues, comments, and optionally post a wrap-up comment.
- For `visual-recap`: the **Agent-Native Plan** MCP connector (`plan`) connected.

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
