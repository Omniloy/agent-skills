---
name: create-jira-work-items
description: >-
  Creates Jira work items following the team's mandatory structure: a Feature
  plus a linked Dev task and an independent Verification. Handles duplicate
  checking, Given/When/Then user stories, correct assignees (dev vs. independent
  verifier), and a review step before anything is created. Use this skill ANY
  time the user wants to create, open, write, or file a Jira issue — Feature,
  story, task, ticket, dev task, verification — or whenever they ask to use the
  Jira MCP at all, even if they only describe a requirement that should be
  tracked ("habría que crear una tarjeta para...", "create a ticket", "new
  story", "abre una feature"). When in doubt about whether a Jira request needs
  this skill, use it: it encodes structure and assignment rules that are easy to
  get wrong.
allowed-tools: AskUserQuestion, mcp__atlassian__createJiraIssue, mcp__atlassian__createIssueLink, mcp__atlassian__searchJiraIssuesUsingJql, mcp__atlassian__getJiraIssue, mcp__atlassian__editJiraIssue, mcp__atlassian__getJiraProjectIssueTypesMetadata, mcp__atlassian__getVisibleJiraProjects, mcp__atlassian__lookupJiraAccountId, mcp__atlassian__atlassianUserInfo, Read
user-invocable: true
---

# Create Jira Work Items

## The golden rule

A Feature never lives alone. Every Feature ships with **at least two children,
both linked to it**:

1. a **Dev task** (`Tarea`) — the implementation work, and
2. a **Verification** — independent proof the work does what the Feature claims.

The Verification exists to be a *second pair of eyes*. So its assignee must be a
**different person** from whoever owns the product Feature and the dev task. If
the same person specs, codes, and verifies, there was no independent check —
which defeats the purpose. Enforce it: `verification_assignee ≠ feature_assignee`
and `≠ dev_assignee`.

Concretely, the person running this usually owns **both** the Feature (product)
and the Dev task, and the Verification goes to a teammate. So "the verification
can't be the same person" in practice means "not the requester".

## Structure (read this before creating anything)

```
Epic  (existing — the Feature's parent)
└── Feature                       parent = Epic
      ├── Tarea (dev)             linked to Feature with "Relates"
      └── Verification            linked to Feature with "Relates"
```

Two things that are easy to get wrong:

- **Children are peers, not subtasks.** Feature, Tarea and Verification all sit
  at the same hierarchy level, so the Dev task and Verification **cannot** hang
  off the Feature through the `parent` field. Link them with an issue link of
  type **`Relates`**. Do **not** create them as Jira sub-tasks.
- **Only the Epic is a real parent.** The Feature's epic goes in the Feature's
  `parent` field on creation. The children get no `parent`.

Default behaviour: create the **full set** (Feature + Dev + Verification) unless
the user asks for something narrower.

## What is fixed vs. what you look up

This skill is shared across the team, so nothing person- or epic-specific is
hard-coded. Resolve it at runtime:

- **Site / Cloud ID:** discover with `getVisibleJiraProjects` (or
  `atlassianUserInfo`) if you don't already have it in context. Reuse the same
  cloudId for every call in the flow.
- **Project:** SOF, MAR, CFC, … — ask only if ambiguous; otherwise infer from
  context.
- **Epics:** never guess or hard-code them. List the project's epics with
  `searchJiraIssuesUsingJql`:
  `project = "<PROJECT>" AND issuetype = Epic AND statusCategory != Done ORDER BY created DESC`.
  Present them and let the user pick. **Always** pin the Feature to an epic — if
  the user didn't name one, ask; never let a Feature land with no epic.
- **Issue type names:** the team uses `Feature`, `Tarea` (dev), `Verification`,
  `Error` (bug). If a project rejects one, confirm the real names with
  `getJiraProjectIssueTypesMetadata`.
- **People:** resolve the requester with `atlassianUserInfo` (→ Feature + Dev
  assignee). For the verifier, use the name the user gives and resolve the
  accountId with `lookupJiraAccountId`; if they didn't name one, ask who should
  verify. Validate the verifier isn't the Feature/Dev owner.

## Workflow

### 1. Understand the request

If essentials are missing, ask (one focused question at a time): who the feature
is for, what problem it solves, acceptance criteria, the epic, the verifier, and
the project (only if ambiguous). Prefer asking over inventing acceptance
criteria — a vague story creates rework downstream.

### 2. Duplicate check

Pull 2–3 keywords from the request and search before creating anything:

```
project = "<PROJECT>" AND text ~ "<keywords>" ORDER BY created DESC
```

If you find likely duplicates, show them and stop for a decision:

```
Posibles duplicados:
1. <KEY> — <título> (<estado>) — <browse-url>/<KEY>
   Similitud: <Alta/Media/Baja> — <razón>

a) Son diferentes — proceder
b) Es duplicado de #N — cancelar
c) Está relacionado con #N — crear y enlazar
```

Wait for the answer. If nothing looks like a duplicate, continue silently.

### 3. Draft everything for review

Draft the Feature description plus the summaries of the Dev task and the
Verification, and state the assignees (Feature+Dev → requester; Verification →
the chosen teammate). Show all of it and **wait for approval — create nothing
yet.** This is the moment to catch a wrong epic, missing criteria, or a verifier
who is actually the dev.

Use the team's real format: `##` headings with bullet acceptance criteria, in
the user's language. These mirror existing tickets (Feature MAR-1078, Dev
MAR-1079, Verification MAR-1080) — write so a new ticket reads like the rest.

