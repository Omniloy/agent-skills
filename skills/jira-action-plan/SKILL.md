---
name: jira-action-plan
description: Take a Jira issue (task or feature), fetch its context + linked issues via the Atlassian MCP, scan the current repo for related code, surface key decisions with pros/cons, draft a step-by-step action plan (including tests), and — only once the plan is approved — implement it pausing after every step so the user can review the working tree. Never commits or pushes without explicit user permission. Usage - "/jira-action-plan MAR-123" or "/jira-action-plan https://omniloy.atlassian.net/browse/MAR-123".
user_invocable: true
---

# jira-action-plan

Turn a Jira issue into a reviewed, step-by-step implementation. The skill reads the issue and its linked context, scans the current repo, consults the user on real decisions, writes a plan for approval, and only then implements — one step at a time, pausing for human review between each.

This is the **deliberate** counterpart to `epic-loop`: one ticket, with the human in the loop at every fork.

## Input

The user provides a Jira issue key or full URL:

- `/jira-action-plan MAR-123`
- `/jira-action-plan https://omniloy.atlassian.net/browse/MAR-123`

If the URL form is provided, extract the key (the segment after `/browse/`). The Atlassian site / project is inferred from `getAccessibleAtlassianResources` + the key's project prefix — do not ask the user for it.

## Scope discipline (read this before every step)

These rules apply throughout the skill:

- **Stay within the issue's stated scope.** Do not pull work in from linked issues, parent epics, or comments — even if it looks related or "easy to bundle".
- **Do surface opportunities.** If you notice a beneficial improvement adjacent to the scope (refactor, bug nearby, missing test), mention it in the plan as an **optional extension** with a 1-line rationale and ask via `AskUserQuestion` whether to include it. Default to "no" unless the user opts in.
- **Issue type guard.** This skill handles `Task` and `Story`/`Feature` issue types. If the fetched issue is a `Verification` (or any non-code issue type), stop and tell the user this skill doesn't cover that type yet.

## Step 1: Fetch Jira context

1. Resolve the Atlassian site:
   ```
   getAccessibleAtlassianResources  → pick the cloudId for the user's Omniloy site
   ```
2. Fetch the issue:
   ```
   getJiraIssue(cloudId, issueIdOrKey=<KEY>, fields=["summary","description","issuetype","status","priority","labels","components","parent","assignee","comment","issuelinks"])
   ```
3. Fetch remote links (Confluence pages, Figma, GitHub PRs/issues, etc.):
   ```
   getJiraIssueRemoteIssueLinks(cloudId, issueIdOrKey=<KEY>)
   ```
4. For each `issuelinks` entry, fetch the linked issue's summary, status, and description with `getJiraIssue` (lean — only the fields you need to understand the relationship). Do NOT recurse further; one hop is enough.
5. Read all comments returned on the issue.
6. **Issue-type guard:** if `issuetype.name` is `Verification` (or any type outside `Task` / `Story` / `Feature` / `Bug` / `Improvement`), stop and report this to the user — do not proceed.

Produce a short internal summary you can reference later: title, type, status, the goal in 1-2 sentences, the acceptance criteria you can extract from the description/comments, and a one-line note for each meaningfully related linked issue (what it is + whether it changes anything about this ticket).

## Step 2: Scan the current repo

The "current repo" is the git repo of the working directory. **Do not look at sibling repos** unless the user explicitly tells you to (e.g. "also check maria-core-service"). The Omniloy workspace contains multiple repos (`maria-voice`, `omniloy-mcp-server`, `maria-core-service`, `maria_db`); each task is scoped to one unless stated otherwise.

1. Confirm the repo:
   ```bash
   git rev-parse --show-toplevel
   git remote -v
   ```
2. Search the repo for code related to the issue — feature names, entity names, endpoints, file paths or symbols mentioned in the description/comments. Prefer `Grep` / `Glob`; spawn an `Explore` subagent if the search is open-ended.
3. Read the relevant files (full read for short ones, targeted reads for long ones) so you actually understand the current behaviour before proposing changes.
4. Check the branch state:
   ```bash
   git status
   git branch --show-current
   git log -1 --oneline
   ```
   Note whether a branch already exists that looks dedicated to this ticket (e.g. branch name contains the Jira key). This feeds into Step 4.

Produce an internal summary: what already exists, what's missing, what looks closest to where the change belongs, and any risks you spotted (coupling, tests that will break, migrations needed, etc.).

## Step 3: Consult on key decisions

If your repo scan surfaced genuine forks — choices where a reasonable engineer would pause — present them to the user **before** drafting the plan, using `AskUserQuestion`.

Only ask about things that are:
- **Load-bearing** for the implementation (changes which files you touch, which approach you take, or the test strategy).
- **Genuinely uncertain** — don't manufacture decisions just to look thorough. If there's an obvious right answer, take it and mention it briefly in the plan.

Format each question as an `AskUserQuestion` with 2-4 options. For each option, include a **one-line pros / one-line cons** in the description so the user can choose without re-reading the code.

