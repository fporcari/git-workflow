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
`$git-desk`, and so on.

## Two hosts, one plugin

The plugin lives in `plugins/git-workflow/`, with a manifest per host
(`.claude-plugin/plugin.json` and `.codex-plugin/plugin.json`) and one
`agents/openai.yaml` per skill. The skills themselves are host-agnostic: they
say `<PLUGIN_ROOT>` instead of any host variable, and `refs/runtime.md` is the
one place that resolves it and answers the other host-specific questions —
how to ask the user a question, how to launch a detached desk, how to title a
session, how to delegate to a background subagent, how to spawn a dedicated one. The
two Claude Code wrappers in `commands/` are thin by design: they load a skill
and declare that host's tool names, nothing else. Explicit desk actions pick
their ephemeral backend with `--agent auto|claude|codex`. The launching
conversation stays attached by default — every click except triage is
executed there, as the command it stands for — and detaches only on request.
Read-only PR analysis can select a model and effort through the portable
`GIT_WORKFLOW_ANALYZE_*` variables, or their host-specific `CODEX` / `CLAUDE`
variants. `server/tests/test_packaging.py` pins the cross-host invariants.

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

Exactly those, in that order, then stop. They are analysed by background
agents, at most four at a time, while you do something else; you are
interrupted twice — once with a digest of the proposals (ready, yours by hand,
nothing to do), answered in one go, and once with the outcome, each fix in its
own worktree. `refs/batch.md` is the protocol.

**The same three lines on the issue side**: `/issue-triage`, `/issue-loop`,
`/issue-loop 1156,1149 batch=2`.

**A PR an agent opened is a subordinate's PR.** `issue-loop` and `issue-work`
label every PR they open `needs-verification`: it stands under your login, but
the hands were not yours, so the desk reads it as work to check, not as your
own — `verify it` comes before any merge, in a repository where there is
nobody else to ask. Where somebody else IS asked, that review is the
verification and the regime does not fire at all. `pr-analyze` answers with a
numbered verification plan, `pr-loop` shows the plan as the checklist you
approve, asks which instance to run the browser checks on, and hands it to a
fresh agent that serves the PR from its own worktree — never your running
instance. The pass closes by removing the label and recording the tested SHA
on the desk, not by publishing a review you would have signed yourself; a
push past that SHA asks for the run again, and the merge is then your
decision. The
launch recipe is the repo's own (a `run`/`ui-test` project skill or
`.claude/launch.json`); without one the UI steps come back blocked, not faked.

**"Show me, don't tell me."** — the detached dashboard:

```
/git-desk
```

One page, with Pull request, Issue and Filoni as tabs. It opens in the browser, reads
provider/cache JSON itself and paints in seconds. Opening or polling it spends
no model tokens; an explicit action button starts one ephemeral Codex or
Claude process and records its progress and result in a job JSON. While it
runs, the desk shows elapsed time, current phase and sanitized tool activity;
opening the progress view does not attach the primary conversation. The
launching chat is titled `Git desk · owner/repo · 2026-09-09 14:32` when the
desk opens and gets a ` · closed` suffix once the desk is gone — the server
registers itself at boot, and the chat's listener ends by itself when the
desk has stopped — so the session list tells the two apart.

Attached clicks carry the originating desk, owning session and a unique
request ID. A second chat cannot silently take over a live desk. Each chat
executes one click at a time, and late results cannot overwrite a newer
request. After updating, restart existing desks and chat
listeners to use the new routing protocol.

## What is in the box

### Read the queue — no side effects

| skill | what it does |
|---|---|
| **`pr-triage`** | Every open PR you are involved in, split into five blocks by the kind of work each needs: mergeable now, trivial action, reviews you owe, people to chase (grouped per person, ready to paste), and the calls only you can make. Each row carries number, date, author, what it is, what is to be done, and whether `pr-loop` would handle it unattended — read from the provider's fields, never by reading diffs. Hands over to `pr-loop`. |
| **`issue-triage`** | The ten most recent open issues nobody has looked at yet, each with an urgency band and its one-line reason, the issues it is better worked after, and a proposed resolution order that honours those dependencies; classified DEFECT / REQUEST / QUESTION / DOCS, with existing branches and PRs cross-checked. Its most valuable find is finished work sitting on a branch with no PR. The filter and the cross-check are the desk's; what it writes back is per issue — the position in the order, the urgency and why, the `after` list, the verified type, the finding, and the date that lets the desk tell a fresh reading from an overtaken one. `issue-loop` takes that order as its queue and treats `after` as an edge. Takes `batch=N` and `mine`. |

