---
name: review-desk
description: Launch BOTH desks (PR desk + issue desk) attached to this chat, and the reference for how an attached chat processes desk events (triage, orders, analyses, pings, dedicated sessions). Use when the user asks for both dashboards at once; for a single one, pr-desk and issue-desk are the entry skills and they point back here for the event loop.
---

# Review desk — attached to this chat

The dashboard renders state; this session is its engine. Buttons in the desk
enqueue events; a background watcher wakes this session, which acts with its
full context and writes results back where the desk reads them.

## 1 · Launch the two desks in chat mode

There are TWO servers — the PR desk (port 8399) and the issue desk (port
8398) — sharing one repo, one state file, one inbox and one watcher. Launch
both via the browser preview (`launch.json` entries), from the repo's
checkout:

```json
{"name": "pr-desk", "runtimeExecutable": "python3",
 "runtimeArgs": ["${CLAUDE_PLUGIN_ROOT}/server/prdesk.py", "--chat", "--desk", "pr"],
 "port": 8399},
{"name": "issue-desk", "runtimeExecutable": "python3",
 "runtimeArgs": ["${CLAUDE_PLUGIN_ROOT}/server/prdesk.py", "--chat", "--desk", "issue"],
 "port": 8398}
```

**The desks do NOT triage at startup.** Each one boots, reads the provider
in the background and paints its real rows within seconds; the triage is the
↻ button, and it arrives as a `triage` event carrying `rows` — the path of a
JSON file with the queue and the issues **already downloaded**. Read that
file instead of re-querying the provider: the skill's own fetch is the
slowest thing it does and the desk has already paid for it. Pass
`--triage-at-boot` only if the user wants the old open-and-wait behaviour.
Launch only the desk the user asked for when they name one.

Without `--chat` a desk runs standalone: Analyze spawns a headless
read-only `claude -p` and Go leaves orders for `/pr-run`; no startup triage.

Options: `--repo owner/repo` (default: the cwd's origin), `--provider
github|forgejo|fixture` (forgejo needs `FORGEJO_URL`/`FORGEJO_TOKEN`;
`fixture` replays a recorded payload with no network, for development),
`--me`, `--port` (default by desk), `--keep-state`, `--triage-at-boot`,
`--no-prefetch`.

## 2 · Park the watcher

Start it in a **background** shell (`run_in_background`), so its exit
re-invokes the session:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/server/watch_inbox.py --repo <owner/repo>
```

It blocks until a desk button is clicked, prints the pending events as JSON
lines, and exits. Tell the user in one line that the desk is live and the
session is listening, then end the turn — the watcher's exit is the next
input.

## 3 · On wake: process the events

Read the printed events, truncate the inbox — **never by writing the path
yourself**, it lives under the OS temp dir and only the plugin knows where:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/server/inbox.py --repo <owner/repo> --truncate
```

then for each event in order:

- **`{"kind": "analyze", "n": N}`** — run the `pr-analyze` skill on PR #N
  right here (read-only; full playbook in `../pr-analyze/SKILL.md`). Write
  the result into the desk state file as that skill specifies — the desk is
  polling and will show the block. In chat, report three short lines **in
  Italian** (cosa / storia / proposta); NEVER paste the raw JSON or the
  English draft into the chat — the draft lives in the desk panel.
- **`{"kind": "order", "n": N}`** — the user clicked Go on the analysis
  block: that click is the authorization, do not re-ask. Read the order from
  the state file (`orders.<N>`: `propose`, `draft`, `instruction`) and
  execute it under the pr-run rules (A2 discipline for answers, A1 gates
  re-checked fresh before any merge, A3 for realigns; an empty or `vai`
  instruction means the proposal as it stands, any other text wins). Set the
  order's `status` to `done` with a one-line `report`, or `failed`/
  `needs-input` with why. Report in chat what was done.
- **`{"kind": "issue-analyze", "n": N}`** — the user wants issue #N worked
  in a **dedicated session**. Read the issue's title (one `gh issue view`),
  then create a spawn-task chip (the `spawn_task` tool) with:
  - `title`: `Lavora issue #N — <slug>`;
  - `prompt` (self-contained, the new session knows nothing): the repo, the
    issue number and title, the checkout directory, and the instruction to
    follow `${CLAUDE_PLUGIN_ROOT}/skills/issue-work/SKILL.md` — analyze
    fresh, fix in a worktree and open the PR when it is one coherent change,
    otherwise lay out the phases (offering a phased workflow only if that
    plugin is installed there);
  - `cwd`: the repo checkout.
  Then notify the desk: `sessione dedicata pronta per #N — clicca la chip
  in chat per aprirla`. The chip is the user's click: never start the work
  in this session.
- **`{"kind": "ping", "token": T}`** — the desk's test mode checking the
  roundtrip. Answer immediately and cheaply, nothing else:
  `python3 ${CLAUDE_PLUGIN_ROOT}/server/notify.py --repo <owner/repo> --pong T "pong — chat collegata e in ascolto"`,
  then restart the watcher. No analysis, no chat prose beyond one line.
- **`{"kind": "explain", "n": N}`** — one line, in Italian, on what PR #N is
  for. The desk only offers this button when the PR has **no description of
  its own**: otherwise it shows the author's own opening straight from the
  payload, which costs nothing. So when this arrives, there is genuinely
  nothing to paraphrase — read the issue it closes and the diff's shape (file
  names, not contents), write `prs.<N>.what`, close the request. One
  sentence.
