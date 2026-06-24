---
name: greptile-resolve
description: One-pass response to a Greptile PR review. Reads the inline comments + Confidence Score, verifies each comment against the repo code, then for each comment - if valid, applies the minimal fix and marks the thread RESOLVED; if invalid, replies with a brief justification and leaves the thread open. Pushes the fixes, re-requests review, and exits. Unlike epic-loop, this skill does NOT poll - it runs once. Triggers on - "/greptile-resolve 220", "address the greptile comments", "solve the greptile feedback", "respond to greptile on PR <n>", "drive this PR to 5/5".
user-invocable: true
---

# greptile-resolve

Make one clean pass at a Greptile review: read the comments + Confidence Score, judge each against the actual code, fix the valid ones (and resolve their threads), reply to the invalid ones with a short reason, push, re-request the review, and stop. **No internal loop** — if the score hasn't reached 5/5 after this pass, the user can rerun the skill.

This is the Greptile-specific cousin of `/review-pr`. Pick the right one:

| If… | Use |
|---|---|
| Reviewer is Greptile and you want the agent to decide validity itself, fix + resolve, and exit | `/greptile-resolve` (this skill) |
| Mixed/human reviewers, or you want to approve a plan before any code is changed | `/review-pr` |
| You want the agent to keep iterating until 5/5 across many PRs | `epic-loop` (calls this skill internally per round) |

## Input

PR number or full URL:
- `/greptile-resolve 220`
- `/greptile-resolve https://github.com/Omniloy/maria-voice/pull/220`

If only a number is given, infer the repo from the current git remote (`gh repo view --json nameWithOwner -q .nameWithOwner`).

## Step 1 — Fetch PR + Greptile state

1. PR head, body, branch:
   ```bash
   gh pr view <pr> --json title,body,headRefName,headRefOid
   ```
2. **Confidence Score** + the score rationale — parse `Confidence Score: N/5` and the surrounding paragraph from the PR body (same source `epic-loop/scripts/backlog.py review-status --gate greptile` uses). The rationale usually names the specific concern keeping the score below 5/5 — that's your highest-priority comment.
3. **Greptile inline comments**, only those by `greptile-apps[bot]`, only those created after your last `@greptileai` re-review request (older ones are already addressed):
   ```bash
   # find the last @greptileai re-review request, if any
   gh api repos/<o>/<r>/issues/<pr>/comments \
     --jq '[.[] | select(.body|contains("@greptileai"))] | last | .created_at'
   # then the inline comments
   gh api repos/<o>/<r>/pulls/<pr>/comments \
     --jq '.[] | select(.user.login=="greptile-apps[bot]") | {id, node_id, path, line: (.line // .original_line), body, created_at, in_reply_to_id}'
   ```
   Filter to comments whose `created_at` is after the last `@greptileai` request (if there is one).
4. **Map each comment to its review thread** — you need the thread `id` (a GraphQL node ID) to resolve it later:
   ```bash
   gh api graphql -F owner=<o> -F repo=<r> -F pr=<pr> -f query='
     query($owner:String!,$repo:String!,$pr:Int!){
       repository(owner:$owner,name:$repo){
         pullRequest(number:$pr){
           reviewThreads(first:100){
             nodes{ id isResolved comments(first:20){ nodes{ databaseId } } }
           }
         }
       }
     }'
   ```
   Build `{comment_databaseId → threadId}`. Skip any thread where `isResolved == true` — those are already done.
5. **Already at 5/5 with no open Greptile threads?** Print `PR is already at 5/5 — nothing to do.` and exit. Don't push an empty commit, don't re-request review.

## Step 2 — Verify each comment against the repo

For each unresolved Greptile comment:

1. **Open the actual file** at the cited path and read enough surrounding context (callers, related helpers, tests). Grep for the symbol if the concern depends on how it's used elsewhere. **Verdicts based on the comment text alone are wrong half the time** — always read code first.
2. Classify:
   - **Valid** — the code really has the problem Greptile describes (bug, missing check, leak, race, broken contract, security issue, etc.). Plan the minimal fix.
   - **Invalid** — Greptile is wrong: misread the code, flagged an intentional choice, suggested an out-of-scope refactor, or its assumption doesn't hold (e.g. the input is already validated upstream, the framework handles it, the path is guarded by a feature flag, etc.).
   - **Ambiguous** — you genuinely can't tell from the code alone. **Pause and ask the user** before continuing; don't guess. (Auto mode is not a license to commit a fix you're unsure about.)
3. Cross-reference the **Confidence Score reason** — a 4/5 with one specific gripe usually narrows down which comment is the blocker. Fixing that one is often what moves the score to 5/5.

Give the user a one-line summary before editing — e.g. `5 comments: 3 valid (will fix), 1 invalid (will reply), 1 ambiguous (asking).` In auto mode, proceed unless the user objects; otherwise wait for confirmation.

## Step 3 — Apply fixes (valid comments only)

- One minimal edit per valid comment. Do not refactor surrounding code, do not bundle unrelated improvements ("while I'm in here…"), do not add comments narrating the fix.
- After all edits, run the project's local gate (lint + type + tests). If the project has a cheap E2E target, run it too.
- If a fix breaks tests, that's a signal — re-evaluate. Either the comment was wrong, or the fix was too aggressive. Don't push broken code.

## Step 4 — Commit + push

```bash
git add <changed files by name>
git commit -m "fix: address Greptile review feedback

- <one bullet per fix, referencing the file and concern>
"
git push
```

Capture the new commit short SHA — you'll cite it in replies.

## Step 5 — Reply + resolve, per comment

Loop over every unresolved Greptile comment from Step 1:

- **Valid (fixed)** — reply briefly with the commit hash, then **resolve the thread**.
   ```bash
   gh api repos/<o>/<r>/pulls/<pr>/comments/<comment_id>/replies \
     -f body="Valid — fixed in <short-sha>. <one-line on what changed>."
   gh api graphql \
     -f query='mutation($id:ID!){resolveReviewThread(input:{threadId:$id}){thread{isResolved}}}' \
     -F id=<threadId>
   ```
- **Invalid** — reply with the brief reason. **Do NOT resolve** — leaving the thread open keeps the disagreement visible to Greptile and human reviewers.
   ```bash
   gh api repos/<o>/<r>/pulls/<pr>/comments/<comment_id>/replies \
     -f body="Intentional — <1-2 sentence reason>."
   ```
- **Ambiguous** — already handled in Step 2 (asked the user). Skip here.

Reply style: respectful, terse, concrete. Lead with one of `Valid — …` / `Good catch — …` / `Intentional — …` / `Handled by … upstream, not needed here.` / `Out of scope — tracked separately.` Never dismissive. Never longer than 2 sentences.

## Step 6 — Re-request review

```bash
gh pr comment <pr> --body "@greptileai review this and update the score"
```

## Step 7 — Report and stop

Print to the user:
- Comments fixed + resolved: **N**
- Comments declined (replied, thread left open): **M**
- New commit: **`<short-sha>`**
- Re-review requested.
- **Next**: the skill does not poll. Rerun `/greptile-resolve <pr>` after Greptile finishes the next round if the score still isn't 5/5, or accept a deliberate <5/5 with a justification comment and merge.

## Guardrails

- **Never resolve a thread you didn't fix.** Resolution is the signal that the concern is settled in the code. Resolving an unfixed thread is a lie to the reviewer and to humans browsing the PR later.
- **Never silently dismiss a comment.** Every unresolved Greptile comment in scope gets either fix+reply+resolve OR reply-with-reason. Nothing left in limbo.
- **Don't loop.** If after this pass the score still isn't 5/5, surface that and stop. Looping autonomously belongs in `epic-loop`, not here.
- **Don't over-fix.** Greptile occasionally suggests big refactors disguised as bug reports. Make the minimal fix that addresses the cited concern; out-of-scope improvements get a polite `Out of scope — …` reply, not a quiet refactor.
- **Investigate the code, not just the comment.** Read the file, grep for callers if needed, run the failing test if there is one. Drive-by verdicts produce drive-by replies.
- **Never commit secrets**; confirm `.env`/keys aren't in the diff before `git push`.
