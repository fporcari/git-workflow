---
name: issue-triage
description: >-
  Show a read-only shortlist of recent open issues not yet analyzed, ranked by
  impact and cross-checked against branches and PRs, then optionally hand the
  selected batch to issue-loop. Explicit invocation only.
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

1. **rank the shortlist by impact** — read the body, not the label
   (1. evidence of real damage; 2. blocks someone else; 3. the rest;
   4. DOCS last). You write that rank as `impact` per issue, and the desk
   reorders the shortlist by it;
2. for any issue whose `cross.note` says *lavoro fermo* (a branch, no PR, no
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
same two judgement steps the desk path leaves you — rank by impact (1 above),
and read the history of every numbered branch (2 above) — plus the
classification the desk's `type` would have given you: DEFECT / REQUEST /
QUESTION / DOCS.

## Step 2 — The shortlist

One table, impact order: `# · date · author · type · title · assignee ·
existing branch/PR · one-line note`. Below it, in prose: the finished-work
finds, the dead branches worth pruning, and which issues the next batch
would pick up.

Export to the review desk state
(`~/.local/state/git-workflow/<owner>__<repo>.json`, preserve other keys).
**Never write `shortlist`**: the desk computes that filter itself, on every
read. You write one entry per issue you read:

```json
{"issues": {"1156": {"type": "DEFECT",
                      "impact": 1,
                      "finding": "<una riga, in italiano>",
                      "at": "<ISO timestamp, now>",
                      "phase": null, "size": null}}}
```

`impact` is the rank you just gave it, 1 = first — the desk reorders the
shortlist by it and says so. `type` overrides the desk's guess from the
labels, which is what a label-less issue needs. `at` is what makes the entry
honest: the desk compares it with the issue's own last activity and marks the
analysis *da aggiornare* when the issue has moved since. issue-analyze fills
`phase` and `size` later.

**Triggered from the detached desk**: run through Step 2 and the requested
per-issue entries, return the required structured JSON, and skip the Step 3
handover question — the user drives from the dashboard.

## Step 3 — Handover

One question only: work the batch now with `issue-loop`, and which issues to
leave alone? On a yes, invoke `issue-loop` in the same session with the batch
and the exclusions. On a no, stop — the shortlist was the deliverable.
