---
name: issue-analyze
description: Analyze ONE issue in a fresh context — verify the root cause in the actual code (DEFECT), walk the reuse ladder (REQUEST), find the proving line (QUESTION/DOCS) — and return a typed verdict with the minimal change and a verification plan. Read-only, never branches or comments. Run as a step of issue-loop in a virgin chat/agent, from the review-desk button, or standalone on a single issue.
---

# Issue analyze — one issue, fresh eyes

Resolve `<PLUGIN_ROOT>` from `<PLUGIN_ROOT>/refs/runtime.md` before running
commands.

Read-only. Designed to run in a **fresh context** (a new agent or chat with
nothing else in it): the analysis must stand on what the code says, not on
what a long session already believes. Never branch, never comment, never
edit.

Input: an issue number, a repo, and (when the caller knows it) the type.

## 1 · Gather

```bash
gh issue view <n> --repo <owner/repo> --json title,body,author,labels,assignees,createdAt,updatedAt,comments
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

Persist the complete handover, not only the desk summary. Use
`server/schemas/issue-analysis.json` for the result fields, including an explicit
`decision: null` when nothing is open. Merge through
`deskstate.update` (which honors `GIT_WORKFLOW_STATE_DIR`), preserving unrelated
keys and replacing every analysis field, including null decisions:

```json
{"issues": {"<n>": {"type": "DEFECT", "finding": "<una riga, in italiano>",
                     "size": "EASY", "phase": "SINGLE-PHASE",
                     "problem": "<problem>", "cause": "<verified cause or gap>",
                     "propose": "<minimal change, naming files>",
                     "verify": "<verification plan>", "decision": null,
                     "at": "<ISO timestamp with timezone, now>"}}}
```

`finding` is user-facing (the desk shows it): write it in Italian. Anything
meant to be posted on the issue stays in English. `at` is not decoration: the
desk compares it with the issue's last activity and marks the analysis *da
aggiornare* rather than showing a reading the issue has moved past. `type`
overrides the desk's guess from the labels — write the one you verified.

When run for a caller (issue-loop, the desk), the final message is one JSON
object matching that schema. When the caller persists it, return the result
without writing state yourself; the server supplies the timezone-aware `at`.
In chat, show the decision as text — **not inside a
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

## 5 · Name the follow-up, and close flat

Read-only means this skill cannot execute its own `Proposta`, so it never ends
with *"procedo?"* — a question promising something it cannot do. Close flat
instead, with the ONE follow-up the verdict implies. Never a menu, and never an
action taken: the follow-up is a line he reads. This skill does not spawn the
session, does not branch, does not comment.

- **an open decision still standing** → no follow-up: the ball is his. Say so
  and stop, whatever the size says.
- **SINGLE-PHASE** → `issue-work <n>`, an agentic session of its own: one
  worktree, one PR. Or `issue-loop <n>`, when he would rather keep the decision
  in the chat he is already in.
- **WORKFLOW** → a fresh chat, because the phases need a context that is not
  this one. Name the installed issue skill as its entry point **only when a phased-workflow
  plugin is actually installed** — check softly (a `wf:`-prefixed or
  `phased-workflow` skill appears among your available skills; never assume it,
  never require it). Use its exact available name and the host's invocation
  syntax: for example `codex-phased-workflow:issue` on Codex or `wf:issue`
  when that is the name Claude exposes. Do not infer an alias from detection
  of the plugin alone. Where it is absent, name `issue-work <n>`, which lays the
  phases out and leaves them on the issue as a comment.

**Skip this section for a caller** (issue-loop, the desk): there the JSON is the
handover, and what happens next is the caller's decision, not yours.

## 6 · Reuse contract for callers

Before skipping analysis, fetch the issue's current `updatedAt` and read its
record using `deskstate.load`. Use the shared predicate, not a date comparison
written by the agent:

```bash
PYTHONPATH="<PLUGIN_ROOT>/server" python3 -c 'import json, sys; import deskstate; record = deskstate.load(sys.argv[1]).get("issues", {}).get(sys.argv[2], {}); print(json.dumps(record if deskstate.issue_analysis_reusable(record, sys.argv[3]) else None))' '<owner/repo>' '<n>' '<updatedAt>'
```

`null` means analyze it. Reuse requires the full proposal, verification plan
and explicit decision field, plus a timezone-aware timestamp at least as new
as the issue activity. Old summary-only records and timestamps without a
timezone remain readable but cannot authorize skipping analysis. The desk uses
the same timestamp predicate, `deskstate.issue_analysis_fresh`, for its stale
badge; that badge alone does not establish completeness.

Say which analysis is reused and carry its open decisions forward. Reuse does
not waive checking current assignment, existing PRs or the relevant code before
implementation: issue activity alone cannot detect a code change.
