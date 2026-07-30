# /ship

Hand it a ticket, approve the plan, walk away — it implements and drives the review to 5/5.

`/ship` is the single-ticket, end-to-end delivery conductor. It front-loads every
human decision into an attended kickoff — close the information gaps, get the Jira
ticket right, sign off a visual plan — and then runs unattended: it implements the
plan, opens one PR against the integration branch, and iterates on the Greptile
review until the Confidence Score is 5/5, finishing with a published visual recap
linked from the PR. The rule that makes this safe: the loop runs while you're away,
so it cannot stop to ask you anything — which is exactly why all the questions come
first.

## What It Does

- Resolves the input — a Jira key updates that ticket; free text creates a new one
  — and detects the repo profile (integration branch + the pre-commit checks CI
  enforces) instead of assuming `main`.
- Closes the information gaps before planning, surfacing only the questions that
  would change the implementation through a **visual questionnaire** you answer.
- Writes the agreed update into the Jira ticket — or creates the ticket — only
  after you approve the exact preview; it never silently rewrites a description.
- Authors a **visual plan** (reuse-first, hard-to-reverse decisions pinned, tests
  always included) and treats presenting it as the approval gate — the last time it
  asks you anything.
- Runs unattended after approval: implements the plan, opens one PR against the
  integration branch, and drives the **Greptile review to 5/5** (reusing the
  `review-pr` loop mechanics, which also clear failing CI/CD checks and
  human/Supabase comments), capped at 8 rounds, never auto-merging.
- Closes out by squashing to a single clean commit, publishing a **visual recap**
  of what actually shipped, and threading the plan + recap links into the PR body.

## When To Use It

Use it when you have one well-scoped ticket and you want it delivered end-to-end
with the human in the loop only at the start. After you approve the plan, it owns
the implement → review → 5/5 → recap cycle on its own. Run it under `/loop`
(`/loop /ship <ticket>`) so the review gate can self-pace between Greptile rounds.

Skip it when you're driving a whole backlog of epics (use `epic-loop`), or when you
only need to resolve an existing PR — its Greptile score, failing CI/CD checks, or
review comments (use `review-pr`).

## Examples

These are good `/ship` runs:

- `/ship MAR-123` — fetch the ticket, close its gaps, append the agreed update,
  plan, implement, and drive its PR to 5/5.
- `/ship "add CSV export to the weekly report screen"` — no ticket yet, so it
  creates one from the closed-out gaps before planning.
- `/ship https://omniloy.atlassian.net/browse/MAR-123` — same as the key form; the
  key is parsed from the `/browse/` segment.
- `/loop /ship MAR-456` — full unattended run; the Act-2 review gate self-paces via
  `ScheduleWakeup` until 5/5 or the 8-round cap.

## Install

Claude Code loads skills from `~/.claude/skills/` (user-level) or `.claude/skills/`
(project-level). Copy this skill from the repo:

```sh
git clone https://github.com/Omniloy/agent-skills
cp -R agent-skills/skills/core/ship ~/.claude/skills/
# then in Claude Code:  /ship <KEY-or-text>
```

### Prerequisites

- **`gh`** (GitHub CLI) authenticated — `gh auth status`.
- The **Atlassian MCP** connector connected — to read the ticket and write the
  update / create it.
- The **Agent-Native Plan** MCP connector (`plan`) — for the visual questionnaire,
  the visual plan, and the visual recap.
- A **Greptile** review bot installed on the repo — the 5/5 gate.
- Run under **`/loop`** so the review gate can self-pace.
