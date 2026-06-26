---
name: review-pr
description: The comprehensive, autonomous PR resolver. Takes a GitHub PR to a clean, mergeable state by resolving EVERYTHING the PR carries — drives a Greptile review to 5/5, fixes failing CI/CD checks (GitHub Actions), addresses human reviewer comments and other bots (incl. Supabase advisors/lints), verifying each against the actual code, applying minimal fixes, replying per comment, resolving threads, and re-requesting review. Runs in SINGLE mode (one pass, then exit) or LOOP mode (self-paced via ScheduleWakeup until the PR is green, capped at N rounds). Always autonomous — it never stops to ask for fix approval; an ambiguous item is the only escalation. Triggers on - "/review-pr 220", "/review-pr 220 loop", "address the PR comments", "fix the failing checks on this PR", "drive this PR to green / to 5/5", "respond to greptile until 5/5".
user-invocable: true
---

# review-pr

Take a GitHub PR all the way to mergeable. This is the single skill that resolves **everything a PR carries**:

- **Greptile review** → verify each comment against the code, fix the valid ones, resolve their threads, decline the invalid ones with a reason, drive the **Confidence Score to 5/5**.
- **CI/CD (GitHub Actions)** → read the failing checks and their logs, diagnose the root cause, fix it.
- **Human reviewers & other bots** (incl. **Supabase** advisors / lints / migration warnings) → classify each comment, fix valid ones, reply per comment.

It is **always autonomous**: it fixes and replies on its own and never pauses for fix-approval. The only thing that stops it is a genuinely **ambiguous** item — that escalates rather than guessing. Pick the cadence with the mode:

| If… | Use |
|---|---|
| One focused pass, then return control | `/review-pr <pr> single` |
| Keep iterating until the PR is green without babysitting | `/review-pr <pr> loop` |
| Drive a whole backlog (many PRs) end-to-end | `epic-loop` (uses this skill per round) |
| Ship one ticket from Jira to a 5/5 PR | `ship` (uses this skill for its gate) |

## Input

PR number or full URL, optionally with a mode flag:
- `/review-pr 220` — mode unspecified, the skill will ask.
- `/review-pr 220 single` (or `--single`) — one pass, then exit.
- `/review-pr 220 loop` (or `--loop`) — keep iterating until green or the round cap.
- `/review-pr https://github.com/Omniloy/maria-voice/pull/220 loop`

If only a number is given, infer the repo from the current git remote (`gh repo view --json nameWithOwner -q .nameWithOwner`). If a full URL is given, extract `owner/repo` and the PR number from it.

## Step 0 — Choose the mode

1. `single`/`--single` in the input → **single-pass**. Skip to Step 1.
2. `loop`/`--loop` in the input → **loop**. Continue at 4.
3. Otherwise ask **once**, briefly: *"Single pass (one round, then return) or loop until green (self-paced, capped at N rounds)? Default: single."* Record the choice, continue.
4. **Loop-mode setup** (loop only):
   - **Round cap**: default **5** rounds. The cap is per invocation — count how many `@greptileai review …` issue comments authored by the running user exist on the PR; that count == rounds already completed. Honour it to avoid infinite re-review storms.
   - **Self-pacing requires `/loop`**: `ScheduleWakeup` only fires under `/loop` dynamic mode. If invoked plainly, tell the user: *"Loop mode needs to run under `/loop` to self-pace. Re-invoke as `/loop /review-pr <pr> loop`."* and stop. (Once they do, the skill re-enters at Step 0 with loop pre-selected.)
   - **State is the PR, not memory.** Every wake-up, re-read the live PR state (Step 1) and deduce where you are.

## Step 1 — Gather the full PR state (the resolve surface)

Read everything the PR carries, in one pass. This is the inventory you will work through.

1. **PR meta**:
   ```bash
   gh pr view <pr> --json title,body,headRefName,baseRefName,headRefOid,mergeable,mergeStateStatus
   ```

2. **CI/CD checks (GitHub Actions + any status checks)** — the failing ones are work items:
   ```bash
   gh pr checks <pr>                                              # human-readable pass/fail
   gh api repos/<o>/<r>/commits/<headRefOid>/check-runs \
     --jq '.check_runs[] | {name, status, conclusion, details_url, id}'
   ```
   For each `conclusion` that is `failure`/`timed_out`/`cancelled`, pull the failing job's log to find the real cause (not just the red X):
   ```bash
   gh run view <run-id> --log-failed         # run-id from the check's details_url / `gh run list`
   ```
   Record per failing check: which job/step failed and the root-cause line(s).

