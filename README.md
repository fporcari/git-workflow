# git-workflow

A plugin for Claude Code and Codex for working a repository's pull requests
and issues as a queue: ten shared skills and a local dashboard server with no
dependencies outside the Python standard library.

The shape of the whole thing is one idea: **fetch paints facts; an explicit
triage publishes verdicts; the model judges only what fields cannot answer.**
The merge gate, the issue cross-check and the issue shortlist are computed
while fetching, on every read — a filter is not a verdict, and a model's copy
of one is a thing to keep in sync. The PR
triage grid and chase blocks are computed in Python and published by the
server itself, on the press — the model adds, per PR, only what a field
cannot say: the one line of what it is for, a conflict read off the diff, an
analysis.

Built for GitHub today, provider-abstracted so a migration to
[Forgejo](https://forgejo.org/) only means implementing one class against the
same normalized row shape (a first REST implementation ships in the box).

The dashboard's information design comes from Giovanni's *PR Review Desk*
prototype: the queue-with-states table, the summary strip, and the detail
panel are his; this repo replaces the mocked data with live provider reads and
wires the verdicts to the skills below.

Prefer pictures? There is an [illustrated quick guide](docs/comic/README.md) —
one page per skill — also bound as a [PDF](docs/comic/git-workflow-comic.pdf).

## Install

Claude Code:

```bash
claude plugin marketplace add fporcari/git-workflow
claude plugin install git-workflow@fporcari
```

Codex: point it at the marketplace in `.agents/plugins/marketplace.json` of
this repo; the skills are invoked as `$pr-triage`, `$issue-triage`,
`$review-desk`, and so on.

## Two hosts, one plugin

The plugin lives in `plugins/git-workflow/`, with a manifest per host
(`.claude-plugin/plugin.json` and `.codex-plugin/plugin.json`) and one
`agents/openai.yaml` per skill. The skills themselves are host-agnostic: they
say `<PLUGIN_ROOT>` instead of any host variable, and `refs/runtime.md` is the
one place that resolves it and answers the other host-specific questions —
how to ask the user a question, how to launch a desk, how to title a session,
how to delegate to a background subagent, how to spawn a dedicated one. The
two Claude Code command wrappers in `commands/` are the only host adapters,
and they carry nothing but that host's tool names. The desk's headless
Analyze picks its backend with
`--agent auto|claude|codex`. `server/tests/test_packaging.py` pins the
cross-host invariants.

## Quickstart

From the checkout of the repo you want to work. Pick the line that matches
what you actually want.

**"What is on my plate?"** — read-only, no side effects, a few seconds:

```
/pr-triage
```

Every open PR you are involved in, split into five blocks by the kind of work
each needs, plus copy-pasteable messages for the people you are waiting on.
Then it offers to act on it.

**"Just deal with it."** — the loop, one PR at a time:

```
/pr-loop
```

First the moves that need no permission (merging your own fully-approved PRs,
answering a review request that named its own fix, realigning your `DIRTY`
branches), then everything else presented four lines at a time for a
go-ahead. `basta` ends it, and what was not reached is listed in queue order.

**"These three, and I have already decided."**

```
/pr-loop 1145,1128,1059 batch=3
```

Exactly those, in that order, then stop — proposed together in one answer and
executed in parallel, each fix in its own worktree.

**The same three lines on the issue side**: `/issue-triage`, `/issue-loop`,
`/issue-loop 1156,1149 batch=2`.

**"Show me, don't tell me."** — the dashboards, attached to this chat:

```
/pr-desk
```

or `/issue-desk`, or `/review-desk` for both. Each opens in the browser, reads
the provider itself and paints in seconds; its buttons hand work back to this
chat.

## What is in the box

### Read the queue — no side effects

| skill | what it does |
|---|---|
| **`pr-triage`** | Every open PR you are involved in, split into five blocks by the kind of work each needs: mergeable now, trivial action, reviews you owe, people to chase (grouped per person, ready to paste), and the calls only you can make. Each row carries number, date, author, what it is, what is to be done, and whether `pr-loop` would handle it unattended — read from the provider's fields, never by reading diffs. Hands over to `pr-loop`. |
| **`issue-triage`** | The ten most recent open issues nobody has looked at yet, ranked by impact and classified DEFECT / REQUEST / QUESTION / DOCS, with existing branches and PRs cross-checked. Its most valuable find is finished work sitting on a branch with no PR. The filter and the cross-check are the desk's; what it writes back is per issue — the impact rank, the verified type, the finding, and the date that lets the desk tell a fresh reading from an overtaken one. Takes `batch=N` and `mine`. |

### Work the queue — the loops

Both are **explicit-invocation only**, and both take the same mandate:
`1145,1128` names the working set (exactly those, in that order, then stop),
`batch=N` proposes N together instead of one, clamped to 4.

| skill | what it does |
|---|---|
| **`pr-loop`** | Drives the queue until nothing is left that only you can do. Lane A acts without asking — merges your fully-approved PRs, answers small *named* review requests, realigns `DIRTY` branches by merging the base in — and iterates until a full pass changes nothing, because its own merges change the queue. Lane B is everything else, presented as author / problem / history / proposal followed by an explicit confirmation question. Canonical home of the rule for what may run in parallel. |
| **`issue-loop`** | The same loop over the open issues: take the most urgent, analyze that one in a fresh context, propose it in four lines, and on a go-ahead assign it, fix it in a worktree and open the PR. `bugfix` is the wide mode: every eligible bug analyzed, all the plans read at once, one single go-ahead, then all the approved PRs in parallel — a bug rarely carries an architectural decision, and the PR review is still the control step. |

With `batch=N` an approved batch is never handed straight to N agents: the
loop builds a conflict graph first — same file, stacked PRs, the same issue, a
merge or a realign sharing a base — and runs the connected components in
parallel while the members of one component run in sequence. Unknown means
sequential. Failures are reported per item; nothing ever says a group
succeeded.

### Analyze exactly one

| skill | what it does |
|---|---|
| **`pr-analyze`** | One PR, read properly: one provider snapshot for the author, linked issue, history, reviews, threads and checks, plus the whole diff; independent reads start together. Returns author / problem / history / one proposal, asks for confirmation, and prepares any draft worth posting. Read-only — never posts, never pushes. Used headless by the desk's Analizza button. |
| **`issue-analyze`** | One issue, in a virgin context: verify the root cause in the actual code (DEFECT), walk the reuse ladder (REQUEST), find the proving line (QUESTION/DOCS). Returns a typed verdict with the minimal change and a verification plan. Read-only — never branches, never comments. |
| **`issue-work`** | The mandate of a session spawned for a single issue: analyze it fresh, then either fix it in a worktree and open the PR when it is one coherent change, or lay out the phases it really needs. |

### The dashboards

| skill | what it does |
|---|---|
| **`pr-desk`** | The PR queue as a dashboard (port 8399), attached to this chat. Startup and reload fetch provider facts only; the explicit `pr-triage` button computes and publishes the grid and chase blocks from those rows itself. Missing or changed triages are highlighted. Its other buttons — merge orders, `pr-analyze`, and `pr-loop` — come back to the chat as events. |
| **`issue-desk`** | The same for the open issues (port 8398): the cross-check and the shortlist computed without a model on every read, the impact ranking and the verified type from `issue-triage`, an analysis marked *da aggiornare* when its issue has moved since. Buttons for dedicated work sessions, `issue-analyze` and `issue-loop`. |
| **`review-desk`** | Launches both at once, and is the reference for how an attached chat processes desk events. Canonical home of the desk protocol: the live-row marker, the request ledger, and the exact `notify.py` flags. |

`plugins/git-workflow/server/` is the code under all three: a zero-dependency
Python stdlib server that reads the provider, prepares explicit triage work,
and serves one page.

## The dashboard

The skills launch it; you can also run it by hand:

```bash
python3 plugins/git-workflow/server/prdesk.py        # repo from the cwd's origin
python3 plugins/git-workflow/server/prdesk.py --repo owner/repo --desk issue --port 8398
```

Open http://127.0.0.1:8399. Tabs: Queue (needs a move from you), Mergeable,
Waiting, All PRs, Issues. Clicking a row opens the detail panel: state of
play, next move with the `pr-loop` autorun class, reviews, linked issues.

Options: `--repo`, `--provider github|forgejo|fixture`, `--me`, `--port`,
`--chat` (buttons hand work to the attached chat instead of running headless),
`--keep-state`, `--keep-cache`, `--no-prefetch`.

**It does not triage at startup.** It fetches the provider itself and paints
in seconds. Reload performs the same pure fetch. Pressing the triage button
reads the provider fresh, computes and publishes the whole grid on the spot,
then hands the chat the rows already downloaded rather than making the skill
re-query — and only the rows still owing a model reading (`needs_model`).
From then on the triage is durable: a PR the provider moves is re-verdicted
by the engine on every read and the grid survives a desk relaunch; only a PR
no press has ever seen is marked as never triaged.

**Choosing what the loop works.** cmd-click (shift-click for a stretch) picks
rows; ▶ then runs `pr-loop`/`issue-loop` on **exactly those, in that order,
and stops**. With more than one picked it asks the one question it cannot
guess — one at a time, or all of them in parallel worktrees — because that is
the difference between thinking about them and having already decided. The
same mandate is typed directly at the skill: `/pr-loop 1145,1128 batch=2`.

Acting belongs to the skills, which log every action on the PR itself.

## Verdicts

`plugins/git-workflow/server/verdicts.py` ports section 7 of the pr-triage skill
— the closed verdict vocabulary (`merge it`, `answer the review`, `realign
with the base`, `waiting on <login>`, …). It runs only on an explicit
triage, publishes what it computed, and is restricted to what the fields can
honestly answer: anything that would need a diff read is reported as `asks`
and left to `pr-loop`. The single fact a model hands back to it is
`conflict_kind` — mechanical or substantive — which is what turns a `DIRTY`
row of your own into an unattended realign. The `autorun` column mirrors what `pr-loop` does unattended (A1
merge, A3 realign) versus what it brings to you for a go-ahead.

## Providers

The server, the verdict engine and the UI speak one normalized row shape
(documented in `plugins/git-workflow/server/providers/base.py`). Providers
translate a hosting
service into it:

- **github** (default) — shells out to the authenticated `gh` CLI, reusing the
  exact GraphQL documents in `plugins/git-workflow/server/gql/`.
- **forgejo** — REST against the Forgejo/Gitea API v1; set `FORGEJO_URL` and
  `FORGEJO_TOKEN`. Written against the published API, not yet exercised
  against a live Forgejo instance: expect to adjust field mappings when the
  migration starts. Known gap: the API does not expose review-thread
  resolution, so `unresolved` is always 0 there.
- **fixture** — a recorded payload replayed with no network. What the test
  suite runs on.

Migrating the *skills* to Forgejo is a separate, later step: they currently
speak `gh` directly. The provider layer is where their data reads will land.

## Tests

```bash
plugins/git-workflow/server/tests/run.sh
```

No network, no GitHub, no rate limit: a few seconds on the fixture provider.
The Python suite covers the row contract, verdict engine, merge gate,
five-block partition, issue cross-check, cache and cross-host packaging
invariants (`test_packaging.py`). The UI checks drive the **real**
`static/index.html` against a **real** desk process through a small DOM shim,
so it is the page's own render path that runs.
`plugins/git-workflow/server/tests/README.md` says what each file is for.