### Work the queue — the loops

Both are **explicit-invocation only**, and both take the same mandate:
`1145,1128` names the working set (exactly those, in that order, then stop),
`batch=N` (N > 1) runs the analyses in background and hands them back as one
digest, clamped to 20, with at most four agents running at once.

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
| **`pr-analyze`** | One PR, read properly: a compact fresh probe first, reusing the desk's normalized row and any still-valid problem statement. When only the head changed after a review, it compares the reviewed SHA with the new head and stops before the full diff if PR-owned behaviour is unchanged. Otherwise it gathers the complete snapshot and diff once. Exact local Git objects accelerate reads without trusting the working tree. Returns author / problem / history / one proposal, asks for confirmation, and prepares any draft worth posting. Read-only — never posts, never pushes. Used headless by the desk's Analizza button. |
| **`issue-analyze`** | One issue, in a virgin context: verify the root cause in the actual code (DEFECT), walk the reuse ladder (REQUEST), find the proving line (QUESTION/DOCS). Returns a typed verdict with the minimal change and a verification plan. Read-only — never branches, never comments. |
| **`issue-work`** | The mandate of a session spawned for a single issue: analyze it fresh, then either fix it in a worktree and open the PR when it is one coherent change, or lay out the phases it really needs. |

### The dashboards

| skill | what it does |
|---|---|
| **`git-desk`** | The detached dashboard (default port 8399, a free one when that is taken), one page with three tabs. *Pull request*: the PR queue. *Issue*: the cross-check and the shortlist computed without a model on every read, only the issues still to take — unassigned or yours, and cited by no open PR (the lead line counts what it leaves out), the resolution order, urgency and dependencies from `issue-triage` shown on every row, an analysis marked *da aggiornare* when its issue has moved since. *Filoni*: PRs and the issues they close, read together. Startup, reload and polling use Python/provider JSON only; the launching chat stays attached by default and executes every click except triage, which — like every click on a detached desk — starts one ephemeral Codex or Claude process. The skill also defines the JSON/job contract. |

`plugins/git-workflow/server/` is the code under it: a zero-dependency
Python stdlib server that reads the provider, prepares explicit triage work,
and serves one page.

### `desk-band` — the desk in the chat, Claude Code only

A separate plugin of the same marketplace, a Claude Code mod (function
hooks), for the chat a desk is attached to:

```bash
claude plugin install desk-band@fporcari
```

- **a band above the prompt**, one row per desk request of this chat, tagged
  `PR` (magenta) or `ISSUE` (green): in coda, in chat ora, in background,
  **aspetta te** first and in yellow;
- **a status line** `PR ⏳1 ⏸1 · ISSUE ⏳1`: what works, what waits for you;
- **a toast** only when a loop starts waiting for you or closes;
- **a guard on a bare `vai`**: with two loops waiting for an answer it does
  not enter and asks which one; with one, the chat is told which it answers.

It reads the desk state files under `~/.local/state/git-workflow/` every four
seconds, re-reading only a file that changed; no model, no network.
`claude plugin test plugins/desk-band` runs its tests.

## The dashboard

The skills launch it; you can also run it by hand:

```bash
python3 plugins/git-workflow/server/prdesk.py        # repo from the cwd's origin
python3 plugins/git-workflow/server/prdesk.py --repo owner/repo --desk issue
python3 plugins/git-workflow/server/prdesk.py --org erpy   # every repo of an owner
cd ~/Development/erpy-org && python3 …/prdesk.py           # a folder of clones
```

