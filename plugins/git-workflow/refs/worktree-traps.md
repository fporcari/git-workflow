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