3. **Greptile state** (if Greptile reviews this repo):
   - **Confidence Score** + rationale — parse `Confidence Score: N/5` and its paragraph from the latest `greptile-apps` review body (`gh api repos/<o>/<r>/pulls/<pr>/reviews`). The rationale usually names the specific concern keeping it below 5/5 — that's your highest-priority comment.
   - **Inline comments by Greptile**, only those created after your last `@greptileai` re-review request (older ones are already addressed):
     ```bash
     gh api repos/<o>/<r>/issues/<pr>/comments \
       --jq '[.[] | select(.body|contains("@greptileai"))] | last | .created_at'   # last re-review request
     gh api repos/<o>/<r>/pulls/<pr>/comments \
       --jq '.[] | select(.user.login=="greptile-apps[bot]") | {id, node_id, path, line:(.line // .original_line), body, created_at, in_reply_to_id}'
     ```
     Keep only comments whose `created_at` is after the last `@greptileai` request.

4. **Human reviewers & other bots** — every inline + summary review comment NOT from Greptile, including **Supabase** advisors/lints and any other integration:
   ```bash
   gh api repos/<o>/<r>/pulls/<pr>/comments \
     --jq '.[] | select(.user.login!="greptile-apps[bot]") | {id, node_id, user:.user.login, path, line:(.line // .original_line), body, in_reply_to_id}'
   gh api repos/<o>/<r>/issues/<pr>/comments \
     --jq '.[] | {id, user:.user.login, body}'      # summary-level comments (some bots post here)
   ```
   Skip your own replies and items already answered (an inline comment that already has your reply in its thread).

5. **Map each inline comment to its review thread** (needed to resolve it later):
   ```bash
   gh api graphql -F owner=<o> -F repo=<r> -F pr=<pr> -f query='
     query($owner:String!,$repo:String!,$pr:Int!){
       repository(owner:$owner,name:$repo){ pullRequest(number:$pr){
         reviewThreads(first:100){ nodes{ id isResolved comments(first:20){ nodes{ databaseId } } } } } } }'
   ```
   Build `{comment_databaseId → threadId}`. Skip threads where `isResolved == true`.

6. **Already clean?** If — all CI checks green, **and** Greptile is 5/5 (or the no-score terminal in Step 7), **and** no unresolved human/bot threads — print `PR is already mergeable — nothing to do.` and exit (both modes). Don't push an empty commit or re-request review.

7. **Loop mode — still waiting on a reviewer/CI?** If your most recent `@greptileai` request has no newer Greptile response, or CI is still `in_progress`/`queued` on HEAD, the round isn't finished — **skip to Step 7** (don't re-fix, don't re-request). The wake-up fires again.

You now have one combined work list: failing checks, Greptile comments, human/bot comments.

## Step 2 — Verify each item against the repo (don't trust the text)

Work through the list. For every comment **read the actual code first** — open the cited file, read surrounding context (callers, helpers, tests), grep the symbol if the concern depends on usage elsewhere. **Verdicts from comment text alone are wrong half the time.**

Classify each item:

- **Valid** — the problem is real (bug, missing check, leak, race, broken contract, security issue, failing assertion). Plan the minimal fix.
- **Invalid** — the reviewer is wrong: misread the code, flagged an intentional choice, suggested an out-of-scope refactor, or the assumption doesn't hold (input already validated upstream, framework handles it, guarded by a flag). Plan a brief decline reply.
- **Ambiguous** — you genuinely can't tell from the code. **Stop and ask the user** before continuing; never guess. In loop mode an ambiguous item is a **hard stop** — escalate, don't keep cycling.

For **CI/CD failures**, "valid" is the default — a red check is a real failure. Diagnose from the log: lint/format → run the repo's formatter/linter; type error → fix the types; failing test → fix the code or the test (decide which is wrong from the assertion); build/dependency → fix config. A **flaky/infra** failure unrelated to the diff is the CI equivalent of "invalid" — re-run it (`gh run rerun <run-id> --failed`) rather than editing code, and note it.

For **Supabase** advisors (security/performance lints, RLS warnings, missing indexes, migration drift): treat like any reviewer comment — verify against the actual migration/schema, fix valid ones, decline false positives with a reason.

Cross-reference the **Greptile Confidence Score rationale** — a 4/5 with one specific gripe usually points at the single blocking comment; fixing that is often what moves the score to 5/5.

