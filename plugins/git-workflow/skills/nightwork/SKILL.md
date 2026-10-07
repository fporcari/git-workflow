---
name: nightwork
description: >-
  Prepare tonight, read-only, the analyses the desk would owe tomorrow — the
  PRs, the issues or both, asked with two checks — then send one notification
  saying what waits for the user and open the desk on it. The PR side analyzes
  every PR asking a judgment whose analysis is missing or stale, plus the
  conflict readings owed on the user's own PRs; the issue side ranks the
  shortlist and analyzes it, at most ten issues per repository.
disable-model-invocation: true
---

# Nightwork

Read `<PLUGIN_ROOT>/refs/runtime.md` first.

It does tonight what the desk would do when it opens, and nothing else: it is
the same preparation (`server/preparation.py`), with the same read-only
profile (`ANALYZE` in runtime.md → *Model policy*), saved where the desk reads
it and keyed to what was read, so a PR or an issue that moves before morning
is the only one read again. Nothing is posted, approved, pushed, branched,
assigned or commented.

- **PR**: one `pr-analyze` job per PR that asks for the user's judgment and
  whose analysis is missing or stale, plus the conflict readings owed on the
  user's own `DIRTY` PRs. Each analysis carries the verdict the wizard sorts
  by — approvable, to reject with its motivation, doubtful with its hunk; on
  the user's own PRs, a fix Claude can do or a choice to make.
- **Issue**: the shortlist is the desk's own filter, computed without a
  model — open issues nobody holds, cited by no open PR, never commented by
  the user, at most ten per repository. One issue-triage pass ranks them,
  then each gets an analysis unless the one it has is newer than the issue's
  last activity.

## Ask what to prepare

When the invocation text says `pr`, `issue`, or both (`tutto`, `entrambi`,
`all`), take it. Otherwise ask once, one multi-select question with two
checks (`runtime.md` → *Questions*), header `Nightwork`: **PR** (the PRs that
wait for your review, and your own) and **Issue** (the issues nobody holds,
at most ten per repository).

Nothing picked: say that nothing started, and stop.

## Run

From the repository's checkout, or with the same `--repo` / `--org` /
`--folder` flags a desk scope takes, start it as a background command of the
host — Claude Code: Bash with `run_in_background`, which reports when it
exits; Codex: a persistent command session. One `--kind` per choice; with
both, the PRs run first:

```sh
LOG="${TMPDIR:-/tmp}/git-workflow-nightwork.log"; \
caffeinate -i python3 <PLUGIN_ROOT>/server/nightwork.py --kind pr --kind issue \
    --agent <claude|codex> > "$LOG" 2>&1; \
grep -E '^(pronta |[^ ]+ (pr|issue)-nightwork )' "$LOG"
```

`caffeinate -i` keeps a Mac awake while it works; drop it where the command
does not exist. `--parallel N` sets how many analyses run at once (default 4).

Tell the user in one line that it is running, and that the machine and this
chat must stay open until it ends: the notification and the desk come from
here. A desk opened meanwhile shows the rows moving into their steps as they
land. Then wait for the exit; do not poll the log.

## Report, notify, open the desk

The command prints, per kind, one line per repository —
`<repo> <kind>-nightwork <done|failed>: <report>`, how many items were
analyzed and each failure with its reason — then `pronta <kind>: <counts>`,
what the wizard now holds for the user across the scope (`review: 3
approvabili, 1 da respingere · tue: 2 da mergiare`, or `niente che aspetti
te`).

1. Say the outcome in Italian, in a few lines: the counts, then any failure.
2. Notify once, as `runtime.md` → *Telling the user* says — the user is
   away, this is the one interruption. One line: `Nightwork pronta · <pronta
   pr> · <pronta issue> · il desk è aperto`, leaving out a kind that did not
   run.
3. Open the desk in this same chat, attached: run the `git-desk` skill with
   the same scope flags, and follow it from its *Launch*. Its boot finds the analyses
   current and buys nothing again but what moved since; every click then runs
   here, where the user picks up in the morning.

Open the desk also after a failed run: a PR whose analysis failed waits
among the doubts with its reason, and the desk's own preparation retries it.

The same reports are saved under `runs.pr-nightwork` and
`runs.issue-nightwork` in the desk state, and as lines of the desk's feed;
the desk says when the last preparation landed ("preparata stanotte alle
23:10"). A run started while another preparation of the same kind is alive
on the same repository — this command or a desk opening — refuses that kind
and says so; a desk opened while this one runs shows it instead of doubling
it. A failed issue ranking does not stop the analyses: they follow the
shortlist's date order instead.
