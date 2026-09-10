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

> Push to `origin`. Open the PR with `gw pr create --head <branch>` from the
> worktree: `gw` reads the service and the repo from the origin, so it never
> opens a PR anywhere else (GitHub and Forgejo alike). Do NOT fork, do NOT
> add remotes, do NOT pass `--repo` to point elsewhere. If a push is
> rejected for permissions, STOP and report.

Base branch = the repo's default branch, `gw repo default-branch` — the
default of `gw pr create` — never the one the harness reports.

PR discipline, same for every type and size: **draft** when a decision is
open (posted on the ISSUE, linked from the body), **ready** when complete and
verified — never claim a verification that was not run. Body sections:
Problem/Root cause or Motivation, Change, Verification, Related issue with
`Fixes #<n>` **in the PR body**: `gw pr create` reads the PR back and exits 1
when the service linked nothing (`closes` is in its output). `--assignee @me`;
`--reviewer` resolved from CODEOWNERS on the touched paths — `gw` refuses a
login that is not in `gw collaborators`, because the services drop such a
request without an error — and `req` in the output confirms it landed.

**Every PR an agent opens carries the label `needs-verification`.** It opens
under the user's login, but the hands were not his: the label is what makes
the PR desk read it as a subordinate's work — `verify it` before any merge,
also when there is nobody else in the repo to ask — instead of as his own.
Create the label first (idempotent), then pass it:

```bash
gw label ensure needs-verification --color 5319E7 \
  --description "independent verification required before merging"
gw pr create --title "<title>" --body-file <f> --head <branch> [--draft] \
  --assignee @me [--reviewer <login>] --label needs-verification
```

`gw` has no verb that rewrites a PR body: a review is answered with
`gw pr comment <n> --body-file <f>`, never by editing the description.

The label names the review regime, and it applies only where nobody else is
going to read the PR — no requested reviewer, no review by another person.
With somebody else in play that human review IS the verification: the label
regime does not fire, and no report of the user's is published on top of it.

The verifying pass closes it by REMOVING the label and recording the tested
SHA, in one verb:

```bash
gw pr verified <n> --sha "$(git rev-parse HEAD)"
```

The SHA (`prs.<n>.verified_sha` in the desk state) is what keeps the proof
pinned: the verdict engine compares it with the head, so a push past it asks
for the run again, exactly as it voids an approval. `FAIL` and `BLOCKED`
leave the label where it is. A report is published on GitHub only when the
user explicitly asks for one — on a repo he works alone in, posting a review
under the login that authored the PR is signing his own work.

PRs verified under the old regime still count: a `COMMENTED` review of his on
the current head whose final line is `Verification result: PASS`.

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
