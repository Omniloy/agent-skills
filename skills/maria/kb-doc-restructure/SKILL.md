---
name: kb-doc-restructure
description: >-
  Turn a raw client FAQ/knowledge document (docx, pdf, txt) into a properly
  structured Markdown file for the PageIndex knowledge base, so the active-flow
  FAQ block stays small while answers stay correct. Headers (#/##/###) map to
  PageIndex node depth. Use when a client gives a new/badly-structured FAQ and
  you want to shrink the per-node FAQ block (B1 / MAR-1009) or just re-ingest a
  doc cleanly. The skill ENFORCES a two-layer faithfulness audit (nothing lost,
  nothing invented) and a grounded eval against the LIVE agent (questions drawn
  from the ORIGINAL doc) before the .md is accepted — because restructuring can
  preserve every fact yet still break retrieval.
---

# Knowledge-base document restructuring (PageIndex)

Convert a messy source document into a structured `.md` that PageIndex ingests
deterministically, **shrinking the active-flow FAQ block without losing answer
quality**.

> `<skill>` below is this skill's directory (installed by agent-skills' `install.sh`, e.g.
> `~/.claude/skills/kb-doc-restructure`); its scripts are standard-library only. The Gate B
> evaluation harnesses import maria-voice and read its `.env`, so they live in that repo,
> `tests/integration/customers/kb/`: run them with `poetry run` from the maria-voice checkout.

## Why structure matters

PageIndex parses Markdown headers into a node tree (`pageindexomniloy
/app/indexing/md_pipeline.py`: `_parse_header_blocks` + `_blocks_to_tree`, **only
levels 1–3**). maria-voice renders the FAQ block from **top-level nodes only**
(`_render_active_flow_kb`, `conversation_node_agent.py`), so fewer/larger `#`
sections → smaller prompt block. Always upload as **`.md`** so this pipeline runs.

**The retrieval trap (read this):** moving content under deeper headers makes the
FAQ block smaller, but a `#` node that is *only* a section title has **empty
content**. `get_node_content` returns a node's own content; if the agent picks an
empty header node it gets nothing and **confabulates**. This is real (San Roque,
MAR-1009 — the MCP fix `StructuredDocument.expand_with_descendants` now expands a
requested node to its subtree). The grounded eval below is what catches this
class of bug; the document-vs-document audit alone never will.

## Hard rules

