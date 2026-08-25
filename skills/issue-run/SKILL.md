---
name: issue-run
description: Work the open issues one at a time — take the most urgent, analyze that one in a fresh agent, propose it in four lines, and on a go-ahead fix it in a worktree and open the PR, then move to the next until the user says stop. Notifies the review desk at every step. Explicit invocation ONLY, or as the continuation issue-triage offers, or on a desk button; never trigger it on your own.
---

# Issue run — the most urgent one, then the next

Conversation in Italian; everything persisted (commits, PRs, code, comments)
in English. **NO AI/tool attribution anywhere** — contractual obligation.

## The shape: one at a time, until he says stop

```
build the candidate list
repeat:
    take the most urgent one left
    analyze THAT one in a fresh agent
    propose it in four lines, and wait
    on a go-ahead: claim it, fix it in a worktree, open the PR
until he says basta, or nothing urgent is left
report against what was done and what remains
```

**Never analyze ahead of the proposal.** This skill used to open with one
`issue-analyze` per candidate — ten agents reading ten issues — and then ask
the user to pick four. Six of those reads were thrown away every run. One
analysis per proposal costs what the user actually accepts.

**Never present the next issue before the current one is settled.** The whole
point of the format is that he holds one decision in his head at a time.

**Stopping is a normal ending, not an abort.** `basta` / `stop` / `per ora ok`
/ an unanswered proposal all mean: close with the report (Step 4) as it
stands. What was not reached is listed, not lost.

At **every step boundary** notify the desk, and mark the issue you are on so
its row is highlighted while you work it:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/server/notify.py --repo <owner/repo> \
  --pr <n> --working "<one line: what you are doing on this one>"
```

**Autonomy**: analysis is read-only and free. Branches, commits and PRs are
approved by the go-ahead on that issue, one issue at a time — that word is
the gate. A merge is never autonomous.

## Step 0 — The candidate list, cheapest source first

In order:

1. **A desk handed you `rows`** — use `shortlist` from that file. Already
   filtered (never looked at, nobody on it, no PR) and ordered newest first,
   with a `cross` note per issue saying whether a branch or a PR exists.
2. **`issue-triage` ran in this session** — use its batch.
3. **Neither** — build it yourself: the three reads the desk uses are cheap
   and need no model (`git ls-remote --heads origin` for branches,
   `is:issue is:open commenter:<me>` for what you have already looked at, the
   queue's `closingIssuesReferences` for existing PRs). Do NOT demand a
   triage first: it is the same data, and refusing to start is worse than
   spending 1.4s on it.

Then order by impact, reading the body and not the label: 1) evidence of real
damage (traceback, crash, data loss); 2) blocks someone else; 3) everything
else; 4) DOCS last. That ordering is the only thing here a model is needed
for — the filtering is not.

## Step 1 — Analyze the one you are about to propose

Spawn ONE **issue-analyze** in a virgin context (Explore/read-only agent, or
a new chat when run manually). Hand it the issue number, the repo and the
type; it follows `${CLAUDE_PLUGIN_ROOT}/skills/issue-analyze/SKILL.md` and
returns the typed verdict JSON, persisting it to the desk state. Notify:
`analisi #<n>: <one line>`.

## Step 2 — Propose it in four lines, then wait

Same format as `pr-run`'s Lane B, because it is the same act:

```
#<n> — <author> opened it, <TYPE>, <age>
what:    <one line: what is actually broken or asked>
finding: <one line: the verified root cause, or the gap>
propose: <one line: exactly what you will do if he says go>
```

`propose` is a single concrete action, never a menu. If it cannot be one
line, the analysis is not finished.

Then wait. `vai` / `ok` / `procedi` / `si` means execute and move on without
re-asking. Anything else is a conversation about THAT issue: answer it,
adjust the proposal, ask again.

Say plainly when a proposal is not yours to execute: an issue somebody else
holds (never touch it), a WORKFLOW-sized one that needs phases rather than a
PR, a new issue whose assignment belongs to the Owner. Those end with what he
should do, and you move to the next one.

## Step 3 — On a go-ahead: claim it, fix it, open the PR

Assign the issue if it has no assignee **before** starting
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

Hand the agent the analyst's verdict verbatim. One accepted issue = one PR,
every type and size: **draft** when a decision is open (posted on the ISSUE,
linked from the body), **ready** when complete and verified. Body sections:
Problem/Root cause or Motivation, Change, Verification (never claim what was
not run), Related issue with `Fixes #<n>` — then verify
`closingIssuesReferences` is non-empty. `--assignee` the author,
`--reviewer` resolved from CODEOWNERS on the touched paths, then confirm
`reviewRequests` landed. Notify the desk at branch, push and PR.

Then loop: back to Step 1 with the next most urgent, unless he has said stop.

## Step 4 — Report, whenever the loop ends

Reached by `basta`, by an exhausted list, or by a proposal he did not answer.
Tables: PRs opened (issue → PR → draft/ready → verified → assignee+reviewer),
WORKFLOW ones with their phases, decisions left to the user, finished work
found on stale branches, and — the one that makes stopping halfway clean —
**what was never reached**, in queue order, so picking it up later starts
where this left off.

One comment per issue that genuinely needs its author (missing repro, request
already satisfied with the snippet, WORKFLOW phases) — never on an issue you
merely did not get to, and never where the ball is already the user's.

Close with three prose lines and a final desk notification, which also
unlocks the desk's button and drops the highlight:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/server/notify.py --repo <owner/repo> \
  --done run:issue-run "issue-run chiuso: <N> PR aperte, <M> non raggiunte"
```
