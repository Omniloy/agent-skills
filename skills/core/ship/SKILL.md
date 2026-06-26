---
name: ship
description: End-to-end "ship it" conductor for a single ticket. Front-loads every human decision (close information gaps with a visual questionnaire, write the update into the Jira ticket — or create a new one — with your approval, then a visual plan you approve), then runs unattended (implement → open ONE PR against the integration branch → drive the Greptile review to 5/5) and finishes with a published visual recap. The PR body always carries the plan + recap links. Composes create-jira-work-items (create mode), visual-plan (approval surface), review-pr loop (the 5/5 + CI/CD gate), and visual-recap (closeout). Triggers on - "/ship MAR-123", "/ship 'add CSV export to the report screen'", "ship this ticket end to end", "implement MAR-123 and drive it to 5/5".
user-invocable: true
---

# ship

Hand `ship` a ticket (or an idea) and walk away once you've approved the plan. It closes the information gaps, gets the Jira ticket right, gets a visual plan signed off — and **only then** runs unattended: it implements, opens one PR, and iterates on the Greptile review until the Confidence Score is **5/5**, then publishes a visual recap and threads the plan + recap links into the PR.

The design rule that makes this safe: **the loop runs while you're away, so it cannot stop to ask you anything.** Every human decision is therefore front-loaded into the kickoff (gaps → ticket → plan). After you approve the plan, no more questions — just code, review, and a recap to read when it's done.

## Where it sits

`ship` is the **single-ticket, fully-autonomous** member of the family. Pick the right entry point:

| If… | Use |
|---|---|
| One ticket, you want it implemented AND driven to 5/5 with no babysitting after plan approval | **`/ship`** |
| A whole backlog (Milestone → Epics → sub-issues) end-to-end | `epic-loop` |
| You only need to resolve an existing PR (Greptile 5/5 + CI/CD + comments) | `/review-pr <pr> loop` |

`ship` reuses the other skills rather than reinventing them:

- **`create-jira-work-items`** — the team's Jira intake standard (Feature + Dev + Verification) used when ship is in create mode (Phase 2).
- **`visual-plan`** — the approval surface (Phase 3) and the visual questionnaire (Phase 1).
- **`review-pr` (loop mode)** — the per-round verify→fix→reply→resolve→re-request→sleep mechanics that drive Greptile to 5/5 and clear CI/CD + comments (Phase 4). Do not duplicate that logic; drive it.
- **`visual-recap`** — the published closeout (Phase 5).

## Input

```
/ship MAR-123                                   # existing key → update that ticket
/ship https://omniloy.atlassian.net/browse/MAR-123
/ship "add CSV export to the weekly report screen"   # free text → create a new ticket
```

How the input decides the Jira path:

- **A Jira key or `/browse/<KEY>` URL** → fetch that issue; in Phase 2 you **write the update into it**.
- **Free text with no resolvable key** → in Phase 2 you **create a new ticket** from the closed-out gaps.

Extract the key from the URL's `/browse/` segment. The site/project is inferred from `getAccessibleAtlassianResources` + the key's prefix — never ask for it.

## The three acts

```
ACT 1 — KICKOFF (attended, human gates)
  Phase 0  resolve input + repo profile (BASE / CHECKS)
  Phase 1  close information gaps           → visual questionnaire, you answer
  Phase 2  write update / create ticket     → preview, you approve, then write to Jira
  Phase 3  visual plan                      → you approve  ← last human gate
ACT 2 — BUILD + GATE (unattended, no questions)
  Phase 4  implement → PR against BASE → Greptile loop to 5/5
ACT 3 — CLOSEOUT
  Phase 5  publish visual recap → PR body carries plan + recap links → stop (no merge)
```

> **Autonomy needs `/loop`.** The Phase-4 gate self-paces with `ScheduleWakeup`, which only fires under `/loop` dynamic mode. For unattended runs, invoke as `/loop /ship <ticket>`. If invoked plainly, Act 1 still works; tell the user to re-invoke under `/loop` before Act 2 starts polling.

---

## Phase 0 — Resolve input + repo profile

1. **Resolve the ticket** (see Input). Hold whether you're in **update** mode (key given) or **create** mode (free text).
2. **Detect the repo profile.** This command is used across repos with different stacks and integration branches. Never assume `main`. Fix:
   - **`BASE`** — the integration branch. Read it from the repo (`gh repo view --json defaultBranchRef`, `git remote show origin`). Prefer `develop`, else `dev`; **never `main`** unless it is genuinely the only integration branch. If unsure, stop and ask.
   - **`CHECKS`** — the pre-commit gate. Derive from the repo and **replicate what CI does** (`.github/workflows/*` is the real contract): Node → the existing `build`/`test`/`lint`/`format:check` scripts; Python/uv → `ruff format --check .`, `ruff check .`, `mypy` if configured, `pytest` with the CI's flags. Remember `make format`/`make lint` *apply* changes — re-validate with `--check` afterward and confirm no diff remains.