**Feature** (summary = the feature name):

```
## Context
<why this is needed; the problem being solved>

## Scope
<what this feature delivers>

## Acceptance Criteria
* <criterion 1>
* <criterion 2>
```

**Dev task** (summary `Implementar <feature>`):

```
## Descripción
<what to build, plus the key technical insight / approach>

## Acceptance Criteria
* <criterion 1>
* <criterion 2>

## Referencias
* Feature de producto: <FEATURE-KEY>. Verificación: <VERIFICATION-KEY>.
```

**Verification** (summary `Verificar <feature>`):

```
## Context
<what the feature changed>

## Verification Scope
<what to validate independently>

## Acceptance Criteria
* <criterion 1>
* <criterion 2>
```

The `Referencias` cross-links are filled with the real keys once they exist
(create order is Feature → Dev → Verification, so the Feature key is known when
you write the children; backfill the Verification key with `editJiraIssue` if
you want it complete). The structural link is the `Relates` issue link — the
references in the body are a convenience on top.

### 4. Create, in order

1. **Feature** — `createJiraIssue`: `projectKey`, `issueTypeName: "Feature"`,
   `summary`, `description` (the template), `parent: <EPIC-KEY>`,
   `assignee_account_id: <requester>`,
   `additional_fields: {"priority": {"name": "Medium"}}` (Medium unless told
   otherwise; add `labels` if relevant).
2. **Dev task** — `createJiraIssue`: `issueTypeName: "Tarea"`,
   `assignee_account_id: <requester>`. Then link it to the Feature:
   `createIssueLink(inwardIssue: <FEATURE>, outwardIssue: <DEV>, type: "Relates")`.
3. **Verification** — `createJiraIssue`: `issueTypeName: "Verification"`,
   `assignee_account_id: <verifier>`. Then link it:
   `createIssueLink(inwardIssue: <FEATURE>, outwardIssue: <VERIFICATION>, type: "Relates")`.

Before creating the Verification, re-check its assignee is not the Feature/Dev
owner. If it is, ask for a different verifier — don't silently proceed.

If the user chose option (c) in step 2, also `Relates`-link the existing issue.

### 5. Confirm

Report the keys with links and a one-line summary each:

```
Feature:      <KEY> — <title> — <browse-url>/<KEY>
Dev:          <KEY> (→ <requester>)  — <link>
Verification: <KEY> (→ <verifier>)   — <link>
```

## Jira formatting rules

Match how the team's existing tickets read (see MAR-1078 / MAR-1079 / MAR-1080):

- Structure descriptions with `##` headings and bullet lists. `**bold**` is fine
  and used in practice — write naturally, don't strip formatting.
- Acceptance Criteria live **inside the description** as a `## Acceptance
  Criteria` bullet list, not in a separate field.
- Dev and Verification descriptions cross-reference the Feature (a `## Referencias`
  section), on top of the `Relates` issue links.
- Prefer asking over inventing acceptance criteria when context is thin.
- Don't write to deprecated custom fields; stick to the standard fields above.
