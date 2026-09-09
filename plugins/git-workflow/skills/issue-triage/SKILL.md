---
name: issue-triage
description: >-
  Show a read-only shortlist of recent open issues not yet analyzed, each with
  an urgency band and its reason, the issues it depends on, and a proposed
  resolution order, cross-checked against branches and PRs, then optionally
  hand the selected batch to issue-loop. Explicit invocation only.
---

# Issue triage — the shortlist

Read `batch=N`, `mine` and any scope note from the user's invocation text.
Resolve `<PLUGIN_ROOT>` and host-specific questions from
`<PLUGIN_ROOT>/refs/runtime.md` before running commands.

Read-only: no branches, no comments, no PRs, no assignments. Acting on the
batch is `issue-loop`.

## Execution mode

For a direct conversational invocation, Step 1 and Step 2 run in a background
subagent so the supervising session remains available. In **detached desk
mode**, this process already is the isolated one-shot worker: do not delegate
again, do not write desk state, work only the issues in the rows JSON, and
return the structured result requested by the launch prompt.

## Step 0 — Scope

- Date the session first: `date +%F`, `gh repo view --json name --jq .name`,
  then set `Issue triage · <repo> · <YYYY-MM-DD>` as the session title when
  the host exposes that capability (read the date from `date`, never from
  memory; leave a hand-written title alone).
- Repo: the `origin` remote. Login: `gh api user --jq .login`.
- **Scope is the whole repo, not my assigned issues** (`mine` restricts it).
- **Batch: the 10 most recent open issues I have not analyzed yet** — no
  comment of mine, no PR referencing them, no remote branch matching
  `(^|/)<n>-` (match on the number, never on prefixes: they drift).

## Step 1 — Collect, cross-check, rank, classify

**If a desk handed you a `rows` path**, Step 1 is already done for you.
Every issue row carries `type` (from labels and title) and a `cross` block:

    branches     the remote branches naming this issue (matched on the
                 NUMBER, never on a prefix)
    open_prs     the PRs that close it, from the queue's own links
    seen_by_me   whether the user has already commented on it
    mine         whether it is assigned to him
    note         what that combination means, in one line

and `shortlist` holds the ten the desk already filtered down to: never
looked at, nobody on them, no PR, newest first. The desk recomputes that
filter on every read, so it is never stale and there is nothing to publish:
it is ~14k tokens of issue rows turned into ten you actually have to read.

**Do not recompute it, and do not copy it back.** What is left for you is the
part that needs judgement and cannot be looked up:

1. **urgency, per issue** — read the body, not the label, and place it in
   one of four bands: 1 evidence of real damage (traceback, crash, data
   loss); 2 blocks someone else; 3 the rest; 4 DOCS. Write the band as
   `urgency` and the reason as `why`, one line: "urgente perché…" is a
   different sentence from the finding, and the user reads it to overrule
   you;
2. **dependencies between the ten** — which issue presupposes another
   (needs its fix to be testable, or to make sense), which one another would
   close or shrink (a duplicate, a superset, a symptom of the same cause),
   which DOCS explains what three DEFECTs are about. Write them as `after`:
   the numbers this issue is better worked after. Same cause → the
   shallower one goes `after` the deeper; duplicate → `after` the one you
   keep, and say so in `finding`. No dependency is the normal case: an
   empty list, never an invented one;
3. **the resolution order** — the sequence you would work them in. Start
   from the urgency bands; then move an issue after everything in its
   `after`, and pull forward the one that unlocks the most others, even a
   band-4 DOCS. Write that position as `impact` (1 = first): the desk
   reorders the shortlist by it, so `impact` IS the proposed order, and
   the bands stay visible next to it as `urgency`;
4. for any issue whose `cross.note` says *lavoro fermo* (a branch, no PR, no
   assignee), answer the two questions only a reading can answer: is the
   content already on the base (`git cherry` is not evidence after a squash
   — verify a symbol or file the branch introduces), and was there a CLOSED
   PR on it, and why was it closed. What survives both is finished work
   nobody is reviewing: the most valuable find of the run.

Otherwise collect it yourself:

```bash
ME=$(gh api user --jq .login)
gh issue list --state open --limit 300 \
    --json number,title,labels,url,author,assignees,createdAt,comments \
  | jq --arg me "$ME" '[.[] | select([.comments[].author.login] | index($me) | not)]
      | sort_by(.createdAt) | reverse | .[:10]'
git ls-remote --heads origin | sed 's|.*refs/heads/||' > /tmp/triage-branches.txt
```

Drop what already has an open PR or a branch, refill to 10 by date. Then the
same judgement steps the desk path leaves you — urgency, dependencies and
resolution order (1–3 above), and the history of every numbered branch (4
above) — plus the classification the desk's `type` would have given you:
DEFECT / REQUEST / QUESTION / DOCS.

## Step 2 — The shortlist

One table, in resolution order: `# · urgency · after · date · author ·
type · title · assignee · existing branch/PR · one-line note`. Below it, in
prose: the sequence and why it departs from pure urgency where it does
("#1128 prima di #1145 perché…"), the finished-work finds, the dead branches
worth pruning, and which issues the next batch would pick up.

Export to the review desk state
(`~/.local/state/git-workflow/<owner>__<repo>.json`, preserve other keys).
**Never write `shortlist`**: the desk computes that filter itself, on every
read. You write one entry per issue you read:

```json
{"issues": {"1156": {"type": "DEFECT",
                      "impact": 1,
                      "urgency": 1,
                      "why": "<una riga: perché è urgente>",
                      "after": [],
                      "finding": "<una riga, in italiano>",
                      "at": "<ISO timestamp, now>",
                      "phase": null, "size": null}}}
```

`impact` is the position in the resolution order you just built, 1 = first
— the desk reorders the shortlist by it and says so. `urgency` is the band
(1–4) and `why` its one-line reason; `after` the issue numbers this one
follows, empty when it follows none. `type` overrides the desk's guess from
the labels, which is what a label-less issue needs. `at` is what makes the
entry honest: the desk compares it with the issue's own last activity and
marks the analysis *da aggiornare* when the issue has moved since.
issue-analyze fills `phase` and `size` later.

**Triggered from the detached desk**: run through Step 2 and the requested
per-issue entries, return the required structured JSON, and skip the Step 3
handover question — the user drives from the dashboard.

## Step 3 — Handover

One question only: work the batch now with `issue-loop`, and which issues to
leave alone? On a yes, invoke `issue-loop` in the same session with the batch
and the exclusions. On a no, stop — the shortlist was the deliverable.
