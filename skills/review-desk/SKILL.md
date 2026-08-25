---
name: review-desk
description: Launch the review-desk dashboard attached to THIS chat session — a web view of the PR queue and open issues whose Analyze/Go buttons send their requests back here, so analyses and executions run in the chat with its full context (CLAUDE.md, skills, permissions). Use when the user asks for the dashboard, the review desk, or a visual overview of PRs and issues.
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

**At startup each desk enqueues its own triage** (`pr-triage` /
`issue-triage`): expect those events as soon as the watcher parks, run them
report-only and export — the desks fill themselves. The ↻ button re-runs
them. Launch only the desk the user asked for when they name one.

Without `--chat` a desk runs standalone: Analyze spawns a headless
read-only `claude -p` and Go leaves orders for `/pr-run`; no startup triage.

Options: `--repo owner/repo` (default: the cwd's origin), `--provider
github|forgejo` (forgejo needs `FORGEJO_URL`/`FORGEJO_TOKEN`), `--me`,
`--port` (default by desk), `--keep-state`.

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

Read the printed events, truncate the inbox
(`: > ~/.local/state/git-workflow/<owner>__<repo>__inbox.jsonl`), then for
each event in order:

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
- **`{"kind": "triage", "flow": "pr-triage"|"issue-triage"}`** — run that
  skill here, report-only, and export its output to the desk state as the
  skill's own export section specifies (`grid`+`chase` for pr-triage,
  `situa` for issue-triage): the desk's Triage tab renders exactly that
  export. Skip the skill's closing handover question — the user drives from
  the dashboard.
- **`{"kind": "run", "flow": "pr-run"|"issue-run"}`** — run that skill here
  in chat, step by step.
- **`{"kind": "shutdown"}`** — the user pressed the desk's stop button: the
  server has already stopped itself. Do NOT restart the watcher; confirm in
  one line that the desk is down.

While working any event, post progress so the desk shows it live:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/server/notify.py --repo <owner/repo> [--pr <n>] "<one line>"
```

The user drives from the chat; the desk is the radar. Decisions (picks,
go-aheads beyond an order, anything Lane B) are asked HERE, never rendered
as desk interactions.

Then **restart the watcher** (step 2) and end the turn. Stop the loop when
the user says so or the preview server is stopped; a watcher exit code 3
(timeout, if one was set) just means restart it.

## What the desk shows

Verdicts are the pr-triage vocabulary computed from provider fields
(anything needing a diff read shows `asks`); the Chase tab groups people to
chase (raw field grouping until pr-triage exports its verified §6 blocks);
the detail panel merges the state file live — analyses, drafts, order
outcomes. Copying prompts is the last-resort link at the bottom of the
panel. Data is cached two minutes; the sync button forces a fresh read.
