---
name: ship
description: End-to-end delivery conductor for one SOC task in the sofia-care monorepo. Front-loads every human decision (close the gaps the port reveals, refine the Jira task, then a visual plan you approve), then runs unattended - implement, open ONE PR whose title carries the SOC key, and drive the Greptile review to 5/5 or to a recorded agreement - and finishes with a published visual recap. Composes feature-to-tasks (refinar), visual-plan, the review-pr loop and visual-recap. Triggers on - "/ship SOC-130", "implementa SOC-132 y llévala a 5/5", "ship this task end to end".
user-invocable: true
---

# ship — sofia-care

Hand `ship` a SOC task and walk away once you have approved the plan. It closes the
gaps the port reveals, refines the ticket, gets a visual plan signed off — and
**only then** runs unattended: implements, opens one PR, and iterates on the
Greptile review until 5/5 or a recorded agreement, then publishes a visual recap.

The design rule that makes this safe: **the loop runs while you are away, so it
cannot stop to ask you anything.** Every human decision is front-loaded into the
kickoff. After you approve the plan, no more questions.

## What is different here

This is the generic `ship` adapted to a regulated monorepo that is being filled by
porting code that already runs.

| Generic `ship` | Here |
| --- | --- |
| Creates a ticket from free text | **Never.** Tickets come from `feature-to-tasks`; ship starts from one that exists |
| `CHECKS` inferred from CI | Read from the ticket's `### Comprobación` — the task already says how it is checked |
| PR links the ticket with `Closes` | **The SOC key rides in the branch name and the PR title.** That is what Jira links on |
| Terminal is 5/5 | 5/5 **or** a recorded agreement — see Phase 4 |
| Implements a design | Implements a **port**, a **rewrite** or something **new**, and the ticket says which |

## Input

```
/ship SOC-130
/ship https://omniloy.atlassian.net/browse/SOC-130
```

A SOC `Tarea` key. Nothing else. Free text with no key is not an entry point: if the
work has no ticket, it has no requirement and no Verification, which in a class II
product is the whole problem. Say so and point at `feature-to-tasks`.

## The three acts

```
ACT 1 — KICKOFF (attended, human gates)
  Phase 0  resolve the task + repo profile (BASE / CHECKS)
  Phase 1  close the gaps the port reveals   → visual questionnaire, you answer
  Phase 2  refine the ticket                 → preview, you approve, then write to Jira
  Phase 3  visual plan                       → you approve  ← last human gate
ACT 2 — BUILD + GATE (unattended, no questions)
  Phase 4  implement → PR against BASE → Greptile loop to 5/5 or agreement
ACT 3 — CLOSEOUT
  Phase 5  publish visual recap → PR body carries plan + recap → stop (no merge)
```

> **Autonomy needs `/loop`.** Phase 4 self-paces with `ScheduleWakeup`, which only
> fires under `/loop` dynamic mode. Unattended runs: `/loop /ship SOC-130`.

---

## Phase 0 — Resolve the task and the repo profile

1. **Fetch the task** with `["summary","description","labels","status","issuelinks","comment"]`.
   Parse its seven sections. A ticket that does not match the standard is a stop, not
   something to reformat on the fly — send it to `feature-to-tasks refinar` first.
2. **Read `### Punto de partida` and hold the kind** — `Porte`, `Reescritura` or
   `Nuevo`. Everything downstream depends on it: what you read first, and what
   "done" means.
3. **`CHECKS` = the ticket's `### Comprobación`, verbatim.** Do not invent commands
   and do not run the repo's full battery. If a listed `just` recipe does not exist
   yet, that is a finding: report it, do not substitute `pnpm`/`uv`/`pytest` by hand.
4. **`BASE`** — the integration branch, read from the repo
   (`gh repo view --json defaultBranchRef`, `git remote show origin`). Prefer
   `develop`, else `dev`; never `main` unless it is genuinely the only one.
5. **The origin clones are read-only.** `~/Workspace/Omniloy/Sofia/sofia-*` are the
   source of truth for what the code does today. Never branch, commit or push there.

## Phase 1 — Close the gaps the port reveals

The ticket already says what to do. What it cannot know is what you find when you
open the origin code. That is where the real questions are.

1. **Read the origin.** Every path in `### Punto de partida`, in the clone. If a path
   is not there, stop — the ticket is wrong and belongs in `refinar`, not in a guess.
2. **Read the destination.** The monorepo package, its neighbours, the `Patrón a
   seguir` line.
3. **Raise only gaps that change the implementation or the acceptance criteria.**
   In a port they are almost always one of these:
   - the origin does more than the ticket's `IN` and the boundary is unclear
   - the origin depends on something not yet ported, so this task needs a shim or a
     `Blocks` that nobody declared
   - a monorepo rule in "cambia al traerlo" has two reasonable readings
   - dead or duplicated code in the origin: port it, drop it, or ask
   - for a `Reescritura`, a behaviour whose equivalent in the new structure is a real
     decision rather than a translation
4. **Ask via the visual questionnaire** (`get-plan-blocks` first, then
   `create-visual-questions`, 2–6 questions with concrete options). Surface the URL;
   read answers with `visual-answer` / `get-plan-feedback`.

## Phase 2 — Refine the ticket (with approval)

The ticket has to reflect what you now know before any code exists.

Use **`feature-to-tasks refinar`** — it owns the standard and its guardrail. In
short: rewrite the sections as the current agreed state, never append an update log,
show a section-by-section diff, one `AskUserQuestion`, then `editJiraIssue` plus a
short comment saying what moved and why.

