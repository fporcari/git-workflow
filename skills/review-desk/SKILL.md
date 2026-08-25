---
name: review-desk
description: Launch the review-desk dashboard attached to THIS chat session — a web view of the PR queue and open issues whose Analyze/Go buttons send their requests back here, so analyses and executions run in the chat with its full context (CLAUDE.md, skills, permissions). Use when the user asks for the dashboard, the review desk, or a visual overview of PRs and issues.
---

# Review desk — attached to this chat

The dashboard renders state; this session is its engine. Buttons in the desk
enqueue events; a background watcher wakes this session, which acts with its
full context and writes results back where the desk reads them.

## 1 · Launch the server in chat mode

Via the browser preview (a `launch.json` entry), from the repo's checkout:

```json
{
  "name": "review-desk",
  "runtimeExecutable": "python3",
  "runtimeArgs": ["${CLAUDE_PLUGIN_ROOT}/server/prdesk.py", "--chat", "--port", "8399"],
  "port": 8399
}
```

Without `--chat` the desk runs standalone: Analyze spawns a headless
read-only `claude -p` (pr-analyze skill) and Go leaves orders for `/pr-run`.
With `--chat`, both buttons write to the inbox instead.

Options: `--repo owner/repo` (default: the cwd's origin), `--provider
github|forgejo` (forgejo needs `FORGEJO_URL`/`FORGEJO_TOKEN`), `--me`, `--port`.

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
  polling and will show the block. One line in chat: which PR, the verdict.
- **`{"kind": "order", "n": N}`** — the user clicked Go on the analysis
  block: that click is the authorization, do not re-ask. Read the order from
  the state file (`orders.<N>`: `propose`, `draft`, `instruction`) and
  execute it under the pr-run rules (A2 discipline for answers, A1 gates
  re-checked fresh before any merge, A3 for realigns; an empty or `vai`
  instruction means the proposal as it stands, any other text wins). Set the
  order's `status` to `done` with a one-line `report`, or `failed`/
  `needs-input` with why. Report in chat what was done.
- **`{"kind": "issue-analyze", "n": N}`** — spawn one fresh read-only agent
  following `../issue-analyze/SKILL.md` on issue #N (virgin context by
  design); it persists the verdict to the desk state itself.
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
