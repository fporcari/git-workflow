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

It does tonight what the desk would ask for in the morning, and nothing else.
The analyses are the ones the desk's Analizza button starts, with the same
read-only profile (`ANALYZE` in runtime.md → *Model policy*), saved where the
desk reads them and keyed to the PR as it was read: a PR that moves before
morning shows its analysis as stale, and the desk asks for it again. Nothing
is posted, approved, pushed or commented.

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
until it ends, and that a desk opened meanwhile shows the analyses as they
land. Then wait for the exit; do not poll the log.

## Report

The last lines of the log are the outcome, one per repository:
`<repo> pr-nightwork <done|failed>: <report>` — how many PRs were analyzed,
whether the conflict readings landed, and each failure with its reason. Say
it in Italian and stop. The same report is saved under `runs.pr-nightwork` in
the desk state and as a line of the desk's feed.

A failure costs only its own PR: the others are analyzed anyway, and the
desk asks again for the missing one. A run started while another PR
nightwork is alive on the same repository refuses to start and says so.