**One page, three views.** Pull request, Issue and Filoni are tabs of the
same page, whichever desk you launched: the server already reads both, and
Filoni pairs every open issue with the PR that closes or cites it —
`owner/repo#n` included — grouped by who has to move, with a strip of the
people involved on top. The layout is built for a tall, narrow pane (the
Browser pane beside the chat on a portrait screen): two-line rows, the
detail under the list with a handle to move the split, filter chips instead
of a metrics row. On a wide window the detail moves beside the list. ⌘K
opens every action of the page — the picked rows, the selected row, the
views, the chase messages, the scope — and `j`/`k`, `x`, `a`, `g p`/`g i`/`g f`
work without it.

**A scope of several repositories.** `--org [host/]owner` covers every
repository of that owner with an open issue or PR (one cross-repo search: no
`read:organization` scope needed on Forgejo); a cwd that holds clones without
being one covers that folder, other owners included; `--folder DIR` and
repeated `--repo` add up. Each member keeps its own cache, state file, jobs
and click ledger — exactly the files a desk of its own would write — and the
page merges them: every row is `repo #n`, every click names its repository,
a run across repositories becomes one loop per repository, each in its own
clone. Clones are found, not configured (the cwd, its children, its parent's
children, `--clones DIR`); a member without one is read and analyzed but never
worked, and the page says so on its rows. The scope button lists the members
and their clones, and hides a member from the page without changing the scope.

Open the URL of the `desk on http://127.0.0.1:<port>` line it prints: 8399
for PRs and 8398 for issues when free, a free port the OS picks when another
repo or the sibling desk holds it, and the running server's URL when the same
desk of the same repo is already up (then the new process just exits). An
explicit `--port` is strict. A desk idle for an hour with no job running exits
on its own (`--idle-exit`). Clicking a row opens the detail panel: the next
move with the `pr-loop` autorun class first, then what the PR solves, the
state of play, reviews and linked issues.

Options: `--repo` (repeatable), `--org`, `--folder`, `--clones`,
`--provider github|forgejo|fixture`, `--me`, `--port`,
`--idle-exit`, `--agent auto|claude|codex`, `--keep-state`, `--keep-cache`,
`--no-prefetch`.

**It does not triage at startup.** It fetches the provider itself and paints
in seconds. Reload performs the same pure fetch. Pressing the triage button
reads the provider fresh, computes and publishes the whole grid on the spot,
then starts one ephemeral agent only if the freshly exported rows need model
work. `model_tasks` names only the stale analysis or conflict artifacts;
`needs_model` remains their compatibility list of PR numbers.
From then on the triage is durable: a PR the provider moves is re-verdicted
by the engine on every read and the grid survives a desk relaunch; only a PR
no press has ever seen is marked as never triaged.
Completing a loop or order asks every open tab for one fresh provider snapshot;
fact refreshes do not relaunch triage or spend model tokens.

**Choosing what the loop works.** cmd-click (shift-click for a stretch) picks
rows; ▶ then runs `pr-loop`/`issue-loop` on **exactly those, in that order,
and stops**. More than one picked is a background batch: the chat parks the
request (`running` on the row), takes the next click while its agents work,
and comes back with the digest. The same mandate is typed directly at the
skill: `/pr-loop 1145,1128 batch=2`.

Acting belongs to the skills, which log every action on the PR itself.

## Verdicts

`plugins/git-workflow/server/verdicts.py` ports section 7 of the pr-triage skill
— the closed verdict vocabulary (`merge it`, `answer the review`, `realign
with the base`, `waiting on <login>`, …). Explicit triage publishes its grid;
after that the same engine re-verdicts it on every provider read. It is restricted to what the fields can
honestly answer: anything that would need a diff read is reported as `asks`
and left to `pr-loop`. The single fact a model hands back to it is
`conflict_kind` — mechanical or substantive, keyed to the exact head/base pair — which is what turns a `DIRTY`
row of your own into an unattended realign. The `autorun` column mirrors what `pr-loop` does unattended (A1
merge, A3 realign) versus what it brings to you for a go-ahead.

## Providers

The server, the verdict engine and the UI speak one normalized row shape
(documented in `plugins/git-workflow/server/providers/base.py`). Providers
translate a hosting
service into it:

