---
name: issue-nightwork
description: >-
  Prepare tonight the issue analyses the desk would owe tomorrow, read-only.
  Rank the shortlist (open issues nobody holds, cited by no open PR, never
  commented by the user), then one issue-analyze job per shortlisted issue
  without a current analysis, so the next desk opens with the issues the user
  missed already read. PRs are pr-nightwork's.
disable-model-invocation: true
---

# Issue nightwork

Read `<PLUGIN_ROOT>/refs/runtime.md` first.

It does tonight what the desk would do when it opens, and nothing else: it is
the same preparation (`server/preparation.py`).
The shortlist is the desk's own filter, computed without a model: the open
issues nobody is assigned to, that no open PR cites, and that the user never
commented — the ones he missed. One issue-triage pass ranks them; then each
gets the analysis the desk's preparation starts, with the same read-only
profile (`ANALYZE` in runtime.md → *Model policy*), unless the one it has is
newer than the issue's last activity. Nothing is branched, posted, assigned
or commented.

## Run

From the repository's checkout, or with the same `--repo` / `--org` /
`--folder` flags a desk scope takes, start it as a background command of the
host — Claude Code: Bash with `run_in_background`, which reports when it
exits; Codex: a persistent command session:

```sh
caffeinate -i python3 <PLUGIN_ROOT>/server/nightwork.py --kind issue --agent <claude|codex> \
    >> "${TMPDIR:-/tmp}/git-workflow-issue-nightwork.log" 2>&1; \
    tail -n 3 "${TMPDIR:-/tmp}/git-workflow-issue-nightwork.log"
```

`caffeinate -i` keeps a Mac awake while it works; drop it where the command
does not exist. `--parallel N` sets how many analyses run at once (default 4).

Tell the user in one line that it is running, that the machine must stay on
until it ends, and that a desk opened meanwhile shows the issues moving into
their steps — to close, for Claude, to decide — as they land. Then wait for
the exit; do not poll the log.

## Report

The last lines of the log are the outcome, one per repository:
`<repo> issue-nightwork <done|failed>: <report>` — how many issues were
analyzed, whether the shortlist was ranked, and each failure with its reason.
Say it in Italian and stop. The same report is saved under
`runs.issue-nightwork` in the desk state and as a line of the desk's feed.

A failed ranking does not stop the analyses: they follow the shortlist's
date order instead. A run started while another issue preparation is alive
on the same repository — this command or a desk opening — refuses to start
and says so; a desk opened while this one runs shows it instead of doubling
it.