Examples of decisions worth asking:
- "Add this as a new endpoint vs. extend the existing one" → pros/cons of each.
- "Reuse `FooService` vs. introduce a thin `BarService`" → pros/cons.
- "Schema change with migration vs. denormalise in app code".
- "Cover with unit tests only vs. add an integration test".

Batch related questions into a single `AskUserQuestion` call (it supports multiple). Cap at ~4 questions total — if you have more, the issue probably isn't ready to implement and you should say so.

Record the answers; they feed Step 4.

## Step 4: Draft the action plan (for approval)

Use the native Claude Code task system (`TaskCreate` / `TaskUpdate`) to author the plan as a checklist the user can see and the skill will tick through. Each task = one reviewable step.

**Every plan must include:**

1. **Branch step (first).** Check whether a dedicated branch already exists for this ticket:
   - If yes, propose checking it out.
   - If no, propose a branch name (`<jira-key>-<short-slug>`, lowercased) and offer to create it. Do not create it yet — that's the first execution step the user approves.
2. **Implementation steps.** Small, ordered, each describable in one line ("add `X` field to `Foo`", "wire `X` into `Bar.handle()`", "update `baz.test.ts`"). Each step should touch a coherent slice that's worth a separate pause.
3. **Tests.** Always include explicit test steps. Pick the level the repo and task call for:
   - Pure logic / utilities → unit tests.
   - New endpoint / handler / service-to-service flow → integration test(s).
   - When unsure, add both and mention why in the step.
   Match the repo's existing test framework and structure — don't introduce a new one.
4. **Optional extensions section.** If you identified beneficial work outside the issue's scope in Step 2, list each here as `Optional — <title>` with a 1-line rationale. These are **not** added to the task list until the user opts in.
5. **Live testing offer (last).** End the plan with: *"After implementation, I can hand off to `/live-testing-plan` (separate skill) to design a manual verification plan. Want me to offer that at the end?"* — capture the yes/no for Step 6.

Present the plan as a single message containing:
- Branch decision (existing / new + name).
- Numbered implementation + test steps.
- Optional extensions (if any) with explicit opt-in.
- The live-testing offer.

Then ask for approval via `AskUserQuestion` with options:
- **Approve as-is** — proceed to Step 5.
- **Approve with changes** — user describes changes, you revise and re-present.
- **Skip step-by-step review** — user approves and asks you to run through all steps without pausing between them (you still pause for genuine blockers and for the final commit prompt).
- **Cancel** — stop here.

Do not start implementing until you have an explicit approve answer. The "skip step-by-step review" choice sticks for the rest of the session.

## Step 5: Implement step-by-step

For each task in order:

1. `TaskUpdate` the step to `in_progress`.
2. Implement that step and only that step. Do not lump in the next one even if it's small.
3. `TaskUpdate` to `completed`.
4. **Pause for review** (unless the user chose "skip step-by-step review"):
   - Briefly summarise what you changed (1-3 bullets, with `path:line` refs).
   - Tell the user the working tree is theirs to inspect and modify.
   - Wait for the user to say "continue" (or equivalent). Do not call any further tools until they reply.
5. When the user comes back, **re-read the working tree** before the next step (`git status`, then `Read` any files you'll touch). The user may have edited code, added files, or reverted something — incorporate their changes rather than overwriting them. If their edits change your assumptions for the next step, flag it and ask before continuing.

If a step fails (test failure, type error, unexpected state), stop and report — do not silently retry past 1 obvious correction. Surface the failure to the user with what you tried.

## Step 6: Wrap up

When all implementation + test steps are complete:

1. Run the repo's test suite (or the relevant subset) and report results.
2. Offer a commit via `AskUserQuestion`:
   - **Yes, commit** — propose a commit message (subject + 1-3 bullet body referencing the Jira key, e.g. `MAR-123: <summary>`). Stage only the files you actually changed (by name, not `git add -A`). Show the user the message and the staged file list for one final confirm before running `git commit`.
   - **No, I'll commit myself** — stop. Do not stage or commit.
3. Do **not** push. If the user wants to push or open a PR, they'll ask.
4. If the user said yes to the live-testing offer in Step 4, suggest: *"Run `/live-testing-plan <jira-key>` to design the manual verification."*
5. Offer to add a short comment to the Jira issue summarising what was implemented + the branch name, via `addCommentToJiraIssue`. Only post if the user says yes.

## Important notes

- **Never commit, push, or transition the Jira issue without explicit user permission.** Every state-changing action outside the local working tree requires a "yes" in this session.
- **Stay scoped.** When in doubt about whether something belongs to this ticket, ask — don't expand silently.
- **Re-read after every pause.** The user can and will edit files between steps; assume the tree changed.
- **One repo at a time.** If the issue clearly needs changes in a sibling repo, surface it as a decision in Step 3 and let the user decide whether to expand scope or open a separate ticket.
- **Keep the plan honest.** If new information mid-implementation invalidates a later step, stop, update the task list, and re-confirm with the user before continuing.
