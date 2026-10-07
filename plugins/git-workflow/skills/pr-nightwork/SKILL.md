---
name: pr-nightwork
description: >-
  Prepare tonight the PR analyses the desk would owe tomorrow, read-only. One
  pr-analyze job per PR that asks for a judgment and whose analysis is missing
  or stale, a few at a time, plus the conflict readings owed on the user's own
  PRs, so the next desk opens with them already done. Issues are
  issue-nightwork's.
disable-model-invocation: true
---

# PR nightwork

Read `<PLUGIN_ROOT>/refs/runtime.md` first.

It does tonight what the desk would do when it opens, and nothing else: it is
the same preparation (`server/preparation.py`), with the same read-only
profile (`ANALYZE` in runtime.md → *Model policy*), saved where the desk reads
it and keyed to the PR as it was read. Each analysis carries a verdict —
approvable, to reject with its motivation, doubtful with its hunk — that the
desk, which only triages, shows in the PR's open row the next morning, with
its keys; a PR that moves before morning is the only one it reads again. Nothing is posted, approved, pushed or
commented.

## Run

From the repository's checkout, or with the same `--repo` / `--org` /
`--folder` flags a desk scope takes, start it as a background command of the
host — Claude Code: Bash with `run_in_background`, which reports when it
exits; Codex: a persistent command session:

```sh
caffeinate -i python3 <PLUGIN_ROOT>/server/nightwork.py --kind pr --agent <claude|codex> \
    >> "${TMPDIR:-/tmp}/git-workflow-pr-nightwork.log" 2>&1; \
    tail -n 3 "${TMPDIR:-/tmp}/git-workflow-pr-nightwork.log"
```

`caffeinate -i` keeps a Mac awake while it works; drop it where the command
does not exist. `--parallel N` sets how many analyses run at once (default 4).

Tell the user in one line that it is running, that the machine must stay on
until it ends, and that a desk opened meanwhile shows the rows moving into
their steps as they land. Then wait for the exit; do not poll the log.

## Report

The last lines of the log are the outcome, one per repository:
`<repo> pr-nightwork <done|failed>: <report>` — how many PRs were analyzed,
whether the conflict readings landed, and each failure with its reason. Say
it in Italian and stop. The same report is saved under `runs.pr-nightwork` in
the desk state and as a line of the desk's feed.

A failure costs only its own PR: the others are analyzed anyway, and the
desk shows the reason in the missing one's open row, and the next nightwork
tries it again. A run started while another PR preparation is alive
on the same repository — this command or a desk opening — refuses to start
and says so; a desk opened while this one runs shows it instead of doubling
it. The desk says when the last preparation landed: "preparata stanotte alle
23:10".