- **`{"kind": "triage", "flow": ..., "rows": "<path>"}`** — run that
  skill here, report-only, **reading `rows` instead of querying the
  provider**. That file holds `{repo, me, generated, queue, issues, grid,
  chase, gates, shortlist}` — the rows AND the deterministic work the desk
  has already done (the §5 blocks, the §6 chase, the §3 gate, the issue
  cross-check and its ten-issue shortlist). Add only what a model can add
  (the `asks` rows, the impact ranking, §8's findings), then export the
  result to the desk state as the skill's own export section specifies (`grid`+`chase` for pr-triage,
  `shortlist` for issue-triage): the desk's Triage tab renders exactly that
  export. Skip the skill's closing handover question — the user drives from
  the dashboard.
- **`{"kind": "run", "flow": "pr-run"|"issue-run"}`** — run that skill here
  in chat, step by step. Two rules the desk depends on: (1) after every
  action that changes the queue (a merge above all) update the grid export
  so the settled PR **disappears from the dashboard** — pr-run's "Publish
  to the review desk" section says how; (2) plain words everywhere the user
  reads — never bare "Lane A/Lane B" in chat or feed: say *azioni
  automatiche* and *le PR che richiedono te*.
- **`{"kind": "shutdown"}`** — the user pressed the desk's stop button: the
  server has already stopped itself. Do NOT restart the watcher; confirm in
  one line that the desk is down.

While working any event, post progress so the desk shows it live:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/server/notify.py --repo <owner/repo> [--pr <n>] "<one line>"
```

### Close the request when you are done — always

Every button press is recorded in the desk's ledger and **locks that button**
until you close it: the click hands work to a chat that may take minutes, and
without a lock the user presses again because nothing visibly happened, and
you get the same event three times. The lock is also the only place the
outcome shows up.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/server/notify.py --repo <owner/repo> \
  --done analyze:1145 "nessuna risposta da dare: il claim regge"
#            ^^^^^^^^^^^^ <kind>:<number>, or <kind>:<flow> for triage/run
# --failed instead of --done when it did not work out, with why
```

Keys: `analyze:<n>`, `explain:<n>`, `order:<n>`, `issue-analyze:<n>`,
`triage:<flow>`, `run:<flow>`. A request you never close goes stale after
half an hour so a dead session cannot wedge the button forever — but that is
a backstop, not a substitute for closing it.

The user drives from the chat; the desk is the radar. Decisions (picks,
go-aheads beyond an order, anything Lane B) are asked HERE, never rendered
as desk interactions.

Then **restart the watcher** (step 2) and end the turn. Stop the loop when
the user says so or the preview server is stopped; a watcher exit code 3
(timeout, if one was set) just means restart it.

## What the desk shows

**The desk computes; the model judges.** The verdicts (§7), the five blocks
(§5), the chase blocks (§6), the merge gate of every base (§3) and the issue
cross-check are all computed by the server from fields and cheap API reads —
pressing ↻ used to spend ~28k tokens of input and a whole turn to re-derive
them. The grid the desk shows is labelled with its provenance: *calcolata dal
desk* until a triage exports a verified one, *verificata dal modello* after.
What the model is called for is what only it can do: the rows marked `asks`,
one-line explanations on request, the impact ranking, and pr-analyze's diff
read.

Verdicts are the pr-triage vocabulary; the Chase tab groups people to chase;
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
verified grid a MODEL produced — stays in `~/.local/state/git-workflow/`,
which is what `--keep-state` carries across a relaunch. The
merge state is fetched as a second phase — it is by far the most expensive
field on GitHub — so the merge column may read `…` for a beat on a cold
start and fill itself in. A queue the provider had to truncate is stated in
a banner, never hidden.
