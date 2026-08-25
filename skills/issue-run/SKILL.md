---
name: issue-run
description: Work the triaged issue batch step by step — one issue-analyze per candidate in a fresh agent, the selection box, then branch + PR fan-out for the picked ones — notifying the review desk at every step. Explicit invocation ONLY, or as the continuation issue-triage offers, or on a desk button; never trigger it on your own.
---

# Issue run — from the shortlist to the PRs

Conversation in Italian; everything persisted (commits, PRs, code, comments)
in English. **NO AI/tool attribution anywhere** — contractual obligation.

Input: the batch from `issue-triage` (re-run its collection if stale). At
**every step boundary** notify the desk:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/server/notify.py --repo <owner/repo> [--pr <n>] "<one line>"
```

**Autonomy**: analysis is read-only and free. Branches, commits and PRs are
approved by the selection box — that pick is the gate. A merge is never
autonomous.

## Step 1 — Analyze: one fresh agent per issue

Spawn one **issue-analyze** per candidate — all in a single message so they
run concurrently, each in a virgin context (Explore/read-only agent, or a
new chat when run manually). Hand each agent the issue number, the repo and
the type; it follows `${CLAUDE_PLUGIN_ROOT}/skills/issue-analyze/SKILL.md`
and returns the typed verdict JSON, persisting it to the desk state (the
dashboard updates as each one lands). Notify: `analisi #<n>: <one line>`.

## Step 2 — The selection box

One `AskUserQuestion`, `multiSelect: true`, top 4 SINGLE-PHASE issues by
impact (real damage first, then blocks-someone, DOCS last). Label
`#<n> — <slug>`; description `@author · date · TYPE · <the finding> · SIZE`,
plus `· assigned to @login` when somebody else holds it. WORKFLOW issues are
never options. An empty selection is a decision, not an error. A claim in
"Other" ("questa la prendo io") assigns the issue to the user first.

## Step 3 — Claim, then fan out one fix agent per picked issue

Assign every picked issue that has no assignee **before** spawning
(`gh issue edit <n> --add-assignee @me`), comment "Working on this.", and
never touch an issue somebody else holds. Base branch: where recent merged
fix PRs target.

Every fix agent runs with `isolation: "worktree"` and its prompt carries,
verbatim:

> Push to `origin`. Open the PR with `gh pr create --repo <owner>/<repo>
> --base <base>`. Do NOT fork, do NOT add remotes, do NOT open a PR against
> any other repo. If a push is rejected for permissions, STOP and report.

plus the worktree traps: `gnr.*` imports resolve to the main checkout unless
`PYTHONPATH=<worktree>/gnrpy` and `module.__file__` is asserted inside the
worktree; anything under `resources/`/`projects/` needs a scratch
`GENRO_GNRFOLDER` whose directory is named `gnr`; never `git stash`
(worktrees share one stash stack) — use a patch file; full-suite counts from
concurrent agents are worthless under `tests/sql/` (the pg fixture pkills
sibling postgres) so gate on `pytest gnrpy/tests/ -q --ignore=gnrpy/tests/sql`.

Hand the agent the analyst's verdict verbatim. One picked issue = one PR,
every type and size: **draft** when a decision is open (posted on the ISSUE,
linked from the body), **ready** when complete and verified. Body sections:
Problem/Root cause or Motivation, Change, Verification (never claim what was
not run), Related issue with `Fixes #<n>` — then verify
`closingIssuesReferences` is non-empty. `--assignee` the author,
`--reviewer` resolved from CODEOWNERS on the touched paths, then confirm
`reviewRequests` landed. Notify the desk at branch, push and PR.

## Step 4 — Unblock the rest

One comment per issue that needs its author (missing repro, request already
satisfied with the snippet, WORKFLOW phases) — never on issues analyzed but
not picked, and never where the ball is already the user's. Label what was
classified from the repo's own label set.

## Step 5 — Report

Tables: PRs opened (issue → PR → draft/ready → verified → assignee+reviewer),
WORKFLOW ones with their phases, decisions left to the user, finished work
found on stale branches, analyzed-not-proposed, proposed-not-picked. Close
with three prose lines and a final desk notification: `issue-run chiuso:
<N> PR aperte, <M> in attesa di decisione`.
