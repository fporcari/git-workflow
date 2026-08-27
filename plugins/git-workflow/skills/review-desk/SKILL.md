---
name: review-desk
description: Launch BOTH desks (PR desk + issue desk) attached to this chat, and the reference for how an attached chat processes desk events (triage, orders, analyses, pings, dedicated sessions). Use when the user asks for both dashboards at once; for a single one, pr-desk and issue-desk are the entry skills and they point back here for the event loop.
---

# Review desk — attached to this chat

Read `<PLUGIN_ROOT>/refs/runtime.md` first. It defines plugin-root resolution,
desk launch, watcher waiting and dedicated-session creation for this host.

The dashboard renders state; this session is its engine. Buttons in the desk
enqueue events; a background watcher wakes this session, which acts with its
full context and writes results back where the desk reads them.

## 1 · Launch the two desks in chat mode

There are TWO servers — the PR desk (port 8399) and the issue desk (port
8398) — sharing one repo, one state file, one inbox and one watcher. Launch
both from the repo checkout using the host procedure in the runtime reference.
The common commands are:

```bash
python3 <PLUGIN_ROOT>/server/prdesk.py --chat --desk pr
python3 <PLUGIN_ROOT>/server/prdesk.py --chat --desk issue
```

Claude browser-preview configuration:

```json
{"name": "pr-desk", "runtimeExecutable": "python3",
 "runtimeArgs": ["<PLUGIN_ROOT>/server/prdesk.py", "--chat", "--desk", "pr"],
 "port": 8399},
{"name": "issue-desk", "runtimeExecutable": "python3",
 "runtimeArgs": ["<PLUGIN_ROOT>/server/prdesk.py", "--chat", "--desk", "issue"],
 "port": 8398}
```

**The desks do NOT triage at startup or reload.** Each one reads the provider
in the background and paints its real rows within seconds; triage starts only
from its button and arrives as a `triage` event carrying `rows` — the path of
a JSON file with the queue and the issues **already downloaded**. Read that
file instead of re-querying the provider: the skill's own fetch is the
slowest thing it does and the desk has already paid for it. Launch only the
desk the user asked for when they name one.

Without `--chat` a desk runs standalone: Analyze uses the selected local agent
backend (`--agent auto|claude|codex`) and Go leaves orders for `pr-loop`; no
startup triage.

Options: `--repo owner/repo` (default: the cwd's origin), `--provider
github|forgejo|fixture` (forgejo needs `FORGEJO_URL`/`FORGEJO_TOKEN`;
`fixture` replays a recorded payload with no network, for development),
`--me`, `--port` (default by desk), `--keep-state`, `--no-prefetch`,
`--keep-cache` (reuse the previous run's provider cache
instead of reading the provider again — offline work).

## 2 · Park the watcher

Start the watcher using the host procedure in the runtime reference:

```bash
python3 <PLUGIN_ROOT>/server/watch_inbox.py --repo <owner/repo>
```

It blocks until a desk button is clicked, prints the pending events as JSON
lines, and exits. Claude may end the turn after parking its wakeable watcher.
Codex keeps the task active and waits on the watcher process in bounded
intervals. In both cases, tell the user in one line that the desk is live and
the session is listening.

## 3 · On wake: process the events

Read the printed events, truncate the inbox — **never by writing the path
yourself**, it lives under the OS temp dir and only the plugin knows where:

```bash
python3 <PLUGIN_ROOT>/server/inbox.py --repo <owner/repo> --truncate
```

then for each event in order:

- **`{"kind": "analyze", "n": N}`** — run the `pr-analyze` skill on PR #N
  right here (read-only; full playbook in `../pr-analyze/SKILL.md`). **An
  analyze never waits for a triage**: it needs nothing a triage produces, so
  a PR the user already knows is analyzed immediately, including while a
  triage agent is still grinding in the background. Write
  the result into the desk state file as that skill specifies — the desk is
  polling and will show the block. In chat, report its compact decision block
  **in Italian** (author / problem / history / proposal), then ask `Procedo
  con questa proposta?`; NEVER paste raw JSON or the English draft into chat,
  and never the block inside a code fence or with the English field labels —
  the draft lives in the desk panel. A chat go-ahead authorizes exactly that
  displayed proposal; execute it under `pr-loop` and report what was done.
- **`{"kind": "order", "n": N}`** — the user clicked Go on the analysis
  block: that click is the authorization, do not re-ask. Read the order from
  the state file (`orders.<N>`: `propose`, `draft`, `instruction`) and
  execute it under the pr-loop rules (A2 discipline for answers, A1 gates
  re-checked fresh before any merge, A3 for realigns; an empty or `vai`
  instruction means the proposal as it stands, any other text wins). Set the
  order's `status` to `done` with a one-line `report`, or `failed`/
  `needs-input` with why. Report in chat what was done.
- **`{"kind": "issue-analyze", "n": N}`** — the user wants issue #N worked
  in a **dedicated session**. Read the issue's title (one `gh issue view`),
  then create the host-native dedicated task described in the runtime reference
  with:
  - `title`: `Lavora issue #N — <slug>`;
  - `prompt` (self-contained, the new session knows nothing): the repo, the
    issue number and title, the checkout directory, and the instruction to
    follow `<PLUGIN_ROOT>/skills/issue-work/SKILL.md` — analyze
    fresh, fix in a worktree and open the PR when it is one coherent change,
    otherwise lay out the phases (offering a phased workflow only if that
    plugin is installed there);
  - `cwd`: the repo checkout.
  Then notify the desk: `sessione dedicata pronta per #N — aprila dalla chat`.
  Never start that work in the supervising session.
- **`{"kind": "ping", "token": T}`** — the desk's test mode checking the
  roundtrip. Answer immediately and cheaply, nothing else:
  `python3 <PLUGIN_ROOT>/server/notify.py --repo <owner/repo> --pong T "pong — chat collegata e in ascolto"`,
  then restart the watcher. No analysis, no chat prose beyond one line.
- **`{"kind": "explain", "n": N}`** — one line, in Italian, on what PR #N is
  for. The desk only offers this button when the PR has **no description of
  its own**: otherwise it shows the author's own opening straight from the
  payload, which costs nothing. So when this arrives, there is genuinely
  nothing to paraphrase — read the issue it closes and the diff's shape (file
  names, not contents), write `prs.<N>.what`, close the request. One
  sentence.