3. Confirm the working repo (`git rev-parse --show-toplevel`, `git remote -v`). One repo at a time — don't touch siblings unless told.

## Phase 1 — Close the information gaps (visual questionnaire)

Before anything is written or planned, find what's **missing or ambiguous** about the task and resolve it with the user.

1. **Gather context** thoroughly:
   - Update mode: `getJiraIssue(... fields=["summary","description","issuetype","status","priority","labels","components","parent","comment","issuelinks"])`, `getJiraIssueRemoteIssueLinks`, and one hop into each linked issue. Read every comment.
   - Scan the repo for the code the task touches (`Grep`/`Glob`, or an `Explore` subagent for open-ended searches). Read the relevant files so the gaps you raise are real.
2. **Identify the gaps** — only the ones that would change the implementation or the acceptance criteria: unclear scope boundary, missing acceptance criteria, an undecided approach with real trade-offs, an unspecified edge case, an external contract (wire format, ids, auth) not pinned down. Don't manufacture questions; if a gap has an obvious answer, state the assumption in the plan instead.
3. **Ask via the visual questionnaire.** Call `get-plan-blocks` first for the authoritative block tags, then `create-visual-questions` with 2–6 gap questions, each with concrete options where possible. Surface the returned URL in chat and read answers back with `visual-answer` / `get-plan-feedback`. (This is the one place a visual questionnaire is the intended surface — gap intake is exactly the `visual-intake` use case.)
4. If, after this, the ticket still isn't ready to implement (too many open forks, conflicting answers), say so plainly and stop — that's a sign the ticket needs a human pass, not a plan.

Record the resolved answers; they feed Phases 2 and 3.

## Phase 2 — Write the update / create the ticket (with approval)

Now Jira reflects reality. **Never write to Jira without explicit approval of the exact content.**

