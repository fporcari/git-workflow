---
description: Show the issue situa — the 10 most recent open issues not yet analyzed, ranked by impact, classified by type, with existing branches and PRs cross-checked — read-only, exported to the review desk. Working them (analysis agents, selection, branches, PRs) is /issue-run, which this command offers at the end. Explicit invocation ONLY. Re-run to advance to the next batch.
argument-hint: optional — "batch=N" to change the 10, "mine" to restrict to issues assigned to me
allowed-tools: Bash, Read, Grep, Glob, AskUserQuestion, ToolSearch, mcp__ccd_session_mgmt__set_session_title
---

# Issue triage — the situa — $ARGUMENTS

Read-only: no branches, no comments, no PRs, no assignments. Acting on the
batch is `/issue-run`.

## Step 0 — Scope

- Date the session first: `date +%F`, `gh repo view --json name --jq .name`,
  then `set_session_title` with `Issue triage · <repo> · <YYYY-MM-DD>` (read
  the date from `date`, never from memory; leave a hand-written title alone).
- Repo: the `origin` remote. Login: `gh api user --jq .login`.
- **Scope is the whole repo, not my assigned issues** (`mine` restricts it).
- **Batch: the 10 most recent open issues I have not analyzed yet** — no
  comment of mine, no PR referencing them, no remote branch matching
  `(^|/)<n>-` (match on the number, never on prefixes: they drift).

## Step 1 — Collect, cross-check, rank, classify

```bash
ME=$(gh api user --jq .login)
gh issue list --state open --limit 300 \
    --json number,title,labels,url,author,assignees,createdAt,comments \
  | jq --arg me "$ME" '[.[] | select([.comments[].author.login] | index($me) | not)]
      | sort_by(.createdAt) | reverse | .[:10]'
git ls-remote --heads origin | sed 's|.*refs/heads/||' > /tmp/triage-branches.txt
```

Drop what already has an open PR or a branch, refill to 10 by date. **A
numbered branch means: read its history first** — two checks, opposite
verdicts: (1) is the content already on the base? (`git cherry` is not
evidence after a squash — verify a symbol or file the branch introduces);
(2) is there a CLOSED PR on it, and why was it closed? (a closed PR usually
carries a decision). What survives both — content absent, no PR ever — is
finished work nobody is reviewing: the most valuable find of the run.

Rank by impact: 1) evidence of real damage (traceback, crash, data loss);
2) blocks someone else; 3) everything else; 4) DOCS last. Read the body, not
the label. Classify each as DEFECT / REQUEST / QUESTION / DOCS.

## Step 2 — The situa

One table, impact order: `# · date · author · type · title · assignee ·
existing branch/PR · one-line note`. Below it, in prose: the finished-work
finds, the dead branches worth pruning, and which issues the next batch
would pick up.

Export to the review desk state
(`~/.local/state/git-workflow/<owner>__<repo>.json`, preserve other keys):
each batch issue under `issues.<n>` with `{type, finding: <the one-line
note>, phase: null, size: null}` — issue-analyze fills phase and size later.

## Step 3 — Handover

One question only: work the batch now with `/issue-run`, and which issues to
leave alone? On a yes, invoke `issue-run` in the same session with the batch
and the exclusions. On a no, stop — the situa was the deliverable.
