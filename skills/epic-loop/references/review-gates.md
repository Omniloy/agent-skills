# Review-gate "done" signals

How to recognise a PR is mergeable, per gate. `scripts/backlog.py review-status --gate <…>` checks these and prints `DONE | PENDING | COMMENTS | FAILED`.

## Greptile (default)
Greptile has **finished a review round** when it added **no new inline comment** AND it either
reacted **👍 (+1)** to your last `@greptileai review this and update the score` comment OR its
**Greptile Summary** in the PR body references the head commit. `review-status` reports:
- **`DONE`** — round complete AND `Confidence Score: 5/5` → **merge**.
- **`REVIEW_BELOW_5`** — round complete but the score is **< 5/5** (e.g. 4/5). Greptile is *done*
  (it 👍'd and added no inline comment) but the **summary explains why it's not 5/5**. Do NOT keep
  waiting (it will never reach 5/5 on its own). Read `reason_excerpt`, then **either fix the cited
  issue(s) and re-request** (push toward 5/5), **or**, if it's a deliberate/acceptable trade-off,
  **reply with the justification and merge**. A 4/5 with a "safe to merge with one small fix" note
  usually means: make that one fix, then merge.
- **`COMMENTS`** — new inline comments to address → `/review-pr`, commit, push, reply, re-request.
- **`PENDING`** — still reviewing → wait.

A `COMMENTED` review alone is NOT a verdict — always read the score (`score` field) + reaction, or
you waste review rounds (or hang forever on a 4/5).
To re-trigger after pushing fixes: `gh pr comment <n> --body "@greptileai review this and update the score"`.
Addressing comments: read each inline comment (`gh api repos/{o}/{r}/pulls/{n}/comments`), make the minimal fix, re-run the local gate, commit, push, **reply to each comment with the fix commit hash** (`gh api …/comments/{id}/replies -f body=…`), then re-trigger. Use `/review-pr <n>` to do this end-to-end.

## CodeRabbit
Similar shape: it posts a summary + inline comments and updates as you push. "Done" ≈ no unresolved actionable comments on the latest commit and its summary shows no outstanding issues. Re-trigger with `@coderabbitai review`. Treat resolved/▢ as not-actionable.

## CI-only (no review bot)
"Done" = all required status checks are green on the head commit AND no unresolved human/bot review comments. `review-status --gate ci` inspects `statusCheckRollup`. Don't merge while checks are `IN_PROGRESS/QUEUED`.

## Human
"Done" = `reviewDecision == APPROVED`. The loop pauses for the human; address requested changes via `/review-pr` and re-request review. Never auto-merge a human gate without an approval.

## Notes
- You cannot self-launch billed cloud reviews (e.g. `/code-review ultra`) — they're user-triggered. Don't build a loop around a gate you can't drive.
- Always honour the per-PR round cap; escalate to the human instead of looping forever.