- **The ORIGINAL document is the only source of truth.** Audit questions and the
  grounded eval are generated FROM the original, never from your restructured
  output (that's how you catch facts the restructuring dropped or distorted).
- **Do not accept the `.md` until both gates pass:** (1) the two-layer
  faithfulness audit, and (2) the live grounded eval with **0 CONTRADICTS**.
- Only header levels `#`, `##`, `###` (depth 1–3). Put real text under the
  deepest relevant header. Group by topic; keep one fact in one place.
- Never invent, infer, or "tidy up" facts (no adding a city, rounding a time,
  completing a list). If the original is ambiguous, keep its wording.

## Procedure

### 1. Extract + restructure
1. Read the source. For docx/pdf, extract text first (e.g. `pandoc in.docx -t
   markdown`, `python-docx`, or `pdftotext`); for txt just read it. Keep a clean
   plain-text copy of the **original** at a known path — it's the ground truth
   for both gates.

**Short-document shortcut — the SINGLE-NODE (flat) structure (RECOMMENDED when
the whole doc fits in one answer context, ≲ ~8k tokens).** Instead of many `#`
topics, use exactly ONE top-level node that is a tiny pointer, with the ENTIRE
document as its content underneath:
```markdown
# Preguntas frecuentes de <cliente>

<one short paragraph that ENUMERATES the topics covered, so the agent knows this
node answers them and its summary stays small>

## Información completa

<the whole document — former section titles demoted to **bold** lines, NOT
headers, so it stays one content blob>
```
Why it works: the FAQ block lists only top-level nodes, so context stays tiny
(one node, ~250-char summary); but with a single node the agent CANNOT mis-route
— it always selects node 1 and `expand_with_descendants` pulls the whole doc.
Measured (San Roque, 284-q golden): flat = 96.1% satisfactory with **0 retrieval
misses, 0 NO_INFO, 0 CONTRADICTS, context sufficient on 100%** — statistically
tied with the best multi-node doc (96.5%) but with a strictly healthier failure
profile (only completeness PARTIALs remain, which the FAQ prompt handles). It
trades ~6-7k tokens into the *answer* turn (only when the user asks) for perfect
retrieval. Use it for short docs; for large docs (won't fit one context) keep the
multi-topic structure below. Note: don't over-tune the completeness prompt on top
of flat — pushing "enumerate everything" harder regressed results (94%).

For the multi-topic structure (large docs):
2. Rewrite into structured Markdown:
   - `#` = top-level topic (these are what the agent sees in the FAQ block — give
     each a clear, self-explaining title).
   - `##` / `###` = sub-topics; the answer text lives here.
   - Reorganize for retrieval: group everything about one topic (and its
     per-center variants) under one `#`. Don't scatter a topic across sections.
   - Copy facts verbatim. Reword only headings/connectors, never data.

### 1b. Layout of repetitive info — PREFER PROSE/LISTS over tables (ASK FIRST)
**Empirical warning (San Roque A/B, 2026-06-19, MAR-1070):** turning repetitive
data into multi-dimensional Markdown **tables HURT answer quality** — the answer
LLM (gpt-4.1) misreads a grid (e.g. picks the wrong row/center, or a `Planta`
time for a `UCI` question, or can't map a `Sí` in a service×center matrix to the
right building). Measured on a fixed 278-question grounded eval: a **prose**
version scored **92%** satisfactory vs **84–88%** for table versions of the SAME
content (−5 to −9 pp; more hallucinations). So the old advice ("a single table is
easier to retrieve") was **wrong for answer quality**. Default to:
- **Schedules/horarios** → one bullet per center: `- Las Palmas: Planta 13–20h;
  UCI 13–14h o 18–19h.` (keep the per-center variants inline, not in columns).
- **Service×center availability** → one bullet per specialty: `- Cardiología:
  Las Palmas, Maspalomas, Lanzarote, Vecindario.`
- **Prices/discounts** → one bullet per insurer: `- Ocaso: 10% (RMN: …).`
Scan for candidates with the advisory tool, but treat its hits as
**prose-list** candidates, not table candidates:
```bash
python3 <skill>/tabulation_scan.py --doc ORIGINAL.txt
```
- **ALWAYS ask the user before changing layout.** If the user still wants tables
  (or for genuinely 2-D data with no better prose form), use **GitHub-flavoured
  pipe tables** (PageIndex ingests them; never HTML/ASCII) — but then you MUST
  A/B prose-vs-table with `faq_retrieval_eval.py` (step 4) and keep whichever
  scores higher. Never assume tables help.
- One topic per node; don't merge unrelated dimensions. Re-layout must not invent
  or drop facts — Gate A still runs over the result.

### 1b-bis. Discoverability — add an "overview line" per top-level node
The active-flow FAQ block shows only **top-level** nodes (title + PageIndex
auto-summary). Any entity that lives only in a sub-node (a sub-site like IKIGAI,
"financiación", "espirometrías", "cristales/biopsias", an email buried in a
schedule) is **invisible to node selection**, so the agent fails to fetch it and
confabulates (San Roque A/B: this was a measurable +4.6 pp gain when fixed). Add
a one-line overview right under each top-level `#` header enumerating the
entities/sub-topics it contains (re-stating existing facts only — no new facts).
PageIndex folds it into the node summary, making those entities findable.

### 1c. TTS rewriting — bake spoken forms into the `.md`
The `.md` is read aloud by a Spanish TTS engine, so raw tokens (`928353535`,
`john.doe@acme.com`, `www.acme.com/mi_pagina`) are read badly. After
restructuring (and after any tabulation), **bake the spoken Spanish form into
the `.md` that goes to PageIndex** using the reusable helper:
```python
from tts_rewrite import rewrite_text, phone_to_words, email_to_words, url_to_words
md = rewrite_text(md)   # replaces every phone / email / URL inline
```
or call the per-type helpers when you need a single value (e.g. inside a table
cell). Examples:
- `928353535` → "nueve dos ocho treinta y cinco treinta y cinco treinta y cinco"
- `+34928353535` → "más treinta y cuatro, nueve dos ocho treinta y cinco treinta y cinco treinta y cinco"
- `john.doe@acme.com` → "john punto doe arroba acme punto com"
- `www.acme.com/mi_pagina` → "doble uve doble uve doble uve punto acme punto com barra mi barra baja pagina"

**Phone grouping scheme (documented choice):** a 9-digit national number is read
as `NNN NN NN NN` — the first 3 digits **digit-by-digit**, each following pair as
a **two-digit cardinal** (matching how Spaniards dictate phones). Odd-length
numbers take a 3-digit head then pairs; even-length numbers are all pairs; a pair
starting with `0` (e.g. `07`) is read "cero siete"; a leading `+` is "más" + the
country code as a cardinal, then a comma; extensions are appended as ", extensión
<digit by digit>". See the module docstring for the full rules.

**Why baked, not at runtime:** PageIndex serves the `.md` text verbatim to the
agent/TTS; there is no TTS-normalization layer in retrieval, so the spoken form
must already be in the document. The **numeric/textual original is preserved
separately** via the knowledge-artifact storage (MAR-1141) and remains the
ground truth for the Gate-A audit and the grounded eval (both run against the
ORIGINAL, never the baked `.md`).

### 2. Gate A — two-layer faithfulness audit (BLOCKING)
1. **Layer 1 (deterministic hard data):**
   ```bash
   python3 <skill>/hard_data_audit.py \
     --original ORIGINAL.txt --restructured NEW.md
   ```
   Exit 0 = clean. Any `LOST` phone/email/URL/%/amount is almost always a real
   omission; any `INVENTED` item is a hallucination or reformat artifact —
   resolve every finding by hand before continuing. The audit is **TTS-aware**:
   it normalizes the original's raw tokens against the restructured doc's spoken
   forms (via `tts_rewrite`), so a phone baked as "nueve dos ocho cuarenta…"
   counts as PRESENT and is NOT flagged LOST. A *wrong* spoken number is still
   flagged.
2. **Layer 2 (independent semantic reader):** spawn a **fresh subagent** (Agent
   tool) that has NOT seen your restructuring reasoning. Give it both documents
   cold and ask it to report, with quotes: (a) any information in the ORIGINAL
   that is missing from the restructured doc, (b) anything in the restructured
   doc not supported by the original, (c) any mis-grouping (e.g. a value attached
   to the wrong center/section). Fix or justify every item it raises. The audit
   report (both layers) is part of the deliverable.

### 3. Upload + wire into the flow
The grounded eval hits the **live** agent, so the new collection must be active
in the client's conversation flow first.
1. Upload the `.md` (oss-core-service knowledge route, multipart-free
   octet/markdown body + query params). Capture the new `collection_id`.
   ```bash
   curl -sS -X POST \
     "$OSS_BASE/api/v1/maria/voice/knowledge/files?title=<t>&filename=<f>.md&language=es" \
     -H "Authorization: Bearer $TENANT_TOKEN" -H "Content-Type: text/markdown" \
     --data-binary @NEW.md
   ```
2. Wire it into the client's **active conversation flow** (workflow clients
   resolve KB via the `conversation_flow_collections` junction, NOT
   `company.rag_collection_id`). Replace the flow's collections with the new id —
   this is the full target list, existing links not listed are dropped:
   ```bash
   curl -sS -X PUT \
     "$CORE_BASE/api/v1/conversation-flows/<FLOW_ID>/collections" \
     -H "x-api-key: <CLIENT_API_KEY>" -H "Content-Type: application/json" \
     -d '{"collection_ids":["<NEW_COLLECTION_ID>"]}'
   ```
   (`$CORE_BASE` is the maria-core backend the MCP talks to — in dev usually
   `http://localhost:3001`; confirm via `omniloy-mcp-server` `BACKEND_URL`.)
3. Optional but recommended: measure the FAQ block before/after by fetching
   `GET /api/v1/collections/<cid>/structure?include_summaries=true` for the old
   and new collections and rendering the top-level nodes the way
   `_render_active_flow_kb` does (doc line + one line per top-level node with its
   summary) — report char/token/node deltas.

### 4. Gate B — grounded eval (BLOCKING)

Two harnesses; both generate factual Q&A from the ORIGINAL and a judge classifies
each answer (**require `CONTRADICTS == 0`**):

**(a) `grounded_eval.py`** — hits the **live** agent, so it tests the collection
**wired into the flow**. Needs the new collection uploaded AND wired (step 3).
```bash
poetry run python tests/integration/customers/kb/grounded_eval.py \
  --original ORIGINAL.txt --api-key <CLIENT_API_KEY> --phone +34600000000 \
  --n 12 --client <label>
```

**(b) `faq_retrieval_eval.py` (RECOMMENDED, MAR-1070/T5)** — targets an EXPLICIT
`collection_id` via the same backend `get_node_content` path, so you can validate
an **additive, not-yet-wired** collection (no flow rewrite). It also does
atomic-fact coverage, conversational multi-turn (query reconstruction), RAG
metrics, a per-question `satisfactory` bool + 100% gate, and Excel/JSON to
`/tmp`. Use `--golden-out`/`--golden-in` to **A/B different docs on the IDENTICAL
question set** (essential — single-run %s vary; always A/B a candidate vs the
currently-wired collection, never ship a regression):
```bash
# generate + save a fixed golden, eval the NEW collection
poetry run python tests/integration/customers/kb/faq_retrieval_eval.py \
  --original ORIGINAL.txt --api-key <KEY> --collection-id <NEW_CID> \
  --golden-out /tmp/golden.json --client cand
# re-run the SAME golden against the current wired collection (baseline)
poetry run python tests/integration/customers/kb/faq_retrieval_eval.py \
  --original ORIGINAL.txt --api-key <KEY> --collection-id <WIRED_CID> \
  --golden-in /tmp/golden.json --client baseline
```
Upload the candidate as a NEW collection via maria-core (x-api-key), which talks
to the same PageIndex the agent reads — no oss-core/JWT needed:
`POST http://localhost:3001/api/v1/collections` then
`POST /api/v1/collections/{cid}/documents` (multipart `-F file=@NEW.md`).

Investigate every `CONTRADICTS`/`NO_INFO`/regression: use the judge's
`context_sufficiency` to split **retrieval miss** (right node not fetched →
fix discoverability/structure/wiring, step 1b-bis) from **answer-gen** (wrong
despite sufficient context → often a table the model misreads → step 1b prose).
Re-run until clean / until the candidate beats the baseline.

## Deliverables
- The accepted `NEW.md` (uploadable).
- The audit report (layer 1 output + layer 2 subagent findings, resolved).
- The grounded-eval result JSON (`/tmp/faq_grounded_<label>.json`) with 0
  CONTRADICTS, and the FAQ-block before/after deltas.

## Files in this skill
- `tabulation_scan.py` — advisory scan for tabulation candidates (step 1b).
- `tts_rewrite.py` — pure, unit-tested Spanish TTS rewriters (step 1c).
- `hard_data_audit.py` — Gate-A layer 1, now TTS-aware.
- `tests/integration/customers/kb/grounded_eval.py` (in maria-voice) — Gate B (live agent, wired collection).
- `tests/integration/customers/kb/faq_retrieval_eval.py` (in maria-voice) — Gate B (explicit collection_id, additive; A/B golden, coverage, Excel) — MAR-1070/T5. `faq_live_eval.py`, next to it, runs the retrieval golden set against the live agent.
- `tests/` — stdlib `unittest` for the helpers. Run:
  ```bash
  python3 -m unittest discover -s <skill>/tests
  ```

## Notes
- Customer harness reference: `tests/integration/customers/test_san_roque.py`
  (`san_roque_call`) — the generic `_client_call` in `grounded_eval.py` mirrors
  it, parameterized by api_key/phone.
- Reference run (San Roque, 2026-06-10): restructure cut the FAQ block 7723→4733
  chars / 35→19 nodes (−39%/−46%); the grounded eval caught 4/10 hallucinations
  from empty header nodes; after the MCP subtree fix, CONTRADICTS 4→0.
- A/B run (San Roque, 2026-06-19, `faq_retrieval_eval.py`, fixed 278-q golden):
  the **prose** 2026-06-10 collection scored **92.1%** satisfactory vs a new
  restructure with **tables+TTS 83.5%**, +overview lines 88.1%, and even
  **prose+TTS+overviews 87.4%** — i.e. every "improved" variant REGRESSED vs the
  shipped prose doc. Lesson: faithfulness (Gate A) ≠ answer quality; tables hurt;
  always A/B a candidate against the wired collection and never ship a regression.
