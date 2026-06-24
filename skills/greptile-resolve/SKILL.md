---
name: greptile-resolve
description: Respond to a Greptile PR review in one of two modes - SINGLE pass (read comments + Confidence Score once, verify each against the repo code, fix valid ones + resolve their threads, reply with a reason to invalid ones, push, re-request review, exit) or LOOP until 5/5 (same per-round cycle, then self-paced ScheduleWakeup until Confidence Score is 5/5 with no unresolved threads, capped at max rounds). The mode is chosen at the start - explicitly via the `loop` / `single` argument, otherwise asked. Triggers on - "/greptile-resolve 220", "/greptile-resolve 220 loop", "address the greptile comments", "drive this PR to 5/5", "respond to greptile until it gives 5/5".
user-invocable: true
---

# greptile-resolve

Make a clean, code-aware response to a Greptile review: read the comments + Confidence Score, judge each against the actual code, fix the valid ones (and resolve their threads), reply to the invalid ones with a short reason, push, and re-request the review. Then either **stop** (single pass) or **self-pace until the score is 5/5** (loop mode). The user chooses the mode at the start.

This is the Greptile-specific cousin of `/review-pr`. Pick the right one:

| If… | Use |
|---|---|
| Reviewer is Greptile, want one focused round, then return control | `/greptile-resolve <pr> single` |
| Reviewer is Greptile, want to keep iterating until **5/5** without babysitting | `/greptile-resolve <pr> loop` |
| Mixed/human reviewers, or you want to approve a plan before any code is changed | `/review-pr` |
| You want to drive a whole backlog (many PRs) end-to-end | `epic-loop` (uses this skill internally per round) |

## Input

PR number or full URL, optionally with a mode flag:
- `/greptile-resolve 220` — mode unspecified, the skill will ask.
- `/greptile-resolve 220 single` (or `--single`) — one pass, then exit.
- `/greptile-resolve 220 loop` (or `--loop`) — keep iterating until 5/5 or the round cap.
- `/greptile-resolve https://github.com/Omniloy/maria-voice/pull/220 loop`

If only a number is given, infer the repo from the current git remote (`gh repo view --json nameWithOwner -q .nameWithOwner`).

## Step 0 — Choose the mode

1. If the input contains `single`/`--single` → **single-pass** mode. Skip to Step 1.
2. If the input contains `loop`/`--loop` → **loop** mode. Continue at 4.
3. Otherwise — ask the user **once**, briefly: *"Single pass (one round, then return) or loop until 5/5 (self-paced, capped at N rounds)? Default: single."* Wait for the answer, record the choice, continue.
4. **Loop-mode setup** (only when in loop mode):
   - **Round cap**: default **5** rounds. (The cap is per skill invocation, not per PR — it counts how many times this skill has driven Greptile through a fix→re-review cycle in the current session, not how many `@greptileai` comments exist in total.) Honour the cap to avoid infinite re-review storms.
   - **Self-pacing requires `/loop`**: `ScheduleWakeup` only fires under `/loop` dynamic mode. If the skill was invoked plainly (not under `/loop`), tell the user: *"Loop mode needs to run under `/loop` to self-pace. Re-invoke as `/loop /greptile-resolve <pr> loop`."* and stop. (Once the user does so, the skill re-enters at Step 0 with loop pre-selected.)
   - **Track the round count statelessly** — count the number of `@greptileai review this and update the score` issue comments on the PR authored by the running user. That count == rounds already completed. (No state file needed.)

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
5. **Already at 5/5 with no open Greptile threads?** Print `PR is already at 5/5 — nothing to do.` and exit (both modes). Don't push an empty commit, don't re-request review.
6. **Loop mode — Greptile still reviewing?** If you can see your most recent `@greptileai` request but Greptile hasn't posted any new comment or updated the summary's commit reference since, the round isn't finished — **skip to Step 7** (don't re-fix, don't re-request). The wakeup will fire again.

