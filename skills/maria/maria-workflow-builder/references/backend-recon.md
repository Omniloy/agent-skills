# Reconnaissance of the customer's backend

Most San Roque production defects were facts about the backend that nobody had written down:
- a filter that hides live appointments;
- an empty list with three meanings;
- a sync lag of more than 95 s;
- a boolean the backend refuses as a string;
- duplicate patient records;
- states swapped after a reschedule.

Probe the API BEFORE designing the nodes that read it, and write the answers into
`BACKEND_CONTRACT.md` in the workplan folder. The flow's prompts and the suite's assertions
cite it.

**Rules of engagement:**
- read-only against production, always, and only with the user's OK;
- writes only against the customer's TEST environment, and every write you make is cleaned
  up by id;
- never paste real identifiers into the contract: name the call or the endpoint instead.

## Questions to answer, per endpoint the flow will use

1. **Environments.** The host of the test and the production system, and the port. San Roque:
   `:8443`; the `:443` of the same host served an unrelated app that returned HTML to
   everything. Are the credentials per environment? Do customer ids (insurers, agreements,
   centres) match across environments?
2. **What question does it actually answer?** Look for a field that cannot answer yours.
   - San Roque `health-centers?specialty_id=` returns where a specialty is ATTENDED, not
     where there is availability.
   - `doctors.health_center_id` is single-valued while agendas hang off other centres.
   - `/specialties` returned 173 entries, among them stores, committees and tests; only 32
     had an agenda.
3. **What does EMPTY mean?** List every reason the answer can be empty, and whether the
   response distinguishes them.
   - San Roque free-spaces: not covered, no slots now, and not bookable by phone all looked
     identical.
   - `availability` distinguishes them through `coverage.main.is_valid` and `slots`.
   - Map each reason to a different caller-facing sentence and exit.
4. **Filters.**
   - Are they exact, OR-by-word, case-sensitive? San Roque `?name=` is OR by word: «Mutua
     Recoletas del Norte» matched 20 records.
   - Does a status filter hide live data? `status_id=scheduled` returned 0 for patients with
     scheduled appointments.
   - Is only one value accepted per parameter?
   - Is a parameter silently ignored in one environment? `include_other_agreements` worked in
     PRE and was ignored in PRO.
5. **Size.** Bytes for a typical and a worst case, against the 8000-char cap. Find the
   size/page parameter.
6. **Latency.** p50/p95 for reads, and COLD writes: San Roque took 18–20 s for a first
   registration. Does the gateway give up at some limit? That limit and your `timeout_s` must
   be consistent, and both ≤ 60 s.
7. **Errors.**
   - What does a business error look like (status + body)? Many are returned as 500.
   - Which errors name a field the caller dictated, so the flow can re-ask that field?
   - Which mean broken backend state rather than a bad request?
   - Collect the exact strings: the suite's error classifier needs them.
8. **Types.** Types of every body field (`System.Nullable<bool>` rejects `"true"`), date
   formats, time zone of returned datetimes (San Roque: Canary time).
9. **Display fields.**
   - Accents, capitalisation, gender of professionals, "surname, name" order.
   - Does the backend USE the display fields you send, or ignore them? If it ignores them, the
     model can make them presentable.
10. **Data quality.**
    - Several records per identity document, and how to tell them apart (name only; birth
      dates are unreliable in duplicates).
    - Records with no billing agreement.
    - Orphan insurer entries with zero policies (San Roque's `MAPFRE`).
11. **Consistency after writes.**
    - How long until a created item is listed? San Roque: more than 95 s.
    - Does a reschedule or cancel leave ghosts or swapped states?
    - Is a second write on the same item safe?
12. **What can be booked at all.** Scan bookability once: per specialty × insurer, read-only.
    San Roque: 11 specialties with an agenda and no valid agreement, so none could be booked
    by phone. Ask the customer to confirm the list before promising anything, and get a
    `schedulable` filter if they can add one.
13. **Test data.** Test patients that exist in the test environment, with and without
    appointments, insured and private, one with two records; a range of documents that do not
    exist, for registration tests.

## Output: BACKEND_CONTRACT.md

```markdown
# <Customer> backend contract (probed <date>, test env <host>, prod <host>)
| endpoint | answers | empty means | size (typ/max) | p50/p95 | errors seen | notes |
|---|---|---|---|---|---|---|
| GET /patients?national_id= | who holds this document | not registered OR mis-dictated document | 1 KB | 0.4/1.2 s | 400 malformed doc | several records per document are normal |
…
## Environment values
host test/prod · credential header name · transfer numbers per queue · ids that differ
## Known backend defects (report to the integrator; the flow must absorb them)
- <defect> — repro <how>, observed <call id / date>, flow mitigation <node/rule>
```

Keep a message to the integrator next to it. San Roque's `greencube_repro/MENSAJE_GREENCUBE.md`
listed each defect with a curl reproduction. Most backend defects are not yours to fix, but
the flow has to say something true while they exist.
