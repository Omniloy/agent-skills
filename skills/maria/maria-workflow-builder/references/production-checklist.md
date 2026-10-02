# Production-readiness checklist

The gate before a flow is promoted and activated for a customer. Copy it into the workplan
(`docs/wip/<customer>_<topic>/READINESS.md`) and tick each item with its evidence: a lint run,
a test name, a query output, a call id. An unticked item is either fixed or explicitly
accepted by the user, with the reason written next to it.

## A. Identity of what ships

- [ ] The api_key's CURRENT default flow was queried (not taken from the task), and the
      target flow for promotion is named with its current version.
- [ ] The sandbox clone id and version being promoted are recorded; the clone was never
      referenced by an api_key.
- [ ] Every version has a concrete `change_description` with its evidence, and
      `created_by_user_id='CLAUDE'` for versions written by you.
- [ ] Health query green on the clone (`version = max(version_number)`, active version set).

## B. Static review

- [ ] `flow_lint.py --env <env> --flow <clone>`: **0 errors**. Every `warn` is fixed or
      justified in READINESS.md.
- [ ] Outcome matrix filled for every conversation node (`authoring.md` §2); no empty cell.
- [ ] Each node's edges were crossed against what its prompt offers: no dead edge, no
      offered option without an edge.
- [ ] Every `global_condition` was read against every node's edges: no overlap, each ending
      with the last-resort clause.
- [ ] A common-rules block is in every `prompt` node (`global_prompt` reaches only the start
      node).
- [ ] Every text READ aloud (`static_text`, `pre_message`, `goodbye_message`) exists in every
      language the customer serves; the translations were checked placeholder by placeholder.
- [ ] Every `pre_message` is true from every predecessor.
- [ ] No orphan tools, no variables nobody writes.
- [ ] `faq_prompt` has the answering rules (no improvisation, search before asking, offer a
      person when it is not documented).

## C. Backend

- [ ] `BACKEND_CONTRACT.md` is written (`backend-recon.md`): empty-result meanings, filters,
      sizes, latencies, errors, types, data quality, consistency lag.
- [ ] Each webhook's `timeout_s` is above the measured cold latency and ≤ 60.
- [ ] Each typical response is below 8000 chars.
- [ ] The `body_params` types match the contract (verified after the last UI save, if any).
- [ ] What is bookable by phone has been confirmed by the customer; the flow does not offer
      what cannot be booked.
- [ ] The known backend defects are listed, each with the flow's mitigation, and reported to
      the integrator.

## D. Tests

- [ ] The edge-coverage test is green: every edge is named by a test or explicitly `None` with
      a reason.
- [ ] Happy paths end to end are verified AGAINST THE BACKEND (snapshot diff) and cleaned up.
- [ ] Every decline has a test showing the irreversible tool is not called.
- [ ] Every write tool has failure tests (timeout / 500 / unreachable) via fault injection.
- [ ] Identity tests: dictation shapes, calling for someone else, corrections, several records.
- [ ] Globals from every node; FAQ answered from the documentation; one call per language.
- [ ] Each production incident so far is replayed as a test.
- [ ] The fixes' own tests pass 3/3 (`repeat_tests.sh`); the full suite passes once on the
      final clone version; known defects are non-strict xfails with evidence.
- [ ] The skip count was read: nothing is silently skipped.

## E. Latency

- [ ] Median transition gaps measured (`citacion_e2e`, DEV transcripts); no node opens with a
      generated sentence that could be a `pre_message`/`static_text`.
- [ ] No announce-and-wait left: every "I'll do X" is followed by X in the same turn.
- [ ] Slow tools either have filler, or the silence was accepted by the user.

## F. Promotion

- [ ] Environment values are enumerated (hosts, credentials, transfer numbers/queues, ids
      that differ); `--forbid` covers the test host and the test phone numbers.
- [ ] `gen_promotion_sql.py` has been run; DIFF reviewed with the user.
- [ ] `validate_promotion.py` is green: it applies, is healthy, the guard refuses a re-apply,
      and the rollback restores.
- [ ] The runtime dependencies of the flow (features it relies on) are present in the
      production worker image.
- [ ] The KB collection is linked on the target flow.
- [ ] The rollback file and the kill switch (`98_deactivate.sql`) are ready, and who applies
      them is known.

## G. Activation and pilot

- [ ] Activation SQL is separate, guarded on version + collection, and applied at the START of
      a watched window.
- [ ] The pilot plan is written:
  - duration;
  - who watches;
  - how calls are pulled (`calls` read-only);
  - logs downloaded quickly, since only the live pod's logs are served;
  - the criteria to deactivate.
- [ ] After the window:
  - every call is labelled;
  - findings are numbered (P<n>-<k>) with call ids;
  - each finding has a candidate fix and a test;
  - the report is published.

  This is the hand-off point to the monitoring skill.
