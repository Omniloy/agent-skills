# Versioning, clones, promotion and activation

The rules that keep a flow change auditable and keep running workers honest, and the path a
change takes from a sandbox to a customer's production line. Scripts: `../scripts/`.

## Contents

1. [First: find out which flow is actually active](#1-which-flow-is-active)
2. [How versioning works, and why it matters beyond audit](#2-how-versioning-works)
3. [Never edit the customer's flow in place: clone](#3-clone)
4. [Editing: one script per version](#4-editing)
5. [Environments](#5-environments)
6. [Promotion to another environment](#6-promotion)
7. [Activation and the kill switch](#7-activation-and-the-kill-switch)
8. [Repairing a flow written out of band](#8-repairing-a-flow-written-out-of-band)

---

## 1. Which flow is active

```sql
select voice_mode, default_conversation_flow_id from api_keys where id = '<api_key>';
```

**Do not trust the id quoted in a task.**
- On 2026-08-20 a whole test suite was written against `2da05455` while the customer had been
  served `cad74840` since the day before.
- Names lie too: two flows can differ only in their prompts («San Roque Citación» vs «… v2 —
  concisa»).
- `voice_mode='standard'` with a NULL flow id means the customer is on the LEGACY agent: the
  flow exists but nobody hears it.

`python3 scripts/flow_lint.py --env <env> --flow <id>` prints how many api_keys serve the
flow (finding D03).

## 2. How versioning works

Three things move together on every save:

| where | what |
|---|---|
| `conversation_flows_versions` (**flows**, plural) | one row per save: full `flow_document`, `version_number`, `change_type`, `change_description`, `created_by_user_*` |
| `conversation_flows.version` | the new `version_number` |
| `conversation_flows.active_version_id` | FK to the row just inserted |

`PUT /api/v1/conversation-flows/:id` (core-service, `conversationFlowRepository.update`)
does all three atomically. The body is merged with the row, so even
`{"changeDescription": "…"}` is a valid save. `scripts/flowdb.put_flow` sends
`X-User-Id: CLAUDE`, which is what lands in `created_by_user_id`.

**Why it matters beyond audit:** `conversation_flows.version` is the freshness stamp of the
worker's flow cache. The worker revalidates against `GET …/:id/version` and re-fetches only
when the integer changed. A document written directly (PostgREST, SQL, the Supabase UI) is
**invisible to every running worker**: the document changes, the stamp does not, and the
cache serves the old flow until the process restarts. No error anywhere.

`change_type` is CHECK-constrained to `create | edit | promote_from_sandbox | activate |
backfill` (maria_db migration `20260512120000`). Anything else, `update` for instance, is a
400. Use `create` for a fresh clone, `edit` for a change, `promote_from_sandbox` for a
promotion.

**Health query**, run after every write:

```sql
select f.version, f.active_version_id, count(v.id) as version_rows, max(v.version_number)
from conversation_flows f left join conversation_flows_versions v on v.conversation_flow_id = f.id
where f.id = '<flow_id>' group by 1, 2;
```

A healthy flow has `active_version_id` set and `version` equal to the highest
`version_number`. `flowdb.DB.health()` runs this check.

**The change description is the worklog.** Write what changed, concretely, why, and the
evidence (call id, test, measurement):
- «Solo texto: ni nodos, ni aristas, ni tools» when that is the case.
- Never paste PII: name the call (`d7085960`), not the caller.

On San Roque the version descriptions were enough to reconstruct three weeks of work.

## 3. Clone

Never edit a flow an api_key serves. Clone it, fix the clone, validate, then promote.

```bash
python3 scripts/clone_flow.py --source-env prod --source-flow <uuid> --target-env stg \
  --name "<Customer> Citación — sandbox <JIRA>" \
  --rewrite-host <prod-host>=<test-host> --set-header X-API-Key=env:<TEST_KEY_VAR> \
  --set-var <transfer_number_var>=<a TEST number> --allowed-host <test-host> --jira <JIRA>
# dry-run first; add --apply
```

What the script guarantees:
- **all three version fields** written together;
- **the KB collections are copied** (`conversation_flow_collections`); without them every
  factual answer fails;
- **environment values are rewritten**, `tools[]` and every `prefetch_tools`, and a
  credential header left from the source is warned about;
- the clone is referenced by no api_key. Pin it per call through room metadata
  (`conversation_flow_id`), which is what the test harness does.

**A test clone must never carry production's transfer number or write endpoints.** A suite
that books against production books real appointments; a transfer to the real desk calls a
real person.

## 4. Editing

One small script per version, written in the frame of `scripts/flow_edit.py`:

- **dry-run by default**; `--apply` saves through core-service;
- **one `assert` per change against the text it expects to find**, so a prompt that moved
  aborts the edit before anything is written;
- `--expect-version N` catches a concurrent save;
- it refuses a flow that an api_key serves (`--live-flow-ok` is for a hotfix the user
  approved);
- a host guard runs before and after, and the lint runs on the result: new ERRORS abort;
- a readable diff, then a read-back after the save to check the stored document is
  byte-identical, then the health query.

Keep the scripts next to the workplan (`docs/wip/<customer>_<topic>/edit_vN.py`, git-excluded)
so every version can be traced to code. Name each change like the version description:
`"M1 · n_citas: an empty list is not a negative"`.

## 5. Environments

| env | Supabase ref | typical use |
|---|---|---|
| dev | `dfllbgtwfbfyiiayioeu` | shaping a flow, tests against a test backend |
| stg | `kvjdhbpkucvmnoczuoyy` | sandbox clones, release validation, customer QA |
| prod (main) | `yavtkqacpiwaahzrejhk` | the customer. **Read-only without an explicit OK** |

- dev and stg are BRANCHES of maria-db, so they do not appear in `supabase projects list`.
- Keys are read from maria-core-service's `.env` (prod's block is commented out).
- Writes through the API need a core-service pointed at the right database: set
  `MARIA_CORE_URL_<ENV>`. There is no default on purpose.
- The same api_key id can exist in several environments; San Roque's is identical in stg and
  main.

Values that differ per environment and must be enumerated for every customer:
- backend host(s);
- credentials;
- transfer numbers and queue extensions;
- sender/caller ids;
- any customer-specific ids that differ between the customer's test and production systems
  (agreements, centres).

List them in the workplan before the first promotion.

## 6. Promotion

Production usually has no core-service you can drive from here, so a promotion is a SQL file
that moves the three version fields in one transaction. It is generated, rehearsed, reviewed
and only then applied, with the user's OK.

```bash
# 1. generate (reads both environments, writes files only)
python3 scripts/gen_promotion_sql.py \
  --source-env stg --source-flow <clone> --min-source-version <V> \
  --target-env prod --target-flow <flow> --expect-target-version <P> \
  --rewrite-host <test-host>=<prod-host> --keep-header X-API-Key \
  --keep-var <transfer var> [--keep-var …] --forbid <test-host> --forbid <test phone> \
  --jira <JIRA> --description "<what changed and why>" --out-dir docs/wip/<customer>/promotion
# 2. rehearse in a throwaway Postgres seeded with the target's REAL rows
python3 scripts/validate_promotion.py --target-env prod --target-flow <flow> --dir docs/wip/<customer>/promotion
# 3. review DIFF_v<P>_to_v<P+1>.txt with the user; 4. the user (or you, with their OK) applies 01_…sql
# 5. health query on prod; keep 99_rollback_…sql at hand
```

- The generator was validated by reproducing San Roque's hand-made v44 byte for byte.
- **Regenerate, never edit, a promotion file.** The guard refuses to apply it if the target
  moved.
- **A promotion can be a runtime dependency.** If a flow relies on code, note the required
  worker image in the SQL header and check prod runs it. San Roque's global `n_otro_motivo`
  needs MAR-1563; `prefetch_tools` needs the worker that runs them.
- One promotion can carry many clone versions. The description lists them (San Roque v44
  carried clone v39–v51).
- **Hotfix path.** Same generator, from a clone version that contains only the fix. Promote
  it on top of the current prod version, then rebase the rest of the clone work onto it
  (San Roque v43).

## 7. Activation and the kill switch

Activation is a SEPARATE step from promotion, on purpose. A promotion changes nothing callers
hear while no api_key points at the flow; activation is the switch. Apply it at the start of
a watched window (a pilot hour), not before.

```sql
-- 02_activate.sql
BEGIN;
DO $$ DECLARE v int; a uuid; BEGIN
  SELECT version, active_version_id INTO v, a FROM conversation_flows WHERE id = '<flow>';
  IF v <> <N> OR a IS NULL THEN RAISE EXCEPTION 'flow not at v<N> with an active version'; END IF;
  IF NOT EXISTS (SELECT 1 FROM conversation_flow_collections WHERE conversation_flow_id = '<flow>')
  THEN RAISE EXCEPTION 'no FAQ collection linked'; END IF;
END $$;
UPDATE api_keys SET voice_mode = 'conversation_flow', default_conversation_flow_id = '<flow>',
       updated_at = now() WHERE id = '<api_key>';
COMMIT;

-- 98_deactivate.sql — the kill switch: back to legacy, the flow untouched
BEGIN;
UPDATE api_keys SET voice_mode = 'standard', default_conversation_flow_id = NULL, updated_at = now()
 WHERE id = '<api_key>';
COMMIT;
```

Have `98_deactivate.sql` ready BEFORE activating, and know who can apply it. On San Roque it
was used twice: at the end of pilot 1, and on 25/09 11:36Z when pilot 5 showed backend
defects the flow could not absorb.

## 8. Repairing a flow written out of band

A flow inserted directly has **no** version rows. Its first save computes
`version_number = 1`, which `conversation_flows.version` may already be. The stamp does not
change and caches still do not invalidate. A second save moves it. Check with the health
query before assuming it is fixed.
