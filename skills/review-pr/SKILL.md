---
name: review-pr
description: Respond to code review comments on a GitHub PR. Analyzes each comment, presents a plan, implements fixes, replies to comments, and requests re-review. Usage - "/review-pr 220" or "/review-pr https://github.com/org/repo/pull/220".
user_invocable: true
---

# review-pr

Respond to automated or human code review comments on a GitHub PR. Analyze each comment, present a plan for approval, implement fixes, reply on GitHub, and request re-review.

## Input

The user provides a PR number or full PR URL as an argument. Examples:
- `/review-pr 220`
- `/review-pr https://github.com/Omniloy/maria-voice/pull/220`

## Step 1: Fetch PR context

1. If a full URL is provided, extract the `owner/repo` and PR number. If only a number is provided, infer the repo from the current git remote (`gh repo view --json nameWithOwner -q .nameWithOwner`).
2. Fetch PR details:
   ```bash
   gh pr view <number> --json title,body,headRefName,baseRefName
   ```
3. Fetch all review comments:
   ```bash
   gh api repos/<owner>/<repo>/pulls/<number>/comments --jq '.[] | {id, path, line: (.line // .original_line), body}'
   ```
4. Also fetch issue-level comments (some reviewers like Greptile post a summary there):
   ```bash
   gh api repos/<owner>/<repo>/issues/<number>/comments --jq '.[] | {id, user: .user.login, body}'
   ```
5. Read each file mentioned in the comments to understand the full context.

## Step 2: Analyze and present plan

For each review comment, analyze it against the actual code and classify it:

Present the analysis to the user in this format:

```
## PR Review Response Plan

### Comment 1: <short title>
- **File:** `path/to/file.py:42`
- **Reviewer says:** <1-line summary of the comment>
- **Verdict:** Valid / Partially valid / Not valid
- **Reasoning:** <1-2 sentences explaining why>
- **Action:** <what you will do — e.g. "Add cache check before download" or "No change needed, will explain in reply">

### Comment 2: <short title>
...

### Summary
- **Fixing:** X comments
- **Declining:** Y comments (with explanation)

Proceed? (y/n)
```

**IMPORTANT:** Wait for user approval before proceeding to Step 3. Do NOT implement anything until the user confirms.

## Step 3: Implement fixes

For each comment marked as valid or partially valid:

1. Read the relevant file(s)
2. Make the minimal fix that addresses the concern
3. Do NOT over-engineer or add unrelated changes

## Step 4: Commit and push

1. Stage only the changed files (by name, not `git add -A`)
2. Commit with a message like:
   ```
   fix: Address code review feedback

   - <bullet for each fix>

   Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
   ```
3. Push to the PR branch

## Step 5: Reply to comments on GitHub

For each review comment, reply via the GitHub API:

```bash
gh api repos/<owner>/<repo>/pulls/<number>/comments/<comment_id>/replies -f body="<response>"
```

Reply format guidelines:
- **Valid comments that were fixed:** Start with "Valid" or "Good catch". Reference the fix commit hash. Briefly describe what was changed.
- **Partially valid:** Acknowledge the valid part, explain what was fixed and what wasn't (and why).
- **Not valid:** Be respectful. Explain why the concern doesn't apply or why the current approach is intentional. Use phrases like "Intentional — ..." or "This is handled by ..." rather than dismissive language.

Keep replies concise — 1-3 sentences max.

## Step 6: Request re-review

If the reviewer is an automated bot (like Greptile), post a comment requesting re-review:

```bash
gh pr comment <number> --body "@greptileai review this and update the score"
```

If it's a human reviewer, request a re-review via GitHub:

```bash
gh api repos/<owner>/<repo>/pulls/<number>/requested_reviewers -f "reviewers[]=<username>"
```

## Important notes

- Always read the actual code before judging a comment — don't assume the reviewer is right or wrong
- If a comment suggests a change that would break existing functionality, flag it to the user
- Group all fixes into a single commit unless they are logically unrelated
- Never dismiss valid security or correctness concerns
- If unsure about a comment's validity, err on the side of presenting it as "needs discussion" and let the user decide
