---
name: issue-analyze
description: Analyze ONE issue in a fresh context — verify the root cause in the actual code (DEFECT), walk the reuse ladder (REQUEST), find the proving line (QUESTION/DOCS) — and return a typed verdict with the minimal change and a verification plan. Read-only, never branches or comments. Run as a step of issue-loop in a virgin chat/agent, from the review-desk button, or standalone on a single issue.
---

# Issue analyze — one issue, fresh eyes

Read-only. Designed to run in a **fresh context** (a new agent or chat with
nothing else in it): the analysis must stand on what the code says, not on
what a long session already believes. Never branch, never comment, never
edit.

Input: an issue number, a repo, and (when the caller knows it) the type.

## 1 · Gather

```bash
gh issue view <n> --repo <owner/repo> --json title,body,author,labels,assignees,createdAt,comments
```

Comments matter: "cannot reproduce", failed previous PRs and reassignments
change the classification. Then check for existing work — any remote branch
matching `(^|/)<n>-`, any PR referencing the issue — and if found, read its
history before anything else (a closed PR usually carries a decision).

## 2 · Classify and answer the type's question

- **DEFECT** — the verified root cause, read in the actual code. Never trust
  the issue's own diagnosis: reproduce the reasoning against the source.
- **REQUEST** — the reuse ladder first: does the framework already do this?
  Does an existing helper cover it with a parameter? Only then sketch the
  minimal addition and name the gap.
- **QUESTION** — the answer, with the file and line that proves it, and
  whether the docs should have said it.
- **DOCS** — the correct statement and the file that has to change.

## 3 · Verdict

Return (and persist, section 4):

1. the exact minimal change respecting the repo idiom, citing precedents;
2. **SINGLE-PHASE or WORKFLOW** — one coherent change/commit/PR, or an order
   of phases to respect;
3. size EASY / MEDIUM / HARD;
4. a verification plan naming the existing test infra;
5. any open decision, with the options and their one-line consequences.

## 4 · Publish to the review desk

Merge into `~/.local/state/git-workflow/<owner>__<repo>.json` (preserve the
other keys):

```json
{"issues": {"<n>": {"type": "DEFECT", "finding": "<una riga, in italiano>",
                     "size": "EASY", "phase": "SINGLE-PHASE",
                     "at": "<ISO timestamp, now>"}}}
```

`finding` is user-facing (the desk shows it): write it in Italian. Anything
meant to be posted on the issue stays in English. `at` is not decoration: the
desk compares it with the issue's last activity and marks the analysis *da
aggiornare* rather than showing a reading the issue has moved past. `type`
overrides the desk's guess from the labels — write the one you verified.

When run for a caller (issue-loop, the desk), the final message is one JSON
object with those fields. In chat, show the decision as text — **not inside a
code fence**, which flattens the labels he is scanning and offers a copy
nobody wants:

(the fence below delimits the template — your output has no fence)

```markdown
**#<n>** — <TYPE>, <EASY|MEDIUM|HARD>, <SINGLE-PHASE|WORKFLOW>

**Problema** · <cosa è rotto o richiesto, una riga>
**Causa** · <la causa verificata nel codice, o il gap>
**Proposta** · <la mossa minima, una riga>
**Verifica** · <come si dimostra, con l'infra di test che esiste>
```

An open decision goes after the block, as a question with the named
alternatives and their one-line consequence — never folded into `Proposta`.
