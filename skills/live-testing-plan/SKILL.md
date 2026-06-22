---
name: live-testing-plan
description: Design a manual / live verification plan for a Jira issue. Locks in the acceptance criteria (extracting them from the issue or discussing with the user until a set is agreed), then asks who will run the tests (Claude Code against a live server / the Omniloy evals platform / the user themselves), drafts a mode-tailored plan with setup prerequisites and step-by-step test cases each carrying its own acceptance criteria, executes them if Claude is the runner, and delivers a results table marking each test ✅ or ⛔. Pairs with `/jira-action-plan` (Step 6 handoff) but also runs standalone. Usage - "/live-testing-plan MAR-123" or "/live-testing-plan https://omniloy.atlassian.net/browse/MAR-123".
user_invocable: true
---

# live-testing-plan

Turn a Jira issue into a **live verification plan** — the kind a human, Claude, or the evals platform can execute against a real running system — and deliver a pass/fail report. This is the verification half of the loop that starts with `/jira-action-plan`: that skill ships the code, this one proves it works.

## Input

The user provides a Jira issue key or full URL:

- `/live-testing-plan MAR-123`
- `/live-testing-plan https://omniloy.atlassian.net/browse/MAR-123`

If invoked as a handoff from `/jira-action-plan`, the Jira key + the implemented-changes context are already in the conversation — reuse them and skip the redundant fetches.

## Step 1: Fetch the issue and extract acceptance criteria

1. Resolve the site:
   ```
   getAccessibleAtlassianResources  → cloudId for the Omniloy site
   ```
2. Fetch the issue:
   ```
   getJiraIssue(cloudId, issueIdOrKey=<KEY>, fields=["summary","description","issuetype","status","comment","issuelinks","labels","components"])
   ```
3. Read the description and comments looking for **explicit acceptance criteria** — typically a section titled "Acceptance Criteria", "AC", "Criterios de aceptación", a checklist (`- [ ]` items), or a "Given/When/Then" block. Capture each as a discrete, testable statement.
4. Fetch linked issues briefly with `getJiraIssue` (lean fields) — sometimes the parent epic or a linked spec carries the AC instead. One hop only.
5. If the issue's `issuetype` is `Verification`, that's fine here (unlike `jira-action-plan`, this skill explicitly supports verification-type issues — they exist to be tested).

Produce an internal draft: a list of AC statements, each marked `[from issue]` or `[from linked-<KEY>]` so you can show the user where each came from.

## Step 2: Lock in acceptance criteria with the user

Present the AC you found and converge on a final set before drafting any tests. Three cases:

**A. The issue has clear, testable AC.** Show them as a numbered list and ask via `AskUserQuestion`:
- Use these as-is
- Use these with edits (user describes the edits)
- Add more
- Replace entirely

**B. The issue has partial / vague AC** (e.g. "it should work for the patient flow"). Show what you found, propose specific testable versions, and ask the user to approve / amend each via `AskUserQuestion`.

**C. The issue has no AC.** Tell the user, then ask them to describe the change's expected behaviour. Propose 3–6 candidate AC based on what they say and the issue context, and iterate until they confirm a set.

The final AC list is **load-bearing** for the rest of the skill — every test case must trace back to at least one AC, and any AC not covered by a test must be flagged. Save it as the source of truth for Step 5.

**Scope guard:** keep the AC tied to *this* issue. If the user adds something that clearly belongs to a different ticket, flag it and ask whether to drop it or open a new issue.

## Step 3: Choose the execution mode

Ask via `AskUserQuestion` who will run the tests. Options:

- **Claude Code against a live server** — Claude executes API calls / scripted interactions itself against a running service the user points it at. Best for backend flows that are scriptable (REST, MCP, SQL). Claude produces the ✅ / ⛔ report.
- **Omniloy evals platform (`agent-evaluator`)** — Claude produces a structured spec (personas + evaluators + test config) for `/agent-eval-api` to execute against a deployed agent. Best for conversational / voice-agent behaviour where the test *is* a multi-turn dialogue with stopping criteria.
- **User runs it manually** — Claude produces a human-readable checklist with setup, steps, and per-test AC. Best for UI flows, anything requiring a real phone/headset, or anything the user just wants to eyeball.
- **Mixed** — primary mode + some tests overridden per case. Ask the user to confirm the primary mode; per-test overrides happen during plan drafting.

