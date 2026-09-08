# Working in a worktree, with several agents at once

The protocol for any agent that touches a working tree — `pr-loop`,
`issue-loop`, `issue-work`. Every entry bites harder with several agents
running in parallel, which is exactly when it is used.

One worktree per item (`isolation: "worktree"` where the host offers it,
`git worktree add <scratch>/wt-<n> origin/<base>` otherwise), and:

- `gnr.*` imports resolve to the **main checkout** unless
  `PYTHONPATH=<worktree>/gnrpy`; assert `module.__file__` is inside the
  worktree before believing any result;
- anything under `resources/`/`projects/` needs a scratch `GENRO_GNRFOLDER`
  whose directory is named `gnr` — **a distinct one per agent**. One shared
  scratch folder across concurrent agents is a race that only shows up at
  runtime;
- **never `git stash`**: worktrees share one stash stack. Use a patch file;
- full-suite counts from concurrent agents are worthless under `tests/sql/`
  (the pg fixture pkills sibling postgres), so gate on
  `pytest gnrpy/tests/ -q --ignore=gnrpy/tests/sql`. With several agents even
  that is noisy: each agent reports **both** its narrowest test and the
  suite, and **the gate is the narrow one**;
- remove the worktree when done, and never leave a branch behind that no PR
  points at.

## Pushing and opening the PR

Hand every fix agent these rules verbatim, because a fresh agent with a
worktree and a token is one wrong default away from opening a PR on somebody
else's repo:

> Push to `origin`. Open the PR with `gh pr create --repo <owner>/<repo>
> --base <base>`. Do NOT fork, do NOT add remotes, do NOT open a PR against
> any other repo. If a push is rejected for permissions, STOP and report.

Base branch = the repo's default branch read with
`gh repo view --json defaultBranchRef`, never the one the harness reports.

PR discipline, same for every type and size: **draft** when a decision is
open (posted on the ISSUE, linked from the body), **ready** when complete and
verified — never claim a verification that was not run. Body sections:
Problem/Root cause or Motivation, Change, Verification, Related issue with
`Fixes #<n>` **in the PR body**, then verify `closingIssuesReferences` is not
empty. `--assignee` the author; `--reviewer` resolved from CODEOWNERS on the
touched paths and checked against
`gh api repos/<owner>/<repo>/collaborators` — a login with no access is
dropped without an error — then confirm `reviewRequests` landed.

**Every PR an agent opens carries the label `needs-verification`.** It opens
under the user's login, but the hands were not his: the label is what makes
the PR desk read it as a subordinate's work — `verify it` before any merge,
also when there is nobody else in the repo to ask — instead of as his own.
Create the label first (idempotent), then pass it:

```bash
gh label create needs-verification --repo <owner>/<repo> --force \
  -c 5319E7 -d "independent verification required before merging"
gh pr create ... --label needs-verification
```

Never remove it: it names the review regime, and the merge closes it. The
proof is the latest verification report by the user on the current head,
posted as a `COMMENTED` review with `commit_id` set to the tested SHA. Its final
line must be `Verification result: PASS`; `FAIL` and `BLOCKED` keep the gate
closed. An ordinary comment is not a verification report.

## Serving the instance from a worktree

The instance already running on the machine serves the **main checkout** —
the base, not the PR. A browser check against it passes for the wrong reason
or fails for the wrong reason, and the user cannot tell which. So a UI step
never reuses a running instance: the verifying agent serves its own.

- **Ask which instance first.** The repo's code is one thing; the instance it
  runs as (site, database, configuration) is another, and the user has more
  than one. Before serving, ask him which instance to start the check on,
  offering the ones the repo's launch recipe lists. Never pick one for him.
- **The launch recipe is the repo's, not the plugin's.** Look, in order, for a
  project skill named `run` or `ui-test` in the repo, then for a configuration
  in `.claude/launch.json`. Its contract: given the worktree path, the chosen
  instance and a free port, it starts the app serving **that worktree's
  code** and prints the URL. No recipe → the UI steps are `BLOCKED`, with the
  line "no launch recipe in the repo", never skipped in silence.
- Start it with `cwd` inside the worktree, `PYTHONPATH` into the worktree, a
  scratch `GENRO_GNRFOLDER` of its own, on a port probed free — never the
  port of the instance he is using.
- **Prove what is being served before the first click**: the module's
  `__file__` under the worktree, or a string the PR itself introduced, read
  from the served page. A green check on unproven code is worth nothing.
- Point the browser at that URL only (runtime.md, "Opening a URL"). Kill the
  process when the check ends, and say in the report which SHA and port were
  served.