Give the user a one-line summary before editing, e.g. `2 failing checks (will fix), 5 comments: 3 valid (fix), 1 invalid (reply), 1 Supabase RLS (fix).` Then proceed (autonomous — don't wait for approval) unless an item is ambiguous.

## Step 3 — Apply fixes (valid items only)

- One **minimal** edit per valid item. Do not refactor surrounding code, do not bundle "while I'm in here" improvements, do not add comments narrating the fix.
- After all edits, run the project's **local gate** — replicate what CI enforces (`.github/workflows/*` is the contract): lint, format check, type check, tests; cheap E2E if the repo has one. The goal is that the checks you saw red in Step 1 now pass locally.
- If a fix breaks tests, that's a signal — re-evaluate. Either the comment was wrong or the fix was too aggressive. Never push broken code.

## Step 4 — Commit + push

```bash
git add <changed files by name>     # never git add -A
git commit -m "fix: address PR review feedback and CI

- <one bullet per fix, referencing the file/check/concern>

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
git push
```

Capture the new commit short SHA — you cite it in replies.

## Step 5 — Reply + resolve, per item

- **Greptile valid (fixed)** — reply with the commit hash, then **resolve the thread**:
  ```bash
  gh api repos/<o>/<r>/pulls/<pr>/comments/<comment_id>/replies \
    -f body="Valid — fixed in <short-sha>. <one line on what changed>."
  gh api graphql -F id=<threadId> \
    -f query='mutation($id:ID!){resolveReviewThread(input:{threadId:$id}){thread{isResolved}}}'
  ```
- **Greptile invalid** — reply with the brief reason. **Do NOT resolve** — an open thread keeps the disagreement visible to Greptile and humans.
- **Human / other-bot comment** — reply on its thread the same way (`/replies`), referencing the fix commit when you fixed it or the reason when you declined. Resolve the thread only if you fixed it AND the repo's convention is for the responder to resolve; otherwise leave it for the human to resolve.
- **Supabase advisor** — reply on the thread (or the summary comment) with the fix commit or the decline reason.
- **CI/CD failure** — no reply needed (there's no thread); the green check on the next run is the answer.
- **Ambiguous** — already handled in Step 2 (asked / escalated). Skip here.

Reply style: respectful, terse, concrete. Lead with `Valid — …` / `Good catch — …` / `Intentional — …` / `Handled by … upstream.` / `Out of scope — tracked separately.` Never dismissive, never longer than 2 sentences.

## Step 6 — Re-request review / re-trigger checks

- Greptile reviewed and you pushed fixes → re-request:
  ```bash
  gh pr comment <pr> --body "@greptileai review this and update the score"
  ```
- A human reviewer needs to look again:
  ```bash
  gh api repos/<o>/<r>/pulls/<pr>/requested_reviewers -f "reviewers[]=<username>"
  ```
- CI didn't auto-run on the new push (rare) or a check was flaky → `gh run rerun <run-id> --failed`. A normal push re-triggers Actions on its own.

## Step 7 — Report, then exit OR sleep

Always print the round summary:
- CI checks fixed: **N** (still red: **list**)
- Comments fixed + resolved: **N** · declined (replied, left open): **M** · ambiguous (escalated): **K**
- New commit: **`<short-sha>`** (or `none`)
- Re-review requested: **yes/no**
- Mode: **single / loop (round R of <cap>)**

Then branch on mode:

### Single-pass → **stop**
Add: *"Skill does not poll. Rerun `/review-pr <pr>` (or with `loop`) after the next CI run / review round if it isn't green yet."* Exit.

### Loop → **sleep and re-enter**
1. **Stop if you can** — re-check the terminal condition inline:
   - **All CI green, Greptile 5/5 (or the no-score terminal below), no unresolved threads** → success. Print `✅ PR <pr> is green after R round(s).` and exit.
   - Round cap exceeded (R ≥ cap) → escalate. Print `⛔ Hit the round cap (R) without going green. Score: X/5. Failing checks: … Open threads: …` and exit so the user decides.
   - An ambiguous item was found → already paused in Step 2; do not sleep.
2. **Otherwise sleep.** `ScheduleWakeup`:
   - `delaySeconds`: **270** while waiting for a Greptile round or a CI run to finish (inside the prompt-cache TTL).
   - `prompt`: the exact `/loop /review-pr <pr> loop` invocation (verbatim) so the next firing re-enters in loop mode.
   - `reason`: e.g. `"waiting for greptile round R+1 / CI on PR <pr>"`.
3. Tell the user: *"Slept 270s; will re-check after the next round. Interrupt anytime."* Exit this turn.

**No-score terminal (some Python repos emit no `N/5`):** when Greptile's review body is empty, the practical terminal is **all CI checks green + 0 new inline comments, sustained across a re-review**. Don't burn rounds waiting for a number that never comes.

## Guardrails

- **Always autonomous, but never reckless.** No approval gate for fixes — but an **ambiguous** item is a hard stop in both modes (escalate, don't guess). Reaching the terminal condition never auto-merges; merge is the user's call (or `epic-loop`'s, which calls this skill).
- **Investigate the code, not just the comment.** Read the file, grep callers, read the CI log's real failing line. Drive-by verdicts produce drive-by fixes.
- **Never resolve a thread you didn't fix.** Resolution is the signal the concern is settled. Resolving an unfixed thread lies to the reviewer.
- **Never silently dismiss anything.** Every in-scope comment gets fix+reply+resolve OR reply-with-reason. Every failing check gets fixed or explicitly flagged (flaky/infra). Nothing left in limbo.
- **Don't over-fix.** Minimal edit per item; out-of-scope suggestions get a polite `Out of scope — …` reply, not a quiet refactor.
- **Honour the round cap in loop mode** (default 5). Hitting it escalates with the current score + failing checks + open threads. Loops without caps cost money and trust.
- **Run the local gate before every push** and fix all of it — replicate what the repo's CI enforces so the red checks actually go green.
- **Never commit secrets**; confirm `.env`/keys aren't in the diff before `git push`.