Mode determines the plan's output format (Step 5), so don't skip this. Record the answer.

If "Claude Code against a live server" is chosen, also ask in the same `AskUserQuestion` call:
- **Server target** — local (`http://localhost:<port>`) / staging / explicit URL. Don't guess.
- **Auth** — token in env var (name it), `gh`-style login, none. Don't proceed without knowing how to authenticate if the endpoints need it.

## Step 4: Scan the repo / change context

Find what's actually being tested so the plan is concrete.

1. Confirm the repo (`git rev-parse --show-toplevel`, `git remote -v`).
2. If invoked standalone (not from `/jira-action-plan`), inspect recent change context:
   ```bash
   git status
   git log --oneline -10
   git diff main...HEAD --stat   # or vs. the user's chosen base
   ```
   Read the changed files. The point is: tests should target the actual code paths that changed, not a generic interpretation of the issue.
3. Identify the **surface area** to hit: HTTP endpoints, MCP tools, CLI commands, UI screens, DB tables. List them mentally and map each AC to at least one surface.
4. Identify **prerequisites** the tests will need: a seeded patient, a configured questionnaire, a feature flag on, a service running, fixtures loaded, env vars set, an auth token. These become the *Setup* section in Step 5.

## Step 5: Draft the plan

Structure the plan in two sections — **Setup** and **Tests** — and adapt the *format* of each test case to the execution mode picked in Step 3.

### Setup section (all modes)

Numbered list of things that must be true before any test runs. For each item:
- **What** — one line (e.g. "Create a test patient named `LT-<jira-key>-patient` in DB").
- **How** — concrete steps or commands (curl, SQL, UI clicks).
- **Done-when** — how to know setup succeeded (record the ID/value the tests will reuse).

For Claude execution: Claude does the setup itself unless an item is human-only (e.g. "plug in the headset"); mark those `[human]`.
For evals / human modes: the whole Setup is a prereq list the user/runner ticks off before kickoff.

### Tests section — format per mode

**Mode: Claude against live server.** Each test case:
- **#** and short **title** (will appear in the results table).
- **AC covered** — reference back to Step 2's numbered AC.
- **Steps** — concrete actions (HTTP method + path + body, or commands). Include exact assertions, not "should look right".
- **Expected result** — single sentence + concrete value(s) (status code, response field equals X, DB row exists, etc.).
- **Acceptance criteria for this test** — the precise condition that makes it ✅ vs. ⛔.

**Mode: evals platform.** Each test case maps to:
- **Persona** — name (`AI_generated-<short-slug>`), `objective`, `persona_type` (usually `llm_conversational`), and `stopping_criteria_rules` covering at minimum: success, hang up, transfer, auth loop.
- **Evaluator(s)** — one per criterion, `tool_called` for binary "was tool X called" or `llm` for judgement. Name them `AI_generated-<slug>`. **One criterion = one evaluator.**
- **Test config binding** — `agent_id` (resolved from the issue/repo, do not invent), `persona_ids`, `evaluator_ids`, `runs_per_persona`, `max_concurrency`, `timeout_seconds`, optional `caller_phone_number` (force `max_concurrency=1` if set).
- **AC covered.**

The deliverable in this mode is a structured spec (Markdown with the above fields per test, or a JSON block) that `/agent-eval-api` can consume. Do **not** call the evals API yourself — that's that skill's job.

**Mode: user manual.** Each test case:
- **#** + **title**.
- **AC covered.**
- **Steps** — numbered, plain-language, UI/CLI clicks the user can follow without re-reading the issue.
- **Expected result** — what they should see/hear.
- **Pass criteria** — single yes/no condition for the user to check.
- A trailing `[ ] ✅ pass  [ ] ⛔ fail  Notes: ___` line they can fill in.

### Coverage check (all modes)

Before presenting the plan, verify every AC from Step 2 maps to at least one test. If any AC is uncovered, either add a test or call it out explicitly: `AC <n> not covered — <reason>`. Surface uncovered AC to the user during approval rather than silently shipping a partial plan.

## Step 6: Present for approval

Show the user a single, compact view of the plan:
- The locked AC list (numbered, from Step 2).
- The execution mode + targets.
- The Setup section.
- Each test case in its mode-appropriate format.
- The coverage check (AC → tests mapping).

