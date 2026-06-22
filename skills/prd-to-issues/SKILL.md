---
name: prd-to-issues
description: Turn a PRD (or any spec doc) into a super-visual, self-contained HTML implementation plan of GitHub Epics + sub-issues + labels — each issue with explicit Functional and Technical sections — and, on approval, create the real Epics, sub-issues, labels and milestones via the GitHub CLI. Triggers on: "create issues from this PRD", "turn the PRD into GitHub epics", "register pending work as issues", "issue plan from spec", "PRD to issues".
---

# PRD → GitHub Issues (visual plan, then `gh` create)

This skill is the `/visual-recap` concept applied forward to project planning, but it
renders **directly to a standalone HTML file** (no Plan MCP / bridge needed) and ends in
real GitHub issues. You read a PRD, author a structured `manifest.json`, render it to a
detailed visual `plan.html`, get the human's approval, then create the Epics + sub-issues +
labels + milestones with the `gh` CLI from that same manifest.

The deliverable the user reviews is the **HTML plan**. Nothing is created on GitHub until they
approve. Issue creation is outward-facing — always confirm first.

## Core principles (borrowed from visual-recap)

1. **Ground everything in the real doc.** Every Epic and issue must trace to a PRD section /
   requirement — cite it (`prd_refs`). Never invent scope the doc doesn't support. If you
   inferred something, mark it inferred in the body.
2. **Substantial, not thin.** A real plan is not a flat bullet list. Each Epic carries a
   narrative ("what & why"), its PRD refs, optional visuals, and a set of detailed sub-issues.
   Each sub-issue carries a **Functional** section and a **Technical** section (see below).
3. **Visual headline.** Lead with diagrams. **Reuse images/diagrams that already exist in the
   doc** (ASCII pipeline diagrams, embedded PNGs, PDF figures) — extract or screenshot them —
   and **create the missing ones** (architecture diagram, data-model ERD, phase roadmap).
   Use the block types below; do not hand it over as prose only.
   **Carry UX mockups into the issues, not just the HTML** — see "Images & design mockups".
4. **Map to the codebase, not the void.** When the repo already provides something (a table, a
   route, a tool), say "extend X at `path`" instead of "build X". Reuse the visual-recap of the
   codebase if one exists.
5. **Idempotent, reversible creation.** The create step checks for existing issues by title and
   skips dupes; labels/milestones are upserted. Default is `--dry-run`; real creation needs
   `--apply`.

## Functional vs Technical (required on every issue)

Every sub-issue MUST have both, clearly separated — this is the point of the skill:

- **Functional** — *what and why, for a non-engineer*: the user-facing behavior, the workflow
  step it serves, the persona, and the **acceptance criteria** (checklist of observable
  outcomes). No implementation detail.
- **Technical** — *how, for the engineer*: concrete files/modules to add or change (with
  paths), data-model/migrations, API endpoints, libraries, external services, the agent
  tools/skills involved, sequencing, and risks. Reference the inherited platform explicitly.

## Images & design mockups (UX-bearing PRDs)

If the PRD embeds **UX mockups** (screenshots of screens, console layouts, component states),
they are the **target UI** — implementers must build *to them*, not to prose. A "Figura 6"
reference in text is useless to a coding agent that can't see the picture. So:

1. **Extract** all embedded images from the doc (e.g. unzip a `.docx`/`.pptx`, render PDF pages).
   Triage into **UX mockups** (target UI) vs **architecture diagrams** (rationale) vs decoration.
2. **Use them in the HTML plan** as `image` blocks (the overview headline) — already covered.
3. **Commit the UX-relevant figures into the TARGET REPO** (e.g. `docs/design/figures/`) with a
   `README.md` mapping each figure → the epic/feature it informs. This is what makes them
   versioned and **visible to implementation agents** (worktree agents check out from git; an
   untracked `.prd-issues/assets/` is invisible to them). Do this as (or before) the create step.
4. **Link them from the issues that need them.** Put `design_refs` on those issues so the issue
   body renders a **🎨 Design reference** section. `design_refs` is a list of
   `{src, label?, caption?}` (use the repo blob path/URL, e.g.
   `https://github.com/<owner>/<repo>/blob/main/docs/design/figures/fig6.png`); set an optional
   `design_note` on the issue for a one-line instruction. For **private repos**, *link* the file
   (blob URL) rather than trying to embed — raw-image embeds need auth and won't render.
5. **Rebrand caveat.** If the mockups show an old/proposal product name, say so in the
   `design_note` ("match layout/components; rebrand name X → Y in the UI").