**The guardrail is the point.** If what you learned adds a bullet to `IN`, that is
not a refine — it is a new task. Say so and offer to open it. And a threshold never
moves to match what you managed to build: it came from the Feature, the Feature came
from the requirement, and the requirement is what gets audited.

## Phase 3 — Visual plan (the approval gate)

Author a structured visual plan with the `visual-plan` skill (read its `references/`
first). Lead with what already exists — for a port that means the origin files and
what maps where, not a design. Pin the hard-to-reverse decisions. Always include
explicit test steps, and for a `Porte` name **the origin suite that has to pass
unchanged**: that is the parity criterion and it is the plan's spine.

Publish, surface the URL, set visibility, keep the URL. One `AskUserQuestion`
(Approve / Approve with changes / Cancel).

**This is the last human gate.** No code before it. After approval, Act 2 is
unattended.

## Phase 4 — Implement and drive the review (unattended)

Idempotent: on every wake-up read the real state — branch, PR, HEAD sha, last
Greptile review — and deduce where you are. The PR is the state, not memory. Repeat
Phase 0 on each wake-up in case context was lost.

1. **No branch/PR yet?** `git fetch origin $BASE`, branch **`soc-130-fuentes-de-chat`**
   from `origin/$BASE` — the SOC key leads the branch name. Implement the approved
   plan, run `CHECKS`, fix everything, commit, push, and `gh pr create --base "$BASE"`.

   **The PR title carries the key**, because that is what links the PR to Jira:

   ```
   SOC-130 db: fuentes de chat, plantillas y notas del resumen
   ```

   The body carries the traceability, which is what an auditor follows:

   ```
   SOC-130 · SOC-1 (RF-M1-001)
   Verificación: SOC-65
   Riesgos: R-01

   📐 Plan:  <visual-plan URL>
   📋 Recap: <pendiente>
   ```

   Schedule the next wake-up (~270s) to give Greptile time.
2. **PR exists — still mergeable?** `gh pr view <n> --json mergeable,mergeStateStatus`.
   If `CONFLICTING`/`DIRTY`: fetch, rebase on `origin/$BASE`, resolve, re-run
   `CHECKS`, `git push --force-with-lease`.
3. **For a `Porte`, parity is a gate, not a nicety.** Before the first push, run the
   origin's own suite against the ported code with its expectations unchanged. If it
   cannot run, say why in the PR — a port whose parity was never demonstrated is a
   claim, not evidence.
4. **Has Greptile reviewed HEAD?** Compare the PR's HEAD sha to the latest
   `greptile-apps` review. If not, post `@greptileai review` once, sleep ~270s, exit.
5. **Reviewed HEAD — run one round of the `review-pr` loop:** read the inline
   comments and the Confidence Score, verify each **against the actual code** (read
   the file, grep the callers — verdicts from comment text alone are wrong half the
   time), fix the valid ones and resolve their threads, reply-with-reason to the
   invalid ones and leave them open, run `CHECKS`, commit, push, re-request review,
   sleep ~270s.
6. **Terminal condition — 5/5 or agreement.**
   - **5/5** with no unresolved threads, or
   - **agreement**: every remaining comment has a reasoned reply, none of them is a
     valid finding, and the position survived a re-review without new P1/P2. Record
     the open points in the PR body so the disagreement is visible rather than lost.

   Some Python repos emit no `N/5`. There the practical terminal is the
   `Greptile Review` check green with 0 inline comments, sustained across a
   re-review. Do not burn rounds waiting for a number that never comes.

**Safety cap: 8 rounds**, counted from the `@greptileai` re-review requests on the
PR. Hit the cap without a terminal condition → stop and report what is outstanding.
An **ambiguous** comment is a hard stop: escalate, do not keep cycling.

## Phase 5 — Visual recap and finalize

Only after the terminal condition:

1. **Squash to one commit**: `git reset --soft $(git merge-base HEAD origin/$BASE)`,
   re-commit with a clean message (`SOC-130 <qué se portó>`, body: the Greptile
   points addressed), the usual `Co-Authored-By:` line, `git push --force-with-lease`.
2. **Publish a visual recap** with the `visual-recap` skill (read its `references/`).
   For a port, the recap that matters shows what moved and what changed on the way —
   origin → destination, plus the parity evidence.
3. **Finalize the PR body**: same block as Phase 4 with the recap URL filled in.
4. **Done.** Stop the loop. Report the PR link, the terminal condition reached, that
   it is squashed, and the plan + recap links.

**Never merge.** And the task is not verified by this run: SOC-65 and its siblings
are executed separately with `live-testing-plan`. Shipping the code and proving the
requirement are two different signatures on purpose.

## Guardrails

- **All human input is front-loaded.** Once Act 2 starts, the loop never asks. An
  ambiguous review comment halts it rather than guessing.
- **Start from a ticket.** No ticket, no run. Point at `feature-to-tasks`.
- **`CHECKS` come from the ticket.** Never a hand-rolled `pnpm`/`uv`/`pytest`.
- **The origin repos are read-only.** Never commit into `sofia-*`.
- **Never write to Jira without approval** of the exact preview, and never widen `IN`.
- **Never push to `BASE`.** Always a branch plus a PR, branched from freshly-fetched
  `origin/$BASE`.
- **The SOC key rides in the branch name and the PR title.** That link is the
  traceability, and a PR that does not carry it is invisible from the requirement.
- **Never auto-merge**, and never mark a requirement verified from here.
- **Run `CHECKS` before every commit.**
- **Don't over-fix the reviewer.** Minimal edit per valid comment; out-of-scope
  suggestions get a reply, not a quiet refactor. Never resolve a thread you did not fix.
- **Never commit secrets.** Confirm `.env` and keys are not in the diff before pushing.