Ask via `AskUserQuestion`:
- **Approve and run** — Claude executes (live-server or evals-handoff mode) or hands the plan over in chat (manual mode).
- **Approve with changes** — user describes changes, you revise and re-present.
- **Don't run yet** — keep the plan in the conversation; the user will come back when ready.
- **Cancel.**

Do not execute anything until you have an explicit approve answer.

## Step 7: Execute (Claude live-server mode)

For each test case in order:

1. Run the **Setup** items not already done (skip if a previous test established them).
2. Execute the test's **Steps** — make the actual HTTP/MCP/SQL calls.
3. Capture the **actual result** — status code, response body excerpt, DB state, whatever the expected result was about.
4. Compare against the test's **Pass criteria**. Mark ✅ or ⛔.
5. On ⛔, capture enough detail for triage (error message, response excerpt, what was different) — but **do not try to fix the underlying code**. This skill verifies; it doesn't patch.
6. Keep going through the remaining tests even if one fails (unless a test's failure means later tests literally can't run, e.g. setup blew up — then stop and report).

Throughout, do not modify the codebase. The working tree should look the same before and after this skill runs (except for the results file in Step 9).

## Step 7-alt: Hand off (evals or manual mode)

**Evals mode:** Show the user the structured spec and say:
> "Plan ready. Run `/agent-eval-api` next — it will resolve the agent, create the personas + evaluators above (prefixed `AI_generated-`), bind them into a test config, and kick off the run. Come back here with the `run_id` and I'll fold the results into the report."

When the user returns with the `run_id` and result summary, render the outcome view (Step 8) using the eval results.

**Manual mode:** Render the full plan in chat — Setup, then each test case with its steps + expected result + pass criteria — and stop. When the user comes back having executed it manually, optionally re-invoke the skill with their results to render the outcome view.

## Step 8: Outcome view

When tests have been executed (by Claude, by evals, or reported back by the user), render a results table in the chat — exactly this shape:

```
| # | Test | Expected | Actual | Result |
|---|------|----------|--------|--------|
| 1 | <short title> | <one-line expected> | <one-line actual> | ✅ |
| 2 | <short title> | <one-line expected> | <one-line actual> | ⛔ |
```

Use ✅ for pass and ⛔ for fail (exactly these emoji — they're the visual signal).

Below the table, add:
- **Summary** — `<X> / <total> passed`.
- **AC coverage** — which AC are fully covered (all their tests passed), partially (some passed), or failed (all failed). Cite AC numbers.
- **Failures** — for each ⛔, a 2-3 line note: what the test was, what happened, where to look (path/endpoint), but **no fix**.

## Step 9: Wrap up

Everything stays in the conversation by default — no file is written.

Offer via `AskUserQuestion`:
- **Post a summary comment on the Jira issue** — short comment with pass/fail count and the results table. Uses `addCommentToJiraIssue`. Only post on yes.
- **Move the Jira issue forward** — if all tests passed and the issue has a "Ready for Review" / "Done" / "Verified" transition available (`getTransitionsForJiraIssue`), offer to transition it via `transitionJiraIssue`. Default to off; only transition on explicit yes.
- **Save the plan + results to a file** — only if the user explicitly asks (e.g. to attach to a PR). Default path: `LIVE_TESTING_<JIRA-KEY>.md` in the repo root. Confirm the path before writing.

Do not push code, open PRs, or modify the working tree.

## Important notes

- **AC are sacred.** Every test ties to an AC. AC that aren't covered are flagged, not hidden.
- **Stay scoped.** A test plan for `MAR-123` tests `MAR-123`'s AC. If the user wants to test something else, that's a different invocation.
- **Verification ≠ fix.** This skill detects ⛔; it does not patch. On failure, surface it and stop — the next move is `/jira-action-plan` (or human triage), not silent code edits.
- **Never invent IDs or endpoints.** For evals mode, follow the `agent-eval-api` rule: resolve real `agent_id` / tool names before drafting the spec. For Claude live-server mode, ask the user for the URL and auth rather than guessing.
- **One repo, one issue.** Same rule as `jira-action-plan` — don't cross repos unless the user explicitly says so.
- **The console is the deliverable.** Plan and results live in chat by default — only write a file if the user asks.
