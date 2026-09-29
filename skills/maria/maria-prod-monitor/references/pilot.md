# Watching a production pilot

How the five San Roque production pilots (21/09–25/09/2026) were run. A pilot is the first
hours a customer's callers hear a new workflow, or a new version of one. It is the step
`maria-workflow-builder` hands over to after activation.

## Before the window

- **Promotion applied and healthy, activation ready, kill switch ready** (`98_deactivate.sql`
  in the promotion folder). Know who can apply it, and agree the criteria to pull it:
  - a defect that sends callers away with nothing, in a streak;
  - a backend defect the flow cannot absorb.

  San Roque pulled pilot 5 at 11:36Z for the second reason.
- **Record the activation instant** (UTC with Z). It is `--since` for the watcher.
- **Know the team's test phones by their last digits.** Their calls are counted apart. Never
  write full numbers anywhere.

## During the window

```bash
python3 scripts/pilot_watch.py --api-key-id <api_key> --since <activation Z> \
  --out <workplan>/pilotN --every 180 --minutes 480 \
  --exclude-phone-suffix <last digits> [--flag NAME=REGEX for this version's changes]
```

- Run it in the background. Each pass saves a full copy of every new or changed call
  (call row + node transitions) and prints one line per call:
  - time, id, status, result, turns;
  - the node path;
  - flags.

  Flags triage, they do not classify. The ones that were signal across pilots:
  - `FIN-EN-ERROR`, `BUCLE-NODO`, `FALLO→PERSONA`;
  - `ID-EN-VOZ-ALTA`, `LEE-UN-ERROR`;
  - `PIDE-DOCUMENTO-2-VECES`;
  - `¿HOLA?` (the caller probing a silence);
  - `NADIE-HABLO`.
- **Pull the worker logs of any call that matters right away** (`argo_logs.py --call <id>`),
  before its pod rotates. The node path is in the DB; the WHY (latency, tool durations, the
  exact tool output) is only in the logs.
- **Write findings hot**, one per case, numbered `P<pilot>-<n>`: call id, what was heard, the
  cause if known, and a candidate fix. Put them in `<workplan>/pilotN/findings.md`. Verify
  backend claims read-only before writing them. Several "the flow is wrong" suspicions were
  the backend:
  - an appointment filter hiding live appointments;
  - more than 95 s to list a new appointment;
  - duplicate records.
- **A hotfix during a pilot** follows the same path as any change: clone version, test,
  promotion SQL, apply with OK. Keep it minimal (San Roque v43 changed one filter and the
  nodes that read it).

## After the window

1. **Label every call** in `labels.json`:
   - `correcto` (with `offered_person` when the agent handed over);
   - `parcial`;
   - `sin_nada`;
   - `directa` (asked for a person or service straight away);
   - `muda`;
   - `prueba`.

   Read the transcript for each; flags are not labels. Keep the same yardstick across pilots,
   so windows compare.
2. **Report**: `python3 scripts/pilot_report.py <dir> --title "Pilot N · flow vX"`. It gives:
   - the denominator (without `directa`/`muda`);
   - % correct / partial / nothing;
   - how many correct calls ended with the agent handing over;
   - the calls that did not end well, with notes.

   Add the finding list and the backend defects. Publish it for the team (an artifact), and
   keep the generator with the data so the report can be regenerated when more calls are
   labelled.
3. **Each finding becomes a test and a flow version**, via `maria-workflow-builder`'s fix
   mode. Look for the same shape elsewhere in the graph. Findings that are the backend's go
   to the integrator, with a curl reproduction.
4. **What recurs across pilots becomes a catalog code** (`method.md` §2), so the regular
   monitor sees it after the pilot phase ends.

## What the San Roque pilots measured

For calibration, not as targets:
- pilot 1: 32 callers, 17 left with nothing, 15 of them before the booking part;
- pilot 3: 96 callers, 76 % correct, 13 % nothing (denominator 67), 0 of 10 new bookings,
  mostly for backend/coverage reasons.

The recurring causes were missing exits, announce-and-wait silences, voice-captured
documents, and backend data the flow trusted too much.
