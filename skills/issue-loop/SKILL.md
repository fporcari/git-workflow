---
name: issue-loop
description: Work the open issues in a loop — take the most urgent, analyze it in a fresh agent, propose it in four lines, and on a go-ahead fix it in a worktree and open the PR, then the next until the user says stop. Takes the numbers to work and a batch size, so several can be proposed together and fixed in parallel worktrees. Notifies the review desk at every step.
argument-hint: opzionale — "1145,1128,1059" per lavorare solo quelle · "batch=4" per proporne 4 insieme
disable-model-invocation: true
---

# Issue loop — the most urgent one, then the next

Conversation in Italian; everything persisted (commits, PRs, code, comments)
in English. **NO AI/tool attribution anywhere** — contractual obligation.

## The shape: a loop, until he says stop

```
read the mandate ($ARGUMENTS)
build the candidate list, unless he named the numbers
repeat:
    take the next BATCH (default 1) off the list
    analyze them in parallel, in fresh agents
    propose them, and wait for one answer covering all of them
    for the approved ones: claim, fix, open the PR
until he says basta, or the list is empty
report against what was done and what remains
```

It is a **loop**, not a run: it never promises to reach the bottom of the
queue. Stopping halfway is a normal ending, and what was not reached is
listed rather than lost.

**Never analyze ahead of the proposal.** Analysing every candidate and then
asking him to pick throws most of those reads away; one analysis per proposal
costs what the user actually accepts. A batch is the exception he asked for
explicitly, and it is opt-in for exactly that reason.

**Autonomy**: analysis is read-only and free. Branches, commits and PRs are
approved by the go-ahead on that issue — that word is the gate, whether it
covers one issue or four. A merge is never autonomous.

## Step -1 — The mandate: `$ARGUMENTS`

| what he typed | what it means |
|---|---|
| `1145,1128,1059` (`#` optional) | **the working set**: exactly those, **in that order**, and then stop. Skip Step 0 entirely and do not rank — he already chose, his order wins |
| `batch=N` | propose N together instead of one. Clamped to **1..4** |
| `mine` | restrict the candidate list to issues assigned to him |
| anything else | a scope note, not a filter — say back how you read it |

**Default `batch=1`**: one at a time is the shape that costs nothing when he
says no. `batch` is him saying *"I have already decided, spend the reads"* —
which is why it pairs naturally with a list of numbers, where no analysis
can be wasted.

**Clamped to 4** because one `AskUserQuestion` box holds four options: a
fifth proposal would cost the answer its clickable path for nothing. More
than four numbers with `batch=4` is fine — it works them four at a time.

**From a desk button** the event is
`{"kind": "run", "flow": "issue-loop", "ns": [...], "batch": N}`. `ns` is
the rows he picked by hand in the dashboard and means exactly what the
typed list means: those, in that order, then stop. Do not re-ask which ones
— the picking was the answer.

## Step 0 — The candidate list, cheapest source first

Skip this entirely when he named the numbers. Otherwise, in order:

1. **A desk handed you `rows`** — use `shortlist` from that file: the numbers
   the desk already filtered (never looked at, nobody on it, no PR) and
   ordered newest first. Every one of them is a full row under `issues` in
   the same file, with a `cross` note saying whether a branch or a PR
   exists.
2. **`issue-triage` ran in this session** — use its batch.
3. **Neither** — build it yourself: the three reads the desk uses are cheap
   and need no model (`git ls-remote --heads origin` for branches,
   `is:issue is:open commenter:<me>` for what you have already looked at, the
   queue's `closingIssuesReferences` for existing PRs). Do NOT demand a
   triage first: it is the same data, and refusing to start costs more than
   the seconds those three reads take.

Then order by impact, reading the body and not the label: 1) evidence of real
damage (traceback, crash, data loss); 2) blocks someone else; 3) everything
else; 4) DOCS last. That ordering is the only thing here a model is needed
for — the filtering is not.

