# git-workflow

A Claude Code plugin for working a repository's pull requests and issues as a
queue: three battle-tested skills plus a local, read-only dashboard server.
Built for GitHub today, provider-abstracted so a migration to
[Forgejo](https://forgejo.org/) only means implementing one class against the
same normalized row shape (a first REST implementation ships in the box).

The dashboard's information design comes from Giovanni's *PR Review Desk*
prototype: the queue-with-states table, the summary strip, and the detail
panel are his; this repo replaces the mocked data with live provider reads and
wires the verdicts to the skills below.

## What is in the box

| piece | kind | what it does |
|---|---|---|
| `pr-triage` | skill | read-only triage of every open PR the user is involved in — five blocks by the kind of work each needs, verdicts read from the provider fields, chase messages ready to paste |
| `pr-loop` | skill | loops the queue: merges the user's fully-approved PRs, answers small named review requests, realigns DIRTY branches, then presents the rest for a go-ahead — one at a time, or `batch=N` proposed together and executed in parallel worktrees |
| `issue-triage` | command | triages the most recent open issues, ranks by impact, analyzes read-only, lets the user pick which get a branch and a PR |
| `issue-loop` | skill | the same loop over the open issues: analyze one in a fresh agent, propose it in four lines, and on a go-ahead fix it in a worktree and open the PR |
| `review-desk` | skill | starts the dashboard server below |
| `server/` | code | zero-dependency Python stdlib server rendering the queue and the issues with the pr-triage verdicts computed from the fields |

## Install

```bash
claude plugin marketplace add fporcari/git-workflow
claude plugin install git-workflow@fporcari
```

## The dashboard

```bash
python3 server/prdesk.py                      # repo from the cwd's origin
python3 server/prdesk.py --repo owner/repo --port 8399
```

Open http://127.0.0.1:8399. Tabs: Queue (needs a move from you), Mergeable,
Waiting, All PRs, Issues. Clicking a row opens the detail panel: state of
play, next move with the `/pr-loop` autorun class, reviews, linked issues.

Read-only by design: the dashboard renders state; acting on it belongs to the
skills, which log every action they take on the PR itself.

**Choosing what the loop works.** cmd-click (shift-click for a stretch) picks
rows; ▶ then runs `pr-loop`/`issue-loop` on **exactly those, in that order,
and stops**. With more than one picked it asks the one question it cannot
guess — one at a time, or all of them in parallel worktrees — because that is
the difference between thinking about them and having already decided. The
same mandate is typed directly at the skill: `/pr-loop 1145,1128 batch=2`.

## Providers

The server, the verdict engine and the UI speak one normalized row shape
(documented in `server/providers/base.py`). Providers translate a hosting
service into it:

- **github** (default) — shells out to the authenticated `gh` CLI, reusing the
  exact GraphQL document of the pr-triage skill.
- **forgejo** — REST against the Forgejo/Gitea API v1; set `FORGEJO_URL` and
  `FORGEJO_TOKEN`. Written against the published API, not yet exercised
  against a live Forgejo instance: expect to adjust field mappings when the
  migration starts. Known gap: the API does not expose review-thread
  resolution, so `unresolved` is always 0 there.

Migrating the *skills* to Forgejo is a separate, later step: they currently
speak `gh` directly. The provider layer is where their data reads will land.

## Verdicts

`server/verdicts.py` ports section 7 of the pr-triage skill — the closed
verdict vocabulary (`merge it`, `answer the review`, `realign with the base`,
`waiting on <login>`, …) — restricted to what the fields can honestly answer:
anything that would need a diff read is reported as `asks` and left to
`/pr-loop`. The `autorun` column mirrors what `/pr-loop` does unattended (A1
merge, A3 realign) versus what it brings to the user one PR at a time.