- **`{"kind": "triage", "flow": ..., "rows": "<path>"}`** — hand it to a
  **background subagent** and stay free: the model half of a triage is the
  slowest thing the desk asks for, and running it here wedges every
  `analyze`/`order`/`explain` behind it. Spawn the host's background agent
  (Claude: the Agent tool, background; Codex: a task) with a self-contained
  prompt: the repo, the `rows` path, the state file path, and the
  instruction to follow `<PLUGIN_ROOT>/skills/pr-triage/SKILL.md`
  (or issue-triage) report-only from that file. The agent does what the
  skill's desk mode prescribes — never touches `grid`/`chase` (the desk has
  ALREADY published them), writes per PR under `prs.<n>` (the one-line
  `what`, the reading of an `asks` row, `conflict_kind` on a DIRTY branch)
  or per issue under `issues.<n>` (impact, verified type, finding, `at`),
  posts progress with `notify.py`, and closes `triage:<flow>` itself. The
  state file takes concurrent writers safely (safejson locks), so other
  events run here in parallel without waiting. Restart the watcher right
  away; when the agent's completion lands, relay §8's repo-level findings
  in chat in a few lines — nothing else, the grid is already on screen.
  Skip the skill's closing handover question — the user drives from the
  dashboard.
- **`{"kind": "run", "flow": "pr-loop"|"issue-loop", "ns": [...], "batch": N}`**
  — run that skill here in chat, step by step. `ns` is the rows the user
  picked by hand in the dashboard: it means *exactly those, in that order,
  and then stop*, the same as the numbers typed as arguments. Do not re-ask
  which ones — the picking was the answer. `batch` is how many to propose
  together, 1..4, and he was asked for it at the moment he pressed ▶, so do
  not re-ask that either. An empty `ns` is the whole queue.
  Three rules the desk depends on: (1) after every action that changes the
  queue (a merge above all) press the desk's own triage again — or tell the
  user to — so the settled PR **disappears from the dashboard**: the grid is
  the desk's to write, never yours; (2) with a batch, mark every row it is working
  (`notify.py --batch`), because a marker naming one of N leaves the others
  reading as idle; (3) plain words everywhere the user reads — never bare
  "Lane A/Lane B" in chat or feed: say *azioni automatiche* and *le PR che
  richiedono te*.
- **`{"kind": "shutdown"}`** — the user pressed the desk's stop button: the
  server has already stopped itself. Do NOT restart the watcher; confirm in
  one line that the desk is down.

While working any event, post progress so the desk shows it live:

```bash
python3 <PLUGIN_ROOT>/server/notify.py --repo <owner/repo> [--pr <n>] "<one line>"
```

### Say which rows you are on

**This section is the protocol. `pr-loop` and `issue-loop` point here rather
than restating it — it encodes exact flags, and three copies of exact flags
drift.**

Any event that works PRs or issues — a `run` above all, but also an
`analyze` — marks them while it lasts, so the desk highlights those rows and
the user sees where the needle is without reading the feed:

```bash
# one at a time
python3 <PLUGIN_ROOT>/server/notify.py --repo <owner/repo> \
  --pr <n> --working "cosa stai facendo, in una riga"

# a batch: every row it is working glows, not just the first
python3 <PLUGIN_ROOT>/server/notify.py --repo <owner/repo> \
  --batch 1145,1128,1059 --working "in parallelo, un worktree per PR"
```

Setting a number the marker does not hold **moves** it: that is the loop
walking to the next one. Setting one the live batch **does** hold refines
that item and leaves the set standing — which is how per-item progress
reaches the desk without collapsing N glowing rows back to one.

`--idle` drops the marker, and so does closing a request with
`--done`/`--failed`. A marker nobody updates for a quarter of an hour is
dropped by the desk itself: a row left glowing after the session died reads
as work in progress, which is worse than no highlight. That is a backstop,
not a substitute for `--idle`.

### Close the request when you are done — always

Every button press is recorded in the desk's ledger and **locks that button**
until you close it: the click hands work to a chat that may take minutes, and
without a lock the user presses again because nothing visibly happened, and
you get the same event three times. The lock is also the only place the
outcome shows up.

```bash
python3 <PLUGIN_ROOT>/server/notify.py --repo <owner/repo> \
  --done analyze:1145 "nessuna risposta da dare: il claim regge"
#            ^^^^^^^^^^^^ <kind>:<number>, or <kind>:<flow> for triage/run
# --failed instead of --done when it did not work out, with why
```

Keys: `analyze:<n>`, `explain:<n>`, `order:<n>`, `issue-analyze:<n>`,
`triage:<flow>`, `run:<flow>`. A request you never close goes stale after
half an hour so a dead session cannot wedge the button forever — but that is
a backstop, not a substitute for closing it.

**One request per loop, not per item.** `run:<flow>` stays a single request
however wide the loop's batches: closing it per item would re-arm the ▶
button mid-loop. What a batch changes is the *report*, which must name every
item — a group of four with one failure is three successes and one failure,
said in four names:

```bash
python3 <PLUGIN_ROOT>/server/notify.py --repo <owner/repo> \
  --done run:pr-loop "2 merge (#1145 #1059), 1 fallita (#1128: conflitto in un file che la base ha riscritto), 3 non raggiunte"
```

Use `--failed` only when **nothing** was accomplished. A loop that merged two
and lost one did its job and says so in the report; marking the whole run
failed would hide the two that landed.

The user drives from the chat; the desk is the radar. Decisions (picks,
go-aheads beyond an order, anything Lane B) are asked HERE, never rendered
as desk interactions.

Then **restart the watcher** (step 2) and end the turn. Stop the loop when
the user says so or the preview server is stopped; a watcher exit code 3
(timeout, if one was set) just means restart it.

## What the desk shows

**Fetch paints facts; triage publishes verdicts.** On the PR desk, boot and
reload show the provider queue without pretending it has been triaged. On the
explicit `pr-triage` click the server computes §7 verdicts, §5 blocks and §6
chase and **writes them itself**, keyed to a fingerprint of the fields each
verdict reads: a PR the provider has moved since becomes `stale`, one never
triaged is `missing`. The issue cross-check remains a deterministic provider
annotation, and so is the issue shortlist — a filter is not a verdict, and
neither is ever a model's copy to keep in sync. An issue analysis is DATED
instead: the desk compares `issues.<n>.at` with the issue's last activity and
says *analisi da aggiornare* when the issue has moved past the reading.
What the model is called for is what only it can do, one PR at a time: the
rows marked `asks`, one-line explanations on request, the impact ranking, and
pr-analyze's diff read. It writes those under `prs.<n>`, never over the grid.

Verdicts are the pr-triage vocabulary; the Chase tab groups the people to chase
over the user's OWN PRs only, per pr-triage §6 — somebody else's stalled PR is
that author's queue, not a block to hand out;
the detail panel merges the state file live — analyses, drafts, order
outcomes. Copying prompts is the last-resort link at the bottom of the
panel.

Rows come from a session cache (fresh for two minutes, served stale while it
revalidates) that **launching a desk clears**: starting the desk is a request
for the truth now. What the cache buys is what happens while it is up — a
browser reload, the UI's polling, a second tab, the sibling desk on the same
repo (a desk starting within a minute of another spares what that one just
fetched, instead of making both pay again). ⟳ forces a fresh read.

The cache, the inbox, the watcher heartbeat and the rows export live in a
private per-user dir under the OS temp dir, so nothing session-scoped is left
in the user's home. Only the state file — the analyses, drafts, orders and
grid published by an explicit triage — stays in
`~/.local/state/git-workflow/`,
which is what `--keep-state` carries across a relaunch. The
merge state is fetched as a second phase — it is by far the most expensive
field on GitHub — so the merge column may read `…` for a beat on a cold
start and fill itself in. A queue the provider had to truncate is stated in
a banner, never hidden.