## Step 1 — Analyze the ones you are about to propose

Spawn one **issue-analyze** per issue in the batch, in virgin contexts
(read-only agents, or a new chat when run manually), **all at once**: the
batch's whole latency win is here, not in the execution. Hand each the issue
number, the repo and the type; it follows
`${CLAUDE_PLUGIN_ROOT}/skills/issue-analyze/SKILL.md` and returns the typed
verdict JSON, persisting it to the desk state. Notify:
`analisi #<n>: <one line>`.

An analysis that comes back empty or failed does not sink the batch: propose
the others and say that one could not be read, with why.

## Step 2 — Propose, then wait

Same four lines as `pr-loop`'s Lane B, because it is the same act — and the
format does **not** degrade in a batch, it repeats:

```
#<n> — <author> opened it, <TYPE>, <age>
what:    <one line: what is actually broken or asked>
finding: <one line: the verified root cause, or the gap>
propose: <one line: exactly what you will do if he says go>
```

`propose` is a single concrete action, never a menu. If it cannot be one
line, the analysis is not finished.

**With `batch=1`** (the default): print the block and wait. `vai` / `ok` /
`procedi` / `si` executes and moves on. Anything else is a conversation
about THAT issue.

**With `batch=N`**: print all N blocks, separated by a blank line, then ask
**once** — one `AskUserQuestion`, one question, `multiSelect: true`, one
option per proposal (`#1145 vai`, `#1128 vai`, …). Ticked means go, unticked
means skip, and "Other" is there for *"il 2 sì ma senza toccare i test"*. A
plain typed answer (`1 vai, 2 no, 3 vai`, or `tutte vai`) is accepted just
the same — the box is the convenience, not the protocol.

**A conversation about one does not hold up the others.** The clean `vai`
proposals start immediately; the one he wants to discuss returns at the head
of the next batch. Otherwise the batch buys nothing.

Say plainly when a proposal is not yours to execute: an issue somebody else
holds (never touch it), a WORKFLOW-sized one that needs phases rather than a
PR, a new issue whose assignment belongs to the Owner. Those end with what he
should do, and you move on.

## Step 3 — What can run in parallel, and what cannot

**Never hand the batch straight to N agents.** Build the conflict graph over
the approved set first, take its connected components, and run the
components in parallel while the members of one component run in sequence,
in queue order. Two items conflict when any of these holds:

- **they touch the same file** — the intersection of the files each
  analyst's minimal change names;
- **their PRs would stack** — one's base is the other's head branch;
- **they meet on the same issue** — the same issue closed by both, or one
  closing the other. This is also what stops two agents racing on
  `gh issue edit --add-assignee`;
- **one of them is a merge or a realign** on a base another shares — a merge
  into the base invalidates every merge state computed a moment ago, so a
  merge or a realign **runs alone**;
- they would push the same head branch.

**Unknown means sequential.** An issue whose analysis names no files — a
REQUEST with the design still open — is not batchable: it runs alone, after
the parallel groups.

Say the grouping **before launching**, one line per group:

> gruppo 2 (sequenziale): #1145 → #1128, toccano entrambe
> `gnrpy/gnr/web/gnrbaseclasses.py`

## Step 4 — Claim it, fix it, open the PR

Assign the issue if it has no assignee **before** starting
(`gh issue edit <n> --add-assignee @me`), comment "Working on this.", and
never touch an issue somebody else holds. Base branch: the repo's default
branch read with `gh repo view --json defaultBranchRef`, never the one the
harness reports.

Every fix agent runs with `isolation: "worktree"` and its prompt carries,
verbatim:

> Push to `origin`. Open the PR with `gh pr create --repo <owner>/<repo>
> --base <base>`. Do NOT fork, do NOT add remotes, do NOT open a PR against
> any other repo. If a push is rejected for permissions, STOP and report.