- **github** — shells out to the authenticated `gh` CLI, reusing the
  exact GraphQL documents in `plugins/git-workflow/server/gql/`.
- **forgejo** — REST against the Forgejo/Gitea API v1. Credentials like
  `gh auth login`, once for every host and harness: a macOS keychain item
  `security add-generic-password -s FORGEJO_TOKEN -a <host> -w` (account =
  the instance's host, password = a token with repository and issue
  read/write, user read); `FORGEJO_URL` and `FORGEJO_TOKEN` in the
  environment override it and are the way on other platforms. Known gap: the
  API does not expose review-thread resolution, so `unresolved` is always 0.
- **fixture** — a recorded payload replayed with no network. What the test
  suite runs on.

The provider is read from the checkout's `origin`: github.com is GitHub, the
host of `FORGEJO_URL` is Forgejo, anything else exits 2 — never a default,
because a GitHub read against a Forgejo checkout returns an empty queue, and
an empty queue reads as "nothing to do". A new service is one class with a
`hosts()` list and one entry in `PROVIDERS` (`server/providers/detect.py`).

## gw — one CLI over every provider

`plugins/git-workflow/bin/gw` (link it into PATH) is what the skills call
instead of `gh`, so a skill written once runs on GitHub and Forgejo:

```
gw whoami · repo info · repo default-branch · collaborators
gw pr list [--state] [--mine] · pr view <n> · pr reviews <n> · pr diff <n> [--name-only]
gw issue list · issue view <n>
gw issue create --title T --body-file F [--label L] [--assignee @me]
gw pr create --title T --body-file F --head BRANCH [--base B] [--draft] [--label L] [--assignee @me] [--reviewer L]
gw pr edit <n> --add-reviewer L · pr comment <n> --body-file F · issue edit · issue comment
gw pr merge <n> [--squash] [--delete-branch] · pr verified <n> --sha SHA
gw label ensure NAME [--color HEX] [--description D]
gw api <endpoint> [-X METHOD] [-f k=v] [-F k=json]   # {repo} expands to owner/repo
```

JSON out, the same shape on both services (`server/providers/base.py`). Exit
1 when the service refuses or the item does not exist, 2 when the origin's
host is unknown. `pr create` refuses a reviewer who is not a collaborator and
exits 1 when the body's `Fixes #n` linked nothing. `pr merge` reads the PR
back and reports the state of every issue its body closes. `-f` sends a
string, `-F` a JSON value — a form field typed as a bool refuses the string.
There is no verb that rewrites a PR body.

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

## Codex compatibility update — 0.57.0

A batch no longer asks "one at a time or together": several rows picked are a
background batch, analysed by at most four agents at once, that interrupts you
twice — a digest of the proposals, answered once, and the outcome
(`refs/batch.md`). `chatdesk.py park` frees the attached chat while they work,
so a `pr-loop` and an `issue-loop` launched from the same desk no longer queue
behind each other. The protocol for marking rows and closing requests is back
in `git-desk`, *Rows and requests*.


One desk skill, `git-desk`, replaces `pr-desk`, `issue-desk` and
`review-desk`: the page already served Pull request, Issue and Filoni from one
server, so the three skills launched the same page. Invoke `git-desk` instead
of the old names; restart running desks after the update.

Codex one-shot jobs select native profiles: Sol/high for analysis, Sol/medium
for triage, Astra/high for operations. Existing host-specific and shared
environment overrides retain precedence. Attached desks use 50-second waits,
yielding and resuming the same command session instead of starting duplicate
listeners. Runtime guidance names the native browser, title and task tools.
Global/repository instructions override lane defaults; when a merge needs
immediate confirmation, detached work returns `needs-input` without merging.

Aligned with upstream 0.55.1, including organization/folder scopes and the
three-view desk. PR descriptions may follow changes to the approach, with a
warning before invalidating existing review comments; another author's body
requires authorization. The obsolete Bash body guard is removed. Codex can
load shared hook files, but the remaining Claude Skill matcher does not
establish its model gate. Refresh the chat after an installed plugin update.