Only attach a figure to issues it actually informs (the chat-UI mockup → the chat component issue,
the dashboard mockup → the dashboard issue) — don't bulk-attach every figure to every issue.

## Workflow

1. **Locate the PRD and any companion docs/images.** Read them fully. If a `/visual-recap` of
   the target codebase exists (e.g. `plans/*/plan.mdx`), read it so issues reference real
   files/tables. List the changed/relevant surfaces before authoring (an inventory pass).
2. **Confirm the target repo.** `gh repo view --json nameWithOwner` (or ask). Note the default
   branch and whether issues already exist (`gh issue list --limit 100 --state all`).
3. **Author `manifest.json`** next to the plan (default `./.prd-issues/manifest.json`) using the
   schema in `references/manifest.schema.json`. Define:
   - `meta` (title, source doc, repo, generated date),
   - `labels` (epic + one per phase + `area:*` + `type:*`, each with a hex `color`),
   - `milestones` (usually one per PRD phase),
   - `overview_blocks` (the visual headline: diagrams, mermaid, images, data-model, file-tree,
     callouts, tables — reuse doc visuals here),
   - `epics[]`, each with `issues[]` carrying `functional_md`, `technical_md`, `acceptance[]`,
     `prd_refs[]`, `labels[]`, `milestone`, optional `estimate` and `blocks[]`.
   Copy block shapes from `references/example-manifest.json`.
4. **Render the HTML:**
   `python3 scripts/prd_issues.py render --manifest .prd-issues/manifest.json --out .prd-issues/plan.html`
   It validates the manifest, then emits a self-contained `plan.html` (inline CSS; mermaid via
   CDN with a code fallback). Open/screenshot it and **visually check** it before sharing —
   fix overlaps/empty sections, then re-render.
5. **Hand the HTML to the user for approval.** Surface `plan.html` (send the file). Summarize:
   N labels, M milestones, E epics, K sub-issues. Ask for go/no-go and edits. Do NOT create
   anything yet.
6. **Dry-run, then create on approval:**
   - `python3 scripts/prd_issues.py create --manifest ... --repo <owner/name> --dry-run`
     prints exactly what would be created.
   - On explicit approval:
     `python3 scripts/prd_issues.py create --manifest ... --repo <owner/name> --apply`
     This upserts labels + milestones, creates each Epic, creates its sub-issues (applying
     labels + milestone), links them as **native GitHub sub-issues** (REST `sub_issues` API)
     with a **task-list fallback** in the Epic body, and writes back a `created.json` map of
     titles → issue numbers/URLs for idempotency and re-runs.
7. **Report** the created Epics with their URLs and the sub-issue counts.

## The script

`scripts/prd_issues.py` (stdlib only; needs `gh` authenticated for `create`). Subcommands:

- `render  --manifest <f> --out <plan.html>` — manifest → visual HTML. Pure local, safe.
- `create  --manifest <f> --repo <owner/name> [--dry-run|--apply] [--limit-epic KEY]` —
  manifest → GitHub. `--dry-run` is the default and prints the plan; `--apply` performs it.
  Re-running `--apply` is safe: existing issues (matched by exact title) are skipped, labels and
  milestones are upserted, and only missing links are added.

The same manifest drives both the HTML and the GitHub issues, so what the user approved on
screen is exactly what gets created. The issue body the script writes is built deterministically
from `functional_md` + `acceptance` + `technical_md` + `prd_refs` — identical to the HTML.

## Guardrails

- **Never create issues without explicit approval** (outward-facing). Default to `--dry-run`.
- **Idempotency:** match by exact title within the repo; skip existing. Keep `created.json`.
- **Labels:** upsert with `gh label create --force` (creates or updates color/description).
- **Sub-issue linking:** try native first (`gh api repos/{o}/{r}/issues/{n}/sub_issues -F
  sub_issue_id=<childRestId>`); if the API/plan doesn't support it, fall back to a `- [ ] #n`
  task list in the Epic body (always written regardless, so the hierarchy is visible).
- **Secrets:** never copy tokens/keys from the PRD into issue bodies or the HTML — redact.
- **Scope honesty:** if the doc is ambiguous, add an Epic/issue marked `[needs decision]` rather
  than inventing a resolution; surface it in an Open Questions block.

## References

- `references/manifest.schema.json` — the manifest contract (validated by `render`).
- `references/example-manifest.json` — a small, complete, valid example to copy block shapes from.