- **Update mode (key given).** Draft an *Update* block to append to the ticket: the gap resolutions, the refined/agreed acceptance criteria, and a one-paragraph implementation summary. Show the user the **exact diff/preview** (what gets added — never silently rewrite the existing description). Approve via `AskUserQuestion` (Apply update / Edit / Skip writing to Jira). On approval: `editJiraIssue` to append, or `addCommentToJiraIssue` if the team prefers comments for progress notes (match the ticket's existing convention).
- **Create mode (free text).** Create the ticket via **`create-jira-work-items`** so it follows the team's mandatory structure (a Feature + a linked Dev `Tarea` + an independent Verification) rather than a lone issue. Draft the Feature description, Functional + Technical detail, and acceptance criteria (all derived from the closed gaps); show the full preview, approve via `AskUserQuestion` (Create / Edit / Cancel). On approval, create the set and capture the **Feature** key + URL — that Feature becomes the ticket this run is about (the Dev task tracks the implementation; the Verification is what `live-testing-plan` later executes).

Either way, the resulting Jira key/URL is carried into the PR body and the recap.

## Phase 3 — Visual plan (the approval gate)

Author a structured **visual plan** (the `visual-plan` skill owns the quality bar — read its `references/` before authoring; do not write blocks from memory). Lead with reuse (name existing files/symbols/helpers before the new delta), pin the hard-to-reverse decisions, and **always include explicit test steps** at the level the repo calls for. For UI work, start with the canvas; for backend/data, stay document-first with inline diagrams.

Publish it, surface the URL, and **treat presenting the plan as the approval request** — one `AskUserQuestion` (Approve / Approve with changes / Cancel). Set the plan's visibility appropriately for unreleased work (`set-resource-visibility`) and **keep the published plan URL** — it goes in the PR.

**This is the last human gate.** Do not write a line of code until the plan is approved. After approval, Act 2 runs unattended.

## Phase 4 — Implement + drive Greptile to 5/5 (unattended)

This phase is idempotent: on every wake-up, read the real state (branch, PR, HEAD sha, last Greptile review) and deduce where you are — the PR is the state, not memory. Repeat Phase 0 each wake-up in case context was lost.

1. **No branch/PR yet?** `git fetch origin $BASE`, branch `feature/<ticket-slug>` **from `origin/$BASE`** (freshly fetched — not a stale local `dev`/`develop`), implement the approved plan, run `CHECKS` and fix everything, commit, push, and `gh pr create --base "$BASE"`. The PR body includes **`Closes <jira-key>` (or the Jira URL), the visual-plan link, and a placeholder for the recap link** (filled in Phase 5). Schedule the next wake-up (~270s) to give Greptile time.
2. **PR exists — still mergeable?** Check every wake-up: `gh pr view <n> --json mergeable,mergeStateStatus`. If `CONFLICTING`/`DIRTY`: `git fetch origin $BASE` + `git rebase origin/$BASE`, resolve (import blocks → keep both sides, alphabetical), re-run `CHECKS`, `git push --force-with-lease`, confirm `MERGEABLE`/`CLEAN`.
3. **Has Greptile reviewed HEAD?** Compare the PR's HEAD sha to the latest `greptile-apps` review/check. If not yet, post `@greptileai review` once (if none pending), sleep ~270s, exit.
4. **Greptile reviewed HEAD — run one round of the `review-pr` loop:** read inline comments + Confidence Score (and any failing CI/CD checks or human/Supabase comments), verify each **against the actual code** (read the file, grep callers — verdicts from comment text alone are wrong half the time), fix valid ones + resolve their threads, reply-with-reason to invalid ones (leave open), run `CHECKS`, commit, push, re-request review, sleep ~270s. An **ambiguous** comment is a hard stop — escalate, don't keep cycling.
5. **Terminal condition:** Confidence Score **5/5** with no unresolved threads. For repos where Greptile emits no `N/5` (some Python repos: empty review body), the practical terminal is the **`Greptile Review` check green + 0 inline comments, sustained across a re-review** — don't burn rounds waiting for a number that never comes.

**Safety cap: 8 rounds.** Derive the count from the number of `@greptileai` re-review requests on the PR. Hit the cap without a terminal condition → stop and report what's outstanding.

Reading the score: summary via `gh api repos/:owner/:repo/pulls/<n>/reviews` (`Confidence score: N/5` in the `greptile-apps` body); inline P1/P2/P3 via `.../pulls/<n>/comments`; the check via `gh pr checks <n>` and `gh api repos/:owner/:repo/commits/<sha>/check-runs`.

## Phase 5 — Visual recap + finalize the PR

Only after the terminal condition:

1. **Squash the branch to one clean commit** so `BASE` stays tidy: `git reset --soft $(git merge-base HEAD origin/$BASE)`, re-commit everything with a clean message summarising the task (body: the Greptile points addressed), end with the usual `Co-Authored-By:` line. `reset --soft` leaves the approved tree untouched. `git push --force-with-lease`.
2. **Publish a visual recap** of what actually shipped with the `visual-recap` skill (read its `references/` first): diagrams, file-tree, `data-model`/`api-endpoint` for schema/API changes, annotated diffs of the key files. Publish (never inline), set visibility, and **keep the recap URL**.
3. **Finalize the PR body** so it carries both artifacts and the ticket:
   ```
   Closes <jira-key>

   📐 Plan:  <visual-plan URL>
   📋 Recap: <visual-recap URL>
   ```
   Update via `gh pr edit <n> --body ...` (preserve `Closes`).
4. **Done.** Stop the loop (no `ScheduleWakeup`). Report: PR link, that 5/5 (or the green-check terminal) was reached, that it's squashed to one commit, and the plan + recap links. **Never merge** — that decision is the user's. If the force-push triggers a Greptile re-review, don't wait: the tree is identical and already agreed; just note it.

## Guardrails

- **All human input is front-loaded.** Gaps, ticket approval, and plan approval happen in Act 1. Once Act 2 starts, the loop never stops to ask — an ambiguous Greptile comment is the only escalation, and it halts the loop rather than guessing.
- **Never write to Jira without approval** of the exact preview. Update mode appends — it never rewrites the existing description silently.
- **Never push to `BASE`/`main`.** Always a feature branch + PR against `BASE`. Branch from freshly-fetched `origin/$BASE` to avoid merge conflicts.
- **Never auto-merge.** Reaching the terminal condition ends the run and reports — the merge is the user's call.
- **Run `CHECKS` before every commit** and fix all warnings/diff; replicate what the repo's CI enforces.
- **Honour the 8-round cap.** Hitting it escalates with the current score + open threads; loops without caps cost money and trust.
- **Don't over-fix Greptile.** Minimal edit per valid comment; out-of-scope suggestions get a polite reply, not a quiet refactor. Never resolve a thread you didn't fix.
- **Never commit secrets.** Confirm `.env`/keys aren't in the diff before any push.
- **The plan and the recap are the source of truth**, not chat. If scope shifts during Act 1, update the plan (`update-visual-plan`) rather than only changing course in chat.
