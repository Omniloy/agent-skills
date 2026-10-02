---
name: maria-prod-monitor
description: Monitor MarIA voice calls in production (or stg/dev) to catch new failures and regressions — a read-only pass over a time window that pulls logs and calls, runs deterministic L1 (transport/voice) and L2 (state/execution) detectors against a versioned error-code catalog, queues calls for an L3 semantic judge, and produces a case-by-case triage sheet plus a drafted alert; also live-watches a customer's pilot window after a workflow activation. Use when the user asks to check production calls, triage failures, look for regressions after a release or a flow version, watch a pilot, investigate a call, or maintain the error catalog / known-errors database. Part of MAR-1504. Read-only: Slack and Jira only with explicit approval.
user-invocable: true
---

# maria-prod-monitor

The alarm system of the MarIA workflow skills:
1. `maria-workflow-builder` builds and ships;
2. `maria-workflow-evals` checks through the voice channel;
3. **this skill** watches what real callers get, and feeds the findings back.

It packages the MAR-1504 pipeline: the collectors, a three-layer taxonomy, the catalog
discipline and the case-by-case triage. It adds the pilot watch that ran San Roque's first
production week.

## Where things live

| what | where |
|---|---|
| scripts (this skill) | `scripts/monitor.sh`, `scripts/collectors/*`, `scripts/argo/*`, `scripts/sina/reclassify_auth.py`, `scripts/pilot_watch.py`, `scripts/pilot_report.py` |
| shared memory | **`monitor.sqlite`** (`MARIA_MONITOR_DB`, default `~/.config/maria-monitor/monitor.sqlite`), managed by `scripts/collectors/monitor_store.py`: error codes and proposals, KEDB decisions, Jira tasks ↔ codes, and the history of every pass. Source of truth once it exists; the collectors read it first |
| seed / snapshot | maria-voice `docs/monitoring/*.yaml`: what the store was seeded from (`import-yaml`) and what `export-yaml` writes for a reviewable diff |
| private (patient data, credentials) | `~/.config/maria-monitor/` (`MARIA_MONITOR_PRIVATE`): `argo.env` (`ARGO_USR`/`ARGO_PASS`), `auth_review_*.json`, `state.json`. **Never in a repository** |
| paths | `scripts/collectors/_paths.py`: `MARIA_VOICE_ROOT`, `MARIA_REPOS_ROOT`, `MARIA_MONITOR_DATA`, `MARIA_CORE_ENV_PATH` override the autodetection |

## Read first

- `references/method.md`: the layers and the gate, the catalog rules, the KEDB / task
  decision for every case, one pass step by step, the L3 judge, regressions.
- `references/data-sources.md`: which branch runs where (**prod = `main`**), tables and their
  traps, logs (Argo only serves live pods; PRD Log Analytics is not accessible), PII.
- `references/pilot.md`: watching the first hours of a workflow in production.

## Hard rules

1. **Read-only.** Nothing may sync, restart or write to a customer's systems. SQL goes
   through PostgREST GET, logs through Argo GET or `az` queries.
2. **The only writes, each with an explicit OK:**
   - a Jira task or comment (MAR, under MAR-2);
   - writes to `monitor.sqlite` (proposals, decisions, task links, the run history);
   - a Slack post.

   `notify.py` drafts `slack.txt` and never sends it.
3. **Reason about production on `main`** (`collectors/deployed.py`), not on `dev`.
4. **No PII leaves the private dir**: name calls, never callers; show documents by shape; no
   transcripts in Jira or Slack.
5. **Logs first, and fast.** Argo loses a pod's log when it rotates, so download before
   querying calls, and pull a call's logs as soon as it matters.
6. **Every case ends in Jira or in the KEDB**, never in "noted".
7. **A session never assigns an error code.** It runs `monitor_store.py similar`, then either
   adds its call to an existing code or proposal (`add-example`) or `propose`s a new one.
   Approval, which allocates the id, is the user's: `approve … --by <name>`. This is what
   keeps two people triaging at the same time from coining two codes for one failure, or
   one id for two.

## Procedure

### A · A monitoring pass (triage)

1. **Context**:
   - the environment and window (default: the last 24 h up to now, UTC with Z);
   - the deployed shas (`deployed.py`);
   - the active flow and version per customer. A new sha or flow version since the last
     pass means **re-baseline** (`method.md` §2): diff, then propose code additions and
     deprecations.
2. **Run** `bash scripts/monitor.sh <env> <until-Z> <hours> <out>`, with `<out>` in the
   session scratchpad. It prints the per-layer funnel, the judge queue, the residue and the
   "not evaluated" list. Say explicitly what could not be evaluated and why: no voice logs for
   the window, no node path in prod, and so on.
3. **Judge L3**: work through `<out>/l3/`, one verdict JSON per call following `RUBRICA.md`.
   Use subagents for a big queue. Then re-run `monitor.sh`, or its step 5 onwards, so the
   verdicts enter the findings and the report.
4. **Triage with the user, case by case**, from `parte.md`. For each finding:
   - read the bundle and the logs;
   - classify it with `method.md` §1, and for flow issues with `maria-workflow-builder`'s
     `defect-catalog.md`;
   - decide **Jira task**: create it with the user's OK, then `task-add` + `task-link` the code,
     scoped by tenant or calls when the task only covers some of them; or
   - decide **KEDB entry** (`kedb-add`: `normal` / `known_bug` / `watch`, with an exact
     matcher, `unless` exceptions and the reason).

   Findings whose cause is the flow design go to `maria-workflow-builder` as fix-mode input.
5. **Residue**: cluster "goal not met, no code". For a cluster of ≥3 in 7 days, check
   `similar` and `proposals` first, then `propose` it with its example calls. The user reviews
   `proposals` and approves, merges or rejects.
6. **Publish** `parte.html` as an artifact for the team, and show the drafted `slack.txt`
   with the link. Post only if the user approves and Slack is connected.

### B · A pilot window

Follow `references/pilot.md`:
- `pilot_watch.py` in the background from the activation instant;
- logs pulled hot, findings written hot;
- afterwards every call labelled, `pilot_report.py`, and the report published;
- each finding turned into a test plus a flow version through `maria-workflow-builder`.

### C · Catalog and KEDB maintenance

Everything goes through `scripts/collectors/monitor_store.py`:
- **First use on a machine**: `init`, then `import-yaml` (seed from maria-voice
  `docs/monitoring/`). It is idempotent.
- **A new code**: `similar` → `propose` → the user runs `approve` (or `merge` / `reject`).
  Approving allocates `L<n>-<DOMAIN>-<NNN>` atomically and bumps the catalog minor version.
- **A change of meaning**: never edit in place. Propose the new code, approve it, then
  `deprecate <old> --replaced-by <new>`.
- **A code a deterministic detector will emit**: approve it in the store in the same change as
  the detector (the detector's code is in git).
- **Tasks**: `task-add`, `task-link`, `task-status`, and `tasks --code …` to see who owns what.
- **History**: every `monitor.sh` pass is recorded (`record-run`); `trend --code …` gives the
  code's count per pass, which is what regressions per version are measured on.
- **Snapshot for review**: `export-yaml --out <dir>`, and diff it against the previous one.

Detectors refuse codes that are not in the catalog. Today the store is one local file; it
is designed to be copied to miniomni or loaded into Postgres unchanged when the monitor
becomes shared.

## Finish

Report:
- the window and environment;
- the versions (shas, flow versions, catalog version);
- the funnel;
- the new codes and the streaks;
- the cases decided (Jira keys, KEDB entries) and the ones still open;
- what could not be evaluated;
- the published report link.

Keep only non-derivable state in memory.