plus the worktree traps, every one of which bites harder with several agents
at once:

- `gnr.*` imports resolve to the **main checkout** unless
  `PYTHONPATH=<worktree>/gnrpy`, and `module.__file__` is asserted inside the
  worktree;
- anything under `resources/`/`projects/` needs a scratch `GENRO_GNRFOLDER`
  whose directory is named `gnr` — **a distinct one per agent**. One shared
  scratch folder across concurrent agents is a race that only shows up at
  runtime;
- **never `git stash`**: worktrees share one stash stack. Use a patch file;
- full-suite counts from concurrent agents are worthless under `tests/sql/`
  (the pg fixture pkills sibling postgres), so gate on
  `pytest gnrpy/tests/ -q --ignore=gnrpy/tests/sql`. With several agents even
  that is noisy: each reports **both** its narrowest test and the suite, and
  **the gate is the narrow one**.

Hand the agent the analyst's verdict verbatim. One accepted issue = one PR,
every type and size: **draft** when a decision is open (posted on the ISSUE,
linked from the body), **ready** when complete and verified. Body sections:
Problem/Root cause or Motivation, Change, Verification (never claim what was
not run), Related issue with `Fixes #<n>` **in the PR body** — then verify
`closingIssuesReferences` is non-empty. `--assignee` the author,
`--reviewer` resolved from CODEOWNERS on the touched paths and checked
against `gh api repos/<owner>/<repo>/collaborators` (a login with no access
is dropped without an error), then confirm `reviewRequests` landed.

Then loop: back to Step 1 with the next batch, unless he has said stop.

### When one of them fails

Each agent returns `{n, status: ok|failed, pr, why}`. Then:

- the report is **per item, never aggregate**. There is no "batch completato"
  line: a group of four with one failure is three successes and one failure,
  said in four rows;
- a failure does not stop the others — but inside a **sequential** group it
  aborts the rest of that group, reported as
  *"#1128 non tentata: #1145 è fallita"*;
- an issue already claimed **stays assigned to him** — he did take it — and
  goes in the "still yours" list with the reason there is no PR.

## Step 5 — Report, whenever the loop ends

Reached by `basta` / `stop` / `per ora ok`, by an exhausted list, or by a
proposal he did not answer. Do not keep pushing the queue at him.

Tables: PRs opened (issue → PR → draft/ready → verified → assignee+reviewer),
the failures with their reason, WORKFLOW ones with their phases, decisions
left to the user, finished work found on stale branches, and — the one that
makes stopping halfway clean — **what was never reached**, in queue order,
so picking it up later starts where this left off.

One comment per issue that genuinely needs its author (missing repro, request
already satisfied with the snippet, WORKFLOW phases) — never on an issue you
merely did not get to, and never where the ball is already the user's.

## The desk

Mark the rows you are on, so the dashboard is the radar instead of the feed,
and close the loop's request when it ends:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/server/notify.py --repo <owner/repo> \
  --pr <n> --working "<one line: what you are doing on this one>"

python3 ${CLAUDE_PLUGIN_ROOT}/server/notify.py --repo <owner/repo> \
  --batch 1145,1128,1059 --working "fix in parallelo, un worktree per issue"

python3 ${CLAUDE_PLUGIN_ROOT}/server/notify.py --repo <owner/repo> \
  --done run:issue-loop "3 PR aperte (#1145 #1059 #1102), 1 fallita (#1128: la fixture pg non parte), 2 non raggiunte"
```

One feed line per action, in plain words, throughout. **The semantics of
those flags** — why a batch marker is a set, why marking one member refines
it instead of collapsing it, why the loop closes ONE request however wide its
batches, and when `--failed` is the wrong word — are in
`${CLAUDE_PLUGIN_ROOT}/skills/review-desk/SKILL.md` §3, *Say which rows you
are on* and *Close the request when you are done*. That file is the protocol;
this one only uses it.
