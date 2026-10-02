# The method: layers, codes, and what a finding is

The design is from MAR-1504, adapted from a hierarchical failure taxonomy for voice agents. The
original design and worklog are in maria-voice `docs/wip/hierarchical_taxonomy/`
(`hierarchical_taxonomy.md`, `workplan.md`), git-excluded.

## Contents

1. [Three layers, and the gate between them](#1-three-layers-and-the-gate-between-them)
2. [The error-code catalog](#2-the-error-code-catalog)
3. [The known-error database and the task map](#3-the-known-error-database-and-the-task-map)
4. [One pass, step by step](#4-one-pass-step-by-step)
5. [The semantic judge (L3)](#5-the-semantic-judge-l3)
6. [Regressions: comparing versions and windows](#6-regressions-comparing-versions-and-windows)

---

## 1. Three layers, and the gate between them

| layer | detector | source | examples |
|---|---|---|---|
| **L1** transport / voice / platform | deterministic | logs + `calls` + `call_timeline` | disconnect reasons, a deploy cutting a live call, `E2E time` over threshold, a call with no caller utterance, `session_error` |
| **L2** state / execution | deterministic | DB + core/voice logs | a tool error in the timeline, a webhook timeout, provider codes (`EXT-PAP-*`), a transfer failure reason, a transition outside the flow's edges, a node loop |
| **L3** semantics | LLM judge, only when L1 and L2 pass | transcript + node path + catalog | wrong branch with the right data, a question already answered, a hallucination against the KB, a premature goodbye, the flow design not covering the case |
| outcome (cross-cutting) | deterministic + judge | `call_result` + judge | "goal not met and no code fired" is the **residue** to cluster |

- **Rule of attribution: code the FIRST failure in the trace.** An upstream failure
  invalidates evaluating the layers above it. A call that failed in L1 is
  invalid-for-semantics, not an agent failure.
- **Rule of layer: if a telemetry assertion can detect it, it is not L3.**
- **Severity is orthogonal to layer** (impact × recoverability). A one-off L1 is grave per call
  but alarms by RATE; an L2 identity failure in a streak alarms even if each one was
  "recovered" by a transfer.

## 2. The error-code catalog

The `codes` table of `monitor.sqlite` (seeded from maria-voice `docs/monitoring/catalog.yaml`).
Codes are append-only. **A session proposes and a human approves**: the id is allocated
atomically on approval, so concurrent triage cannot collide (`monitor_store.py`).

- **Code format:** `L<layer>-<DOMAIN>-<NNN>` (`L2-AUTH-003`, `L1-DEPLOY-001`,
  `L3-FLOW-004`). Layer and domain are the small stable part; consumers that do not know a
  code degrade to its family.
- **Each entry has:**
  - `id`, `title`, `layer`, `domain`, `definition`;
  - `detection` (`kind: deterministic|judge` + where);
  - `scope` (`global | client:<slug> | flow:<id>`) and `anchors` (node ids);
  - `severity_default`, `owner` (`backend|flow|prompt|infra|provider`);
  - `status`, `replaced_by`, `since_catalog`, `examples` (call ids).
- **Never redefine a code in place, and never recycle an id.** A change of meaning means a NEW
  code plus deprecating the old one with `replaced_by`. Clarifying wording without changing
  meaning is allowed.
- **Version the catalog (semver), not the codes.** Every analysed call is stamped with
  `(catalog_version, deployed sha, flow_id, flow_version)`.
- **Re-baseline triggers:** a new deployed sha, OR a new version of an active flow. The flow
  and the code are independent axes: a customer-requested flow change triggers a re-baseline
  without any code change. On a trigger:
  1. diff the flow (nodes/edges/tools added/removed/changed) and the code between shas;
  2. propose code additions and deprecations;
  3. a human approves them (`monitor_store.py approve` / `deprecate`).
- **Catalog behind flow:** the pass does not stop. It runs degraded, stamps
  `catalog_behind_flow=true`, and codes anchored to surviving nodes keep applying.
- Detectors only emit codes that exist in the catalog: `_findings.py` validates.

## 3. The known-error database and the task map

The report is a **triage sheet**, not a summary. Every finding is one line, grouped by
customer, with a link to the call in the console
(`https://onestopshop<env>.api.omniloy.com/apps/maria/calls/<call_id>`, `<env>` = `""` /
`-stg` / `-dev`). Each case ends in exactly one of two places:

1. **a Jira task.** There is something to fix. Link it in the store (`tasks` + `task_codes`):
   code → task, optionally scoped by `tenant` or `calls`, so the report stops listing it as
   unowned.
2. **a KEDB entry** (`kedb` table). There is nothing to fix, and the entry writes down why, so
   the case is not listed again.
   - `decision: normal`: counting it was noise.
   - `known_bug`: it is real and already in Jira.
   - `watch`: marks without hiding.

   Matchers are exact equalities on the finding's evidence: a matcher that needs
   interpreting is a matcher nobody audits. Hidden cases are counted (`--show-hidden` lists
   them): nothing disappears silently.

That knowledge lives in the shared store, not in a session's memory. It changes what the
monitor hides, so every row records who decided and when. The store has to travel to
miniomni (the final home of this monitor), which is why it is a single portable file.

## 4. One pass, step by step

`scripts/monitor.sh <env> <until-ISO-Z> <hours> [<out>]`:

1. **Logs first.** Argo serves only LIVE pods, and maria-voice autoscales, so every minute of
   waiting loses coverage. The window is aligned to what the logs cover.
2. **Calls of the window** → one bundle per call (`fetch_bundles.py`).
3. **Detectors L1/L2** + window + latency from the log (`run_detectors.py`):
   - the gate;
   - the per-layer funnel;
   - the judge queue: every flagged call, plus a 10–20 % sample of the passes, with a
     budget;
   - the residue.
4. **Identification**: what SINA actually answered, from the core log (`auth_cases.py`,
   `sina/reclassify_auth.py`). It replaces inference where the log reaches; elsewhere the
   inference stays, marked as such.
5. **L3 judge queue** (`judge_l3.py pack`): one file per call plus `RUBRICA.md`. The judging is
   done by the session (§5). `judge_l3.py collect` gathers the verdicts; then a second
   detector pass puts L3 findings into `findings.json`.
6. **The report**: `report.py` (Markdown) and `artifact.py` (HTML to publish).
7. **The alert**: `notify.py` writes `slack.txt`, the message that WOULD be sent. It is not
   sent: posting to Slack needs explicit approval and a connected Slack.

## 5. The semantic judge (L3)

- One pass per call, following `RUBRICA.md`. The answer is a JSON with:
  - `resolvio`: sí / no / parcial / no aplica;
  - `de_quien`: agente / paciente / dependencia / diseño del flujo / ninguno;
  - `que_paso`, `senales` (a CLOSED list), a literal `cita`, `confianza`.
- The rubric's rules exist because judges fail the same way:
  - do not reward politeness;
  - do not blame the agent for what the caller or a dependency did;
  - tell "could not" from "did not know";
  - transferring is not failing when transferring IS the service.
- **`diseño_del_flujo` is the most valuable verdict.** The agent did exactly what it was told
  and the result was still bad. Two independent windows found it ~8× more often than
  `agente`. Those findings go to `maria-workflow-builder`.
- The signal vocabulary is closed. An `otro:` label that recurs is a label asking to exist:
  add it.
- **Residue → codes.** "Goal not met and no code fired" is summarised in one line per call and
  clustered in the session. A cluster of ≥3 in 7 days is a candidate code, approved by a
  human. The residue shrinking is the measure that the catalog is learning (21 → 5 in one
  iteration).
- Scale: judge in subagents or in a loop over the queue files. Write each verdict as
  `<call_id>.verdict.json` next to its case file.

## 6. Regressions: comparing versions and windows

- **Compare like with like**: same customer, same hour band, stamped with (sha, flow version,
  catalog version).
- **Per-version accumulation, not per-window.** Detecting a 5-point change in a rate needs
  ~1,000 calls per arm. A daily window says "something happened" (a new code, a streak, a
  spike in L1); only accumulation says "this version is worse".
- **A new flow version is a re-baseline trigger** (§2). Before comparing, check which codes
  are anchored to nodes that changed.
- **Latency**: report percentiles only (p50/p90/p95/p99 per customer and turn type), with our
  own baseline (answering turns p50 1.8 s / p95 4.3 s). Fix an SLA after two weeks of data.