## Step 2 — Verify each comment against the repo

For each unresolved Greptile comment:

1. **Open the actual file** at the cited path and read enough surrounding context (callers, related helpers, tests). Grep for the symbol if the concern depends on how it's used elsewhere. **Verdicts based on the comment text alone are wrong half the time** — always read code first.
2. Classify:
   - **Valid** — the code really has the problem Greptile describes (bug, missing check, leak, race, broken contract, security issue, etc.). Plan the minimal fix.
   - **Invalid** — Greptile is wrong: misread the code, flagged an intentional choice, suggested an out-of-scope refactor, or its assumption doesn't hold (e.g. the input is already validated upstream, the framework handles it, the path is guarded by a feature flag, etc.).
   - **Ambiguous** — you genuinely can't tell from the code alone. **Pause and ask the user** before continuing; don't guess. (Auto mode is not a license to commit a fix you're unsure about. In loop mode, an ambiguous comment is also a hard stop — escalate, don't keep cycling.)
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

## Step 7 — Report, then exit OR sleep

Always print the round summary:
- Comments fixed + resolved: **N**
- Comments declined (replied, thread left open): **M**
- New commit: **`<short-sha>`** (or `none` if Step 6 was the only outward action)
- Re-review requested: **yes/no**
- Mode: **single / loop (round R of <cap>)**

Then branch on mode:

### Single-pass mode → **stop**
Add a one-liner: *"Skill does not poll. Rerun `/greptile-resolve <pr>` (or with `loop`) after Greptile finishes the next round if the score still isn't 5/5."* Exit.

### Loop mode → **sleep and re-enter**
1. **Stop if you can.** Re-check Step 1's exit conditions inline:
   - Score is **5/5** with no unresolved threads → success. Print `✅ PR <pr> reached 5/5 after R round(s).` and exit.
   - Round cap exceeded (R ≥ cap) → escalate. Print `⛔ Hit the round cap (R) without reaching 5/5. Current score: X/5. Open threads: …` and exit so the user can decide whether to merge with a justification, fix manually, or raise the cap.
   - An ambiguous comment was found in Step 2 → already paused there; do not sleep.
2. **Otherwise, sleep.** Call `ScheduleWakeup` to re-enter:
   - `delaySeconds`: **270** while waiting for Greptile to post its next round (stays inside the prompt-cache TTL).
   - `prompt`: the exact `/loop /greptile-resolve <pr> loop` invocation (verbatim) so the next firing re-enters this skill in loop mode.
   - `reason`: e.g. `"waiting for greptile round R+1 on PR <pr>"`.
3. Tell the user what's happening: *"Slept 270s; will re-check after Greptile's next round. Interrupt anytime."* Exit this turn.

## Guardrails

- **Never resolve a thread you didn't fix.** Resolution is the signal that the concern is settled in the code. Resolving an unfixed thread is a lie to the reviewer and to humans browsing the PR later.
- **Never silently dismiss a comment.** Every unresolved Greptile comment in scope gets either fix+reply+resolve OR reply-with-reason. Nothing left in limbo.
- **Honour the round cap in loop mode.** Default 5. When the cap is hit, escalate to the user with the current score + open threads — don't quietly keep going. Loops without caps cost money and trust.
- **Loop mode never auto-merges.** Reaching 5/5 ends the loop and reports success. The decision to merge belongs to the user (or to `epic-loop`, which calls this skill as a sub-step).
- **Don't over-fix.** Greptile occasionally suggests big refactors disguised as bug reports. Make the minimal fix that addresses the cited concern; out-of-scope improvements get a polite `Out of scope — …` reply, not a quiet refactor.
- **Investigate the code, not just the comment.** Read the file, grep for callers if needed, run the failing test if there is one. Drive-by verdicts produce drive-by replies.
- **Never commit secrets**; confirm `.env`/keys aren't in the diff before `git push`.
