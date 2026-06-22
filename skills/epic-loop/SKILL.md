---
name: epic-loop
description: Autonomously implement a backlog Epic-by-Epic with a STANDARD issue structure (Milestone → Epic → sub-issues) and strict status hygiene. Reads the project's GitHub Milestones/Epics/sub-issues (or a PRD doc), asks the key clarifying questions once, then runs a self-paced loop that for each Epic implements its sub-issues (code + tests + real E2E), opens one PR, drives the review-bot/CI gate (e.g. Greptile) via /review-pr until it passes, merges, and KEEPS THE BACKLOG TRUTHFUL (closes sub-issues, ticks the Epic task-list, closes the Epic, advances the Milestone). Triggers on - "run the epic loop", "start the build loop", "implement the epics/issues", "ship the backlog", "build out the features one by one", "autonomously work through the issues".
---

# epic-loop

A reusable orchestration playbook: turn a backlog of **Milestones → Epics → sub-issues** into merged code, one Epic at a time, with a review gate, human-veto surfaces, and a backlog that always reflects reality. This is the productized version of a loop already run by hand — it encodes the structure, decisions, guardrails, and the review-bot "done" detection so it works in any repo.

You (the model) are the **orchestrator**. You do not write all the code inline — you delegate each Epic's implementation to a coding subagent, then handle PR + review + merge + sequencing + **issue bookkeeping** yourself, pacing the whole thing with `/loop` (dynamic mode) + `ScheduleWakeup`.

## 0. Preconditions

- A git repo with a GitHub remote and `gh` authenticated (`gh auth status`). If not in a repo, stop and say so.
- A backlog in the **standard structure** below (§1). If it isn't — a PRD/features doc, or loose issues with no milestone/epic hierarchy — do NOT improvise: run `/prd-to-issues` first (it creates exactly this structure), or normalise the existing issues into it (§1), THEN start the loop.
- Discover the backlog before asking anything:
  `python3 <skill>/scripts/backlog.py epics --repo <owner/name>` → epics with their sub-issues + states.

## 1. The issue structure (the contract) — ALWAYS the same shape

Every body of work is modelled with the SAME three-level hierarchy. Do not deviate; consistency is the point.

```
Milestone  "E<N> — <theme>"            ← one per batch/phase; carries the due-date/ramp intent
└─ Epic    issue, label: epic          ← "E<N> · Epic — <title>"; one per milestone (usually)
   ├─ Sub-issue  "E<N>.1 · <title>"     ← the unit of implementation; a real GitHub sub-issue
   ├─ Sub-issue  "E<N>.2 · <title>"
   └─ …
```

Rules that make it consistent and machine-readable:

- **Milestone** — every Epic is assigned to **exactly one** milestone, and **every sub-issue is assigned to the SAME milestone as its Epic**. Title `E<N> — <theme>`; description states scope + the date/ramp/cleanup intent. Create it first (`gh api repos/<O/R>/milestones -f title=… -f description=…`).
- **Epic** — a GitHub issue labelled `epic` (+ an `area:*` label). Title `E<N> · Epic — <title>`. Its body MUST contain a `- [ ] #<n>` **task-list** of its sub-issues (the human-visible hierarchy + the checkbox surface you tick as work lands).
- **Sub-issues** — one issue per implementable unit. Title `E<N>.<M> · <title>`. Each body has a **Functional** section (what/why + acceptance criteria) and a **Technical** section (files/endpoints/risks) — the `/prd-to-issues` contract. Labelled `type:*` + `area:*`, assigned to the Epic's milestone.
- **Linking** — link each sub-issue to its Epic as a **native GitHub sub-issue** (REST: `gh api repos/<O/R>/issues/<epic>/sub_issues -F sub_issue_id=<child REST id>` — note `-F` for the integer id, and the id is the issue's `.id`, not its number), AND keep the `- [ ] #n` task-list in the Epic body as the always-visible fallback. Use both.
- **IDs/refs are load-bearing.** The `E<N>.<M>` numbering, the `epic` label, the milestone assignment, and the native links are what `backlog.py` and you rely on to know what's next and what's done. Keep them exact.

When creating issues, prefer `/prd-to-issues` (renders a plan for approval, then creates milestones + epics + sub-issues + labels + native links idempotently, writing a `created.json` map). When normalising existing issues by hand, create the milestone, then the epic, then the sub-issues, then link them — in that order — so nothing is orphaned.

## 2. Ask the clarifying questions ONCE (before any code)

Use `AskUserQuestion` (batch up to 4 per call, 2 calls max). These are the forks whose answer changes the loop — present a recommended option first. Skip any the user already specified. Record the answers in the loop state file (§5).

1. **PR scope** — one PR per sub-issue · one PR per Epic · **one PR per Epic with stacked per-sub-issue commits** (recommended: review can go commit-by-commit).
2. **Order & start** — Epic order (e.g. issue/milestone order / dependency order) and which Epic to start with; whether to run continuously or pause after each Epic.
3. **Review gate + tool** — what makes a PR mergeable and which tool addresses comments. Options: a **review bot** (Greptile / CodeRabbit) + `/review-pr` until it scores clean; **CI green + no open comments**; **human review**. NOTE: you cannot self-launch billed cloud reviews (e.g. `/code-review ultra`) — only gates you can actually drive in a loop.
4. **Merge authority + repos** — auto-merge to the default branch on which repos? Are any repos/dirs **off-limits** (consume-only)? (If a change is unavoidable there, open that PR and hand it to the human instead of merging.)
5. **Test realism** — real E2E locally (live model + dockerized deps) with a named cheap model · add a key as a CI secret · fully mocked. Confirm the model id(s) and that E2E cost is acceptable.
6. **Non-code issues** — produce docs/config artifacts and commit them · skip + label `needs-human` · ask each time.
7. **Run span & safety cap** — continuous vs pause-per-Epic; and the max review/fix rounds per PR before pinging the human (default 5).
8. **Deferred/blocked issues** — which labels/markers to skip (e.g. `Fase posterior`, `needs-human`, infra you won't stand up).

Also confirm: the **secret source** (e.g. a gitignored `.env`) — never commit it, never put it in issue bodies/PRs/HTML.

## 3. Per-Epic playbook

Pick the **next Epic**: `python3 <skill>/scripts/backlog.py next --repo <O/R>` → the first Epic in the chosen order with ≥1 open, non-deferred sub-issue. Then:

1. **Branch.** `git checkout <default> && git pull && git checkout -b feat/e<N>-<slug>` (or `epic/<slug>` — keep the convention consistent across the repo).
2. **Mark the Epic in-progress.** Move the Epic + its sub-issues to the in-progress signal you use (a `status:in-progress` label, a project column, or a one-line "▶ starting E<N>" comment on the Epic). This is the first half of keeping the backlog truthful (§4) — the board should never show "started" work as untouched.
3. **Implement (delegate).** Launch ONE coding subagent (`subagent_type` from the registry — `coder` if present, else `general-purpose`), ideally `run_in_background: true` so the loop keeps pacing. Brief it to (see `references/agent-brief.md`):
   - Read each sub-issue authoritatively (`gh issue view <n>`) and the design source (PRD/manifest if present).
   - Implement code-bearing sub-issues as **stacked commits**, one per sub-issue, message referencing the issue (`E1.2: … (#9)`).
   - Non-code sub-issues → **docs/config** committed (per §2.6).
   - Write **unit/integration tests (deps mocked)** + **one real local E2E** per the test-realism choice; verify each sub-issue's **Acceptance criteria**.
   - **Respect off-limits repos/dirs**: build adapters in-repo; if a change is genuinely unavoidable there, DO NOT touch it — list it under "NEEDS BACKEND PR".
   - Run the full local gate (lint + type + tests + e2e) — all green — then **push the branch**; do NOT open a PR or merge.
   - Return a structured report (per sub-issue: implemented / needs-backend-PR / deferred; test results; blockers).
4. **Verify before PR.** When the agent finishes, sanity-check the diff yourself: no secrets committed (grep the real key prefix; confirm `.env` untracked), no off-limits paths touched, tests actually green, no stray artifacts swept in. Fix small issues directly or send the agent back (`SendMessage`).
5. **Open ONE PR.** `gh pr create --base <default> --head <branch>` with a body that summarises the sub-issues and a **`Closes #<n>` line for EACH sub-issue** (so the merge auto-closes them) plus `Part of #<epic>`. Note any `needs-human` placeholders and the "NEEDS BACKEND PR" items.
6. **Drive the review gate.** Wait for the review (self-pace ~5 min), then loop:
   - Check status: `python3 <skill>/scripts/backlog.py review-status --repo <O/R> --pr <n> --gate <greptile|ci|human>`.
   - If **comments/changes requested** → run `/review-pr <n>` (assume "yes" to its prompts if the user authorised that), or apply the fixes directly: read each comment, minimal fix, re-run the local gate, commit, push, reply to each comment with the commit hash, then re-request review (for Greptile: `gh pr comment <n> --body "@greptileai review this and update the score"`). Increment the round.
   - If **pending** (bot hasn't re-reviewed the latest commit; `summary_on_head:false`) → wait again.
   - If **done** → merge. **Cap** at the chosen round limit; if exceeded, stop and ping the human with the PR + blocker.
7. **Merge & CLOSE THE LOOP (do all of these — see §4).** `gh pr merge <n> --squash --delete-branch`, then `git checkout <default> && git pull`. Then run the bookkeeping checklist in §4: confirm every `Closes #<n>` sub-issue actually closed, tick the Epic's task-list, close the Epic when all sub-issues are done, and advance/close the Milestone when its Epics are done. Then pick the next Epic. If off-limits "NEEDS BACKEND PR" items exist, open those PRs on the off-limits repo and **hand them to the human** (do not merge).

## 4. Keep the backlog truthful — issue lifecycle & status hygiene

The backlog is only useful if it always reflects reality. **Update issues at every transition, not "later".** After each PR merges, run this checklist (it is part of step 3.7, not optional):

1. **Sub-issues closed.** A merged PR with `Closes #<n>` auto-closes those issues — **verify it** (`gh issue view <n> --json state -q .state` → `CLOSED`). If a sub-issue wasn't in the PR's `Closes` list (e.g. deferred or split), close it explicitly with a reason comment, or relabel it `deferred`/`needs-human` and leave it open. Never leave a *done* sub-issue open.
2. **Epic task-list ticked.** Edit the Epic body to check the boxes for the landed sub-issues (`- [ ] #n` → `- [x] #n`) so the hierarchy view shows real progress. (`gh issue edit <epic> --body-file -` with the updated body.)
3. **Epic comment + status.** Comment on the Epic with the merged PR link and what shipped; flip its `status:in-progress` → done signal. **Close the Epic** once ALL its sub-issues are closed (or the rest are explicitly deferred): `gh issue close <epic> --comment "All sub-issues shipped via #<pr>."`.
4. **Milestone advanced.** A milestone is **done when every Epic + sub-issue in it is closed** — then close it (`gh api -X PATCH repos/<O/R>/milestones/<num> -f state=closed`). If only partly done, leave it open; the open count is the truth. Never close a milestone with open issues, and never leave a fully-shipped milestone open.
5. **State file synced.** Update `.epic-loop/state.json` `done[]` and `current_epic` (§5).

**Definition of Done per level** (don't mark up the chain prematurely):
- *Sub-issue* done = its code merged to the default branch, acceptance criteria met, tests green.
- *Epic* done = all its sub-issues done (or remainder explicitly deferred with a label).
- *Milestone* done = all its Epics done.

Quick audit anytime: `python3 <skill>/scripts/backlog.py epics --repo <O/R>` shows each Epic + sub-issue state — use it to catch a `CLOSED` PR whose sub-issue is still `OPEN`, or a milestone that should be closed. Reconcile before moving on.

## 5. Pacing, state, and resume

- Run as a **dynamic `/loop`** (no interval, self-paced). Run the current step now, then `ScheduleWakeup` as the last action: ~**270s** while polling an external review (stays in the prompt-cache window), or **1200–1800s** as a fallback heartbeat while a background implementation agent runs (its completion notification is the real wake signal). Pass the full `/loop …` prompt verbatim each time so the next firing re-enters the loop.
- Keep a **state file** `.epic-loop/state.json` in the repo (gitignored): `{order[], current_epic, branch, pr, round, answers{}, done[]}`. Read it on each wake to know where you are; update it after each transition. This makes the loop resumable across sessions.
- A **background implementation agent** + **ScheduleWakeup fallback** is the normal shape: launch the agent, sleep long, get woken on completion, then open the PR and switch to short review-polling waits.

## 6. Guardrails (always)

- **Never commit secrets**; confirm the secret file is gitignored and grep the diff for the real key before every push/PR.
- **Never touch off-limits repos/dirs**; route unavoidable changes to a human-reviewed PR.
- **Confirm the first merge** if the user hasn't explicitly authorised auto-merge; merging and opening PRs are outward-facing.
- **Don't claim green you didn't see** — read the agent's actual test output; re-run the gate yourself if unsure.
- **Honour the cap** — escalate to the human instead of looping forever; surface stuck PRs, forced off-limits changes, and rejected model ids.
- **One Epic in flight at a time** unless the user opts into parallel Epics (separate worktrees to avoid branch collisions).
- **Keep the structure intact** — never create a sub-issue without a parent Epic + milestone, an Epic without a milestone, or a PR without `Closes #<n>` lines; never leave a merged Epic's issues open. The structure (§1) and the lifecycle (§4) are the skill's promise.

## 7. Review-gate detection

See `references/review-gates.md` for the per-tool "done" signals. The default (Greptile): a PR is **done** when, on the latest commit, the bot reacted 👍 to your last `@greptileai` comment, added no new inline comment, and the PR body's summary shows **Confidence Score: 5/5** for that commit. `scripts/backlog.py review-status` checks these.

## Scripts & references
- `scripts/backlog.py` — `epics` (list Milestones → Epics → sub-issues + states), `next` (next Epic to work), `review-status` (gate check: greptile/ci/human).
- `references/review-gates.md` — done-signals for Greptile, CodeRabbit, CI-only, and human gates.
- `references/agent-brief.md` — a fill-in template for the per-Epic implementation subagent brief.
- `/prd-to-issues` — the companion skill that CREATES the §1 structure (milestones + epics + sub-issues + labels + native links) from a PRD, idempotently.
