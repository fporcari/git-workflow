---
name: review-desk
description: Launch both detached review desks. Their Python servers serve local JSON and fresh provider facts without keeping Codex or Claude active; explicit buttons alone start ephemeral one-shot agent processes.
---

# Review desk

Read `<PLUGIN_ROOT>/refs/runtime.md` first. It defines how to select the
current host's one-shot backend and how to open localhost.

## Launch

Run both servers as ordinary background processes:

```sh
python3 <PLUGIN_ROOT>/server/prdesk.py --desk pr --agent <claude|codex>
python3 <PLUGIN_ROOT>/server/prdesk.py --desk issue --agent <claude|codex>
```

The default ports are 8399 for PRs and 8398 for issues. Both desks may share
the same repository state. Open the printed URLs, then the launching
conversation may finish. Never start an inbox watcher and never keep a model
session attached to a desk.

## Runtime contract

Normal page loads and the 30-second refresh perform no model call. Python
serves these local artifacts:

- provider cache and a cheap open-item membership snapshot;
- a rows export consumed by explicit triage jobs;
- durable triage, analysis and order state;
- one request/result JSON for each explicit agent job.

Fresh membership is checked independently from the detailed provider cache.
Rows no longer open are filtered before they reach the browser; a newly open
row forces the detailed queue forward. This prevents a merged PR from being
resurrected by a stale search result.

## Explicit jobs

Only these user actions may start an agent process:

- PR analyze or explain;
- issue analyze;
- PR or issue triage;
- PR loop, issue loop, or an individual order.

Each click creates one runtime job JSON, starts exactly one ephemeral `codex
exec` or `claude -p` process, requires structured output, and exits. Read-only
jobs receive read-only tool permissions. Workflow jobs receive normal host
permissions because the click explicitly authorizes the named operation.
Never use a persistent model session or resume a previous session.

While the process runs, its public JSON event stream is normalized into the
job file: elapsed time, current phase, and a bounded list of tool/command
activity. The desk may poll that local JSON once a second while a job is
active. Never persist thinking blocks, raw tool output, credentials, or the
full prompt as progress. A page reload restores active jobs from the server.

The agent returns data only. It must not edit desk JSON. The Python server
validates that the result refers to the requested PRs/issues and then persists
the allowed fields. A provider refresh is requested only when an operation
reports that it changed provider state.

## Attached chat (hybrid mode)

The server is detached either way. What the non-triage buttons do depends on
whether a chat is attached at click time:

- **No chat attached** (heartbeat stale): every button behaves as above — one
  ephemeral one-shot process per click, report card included.
- **A chat is attached**: analyze, explain, order and run clicks are enqueued
  as `requests` records with `via: "chat"` instead of starting a process. The
  attached chat claims them, executes the named skill IN the conversation —
  where the user reads the output — then publishes the result and closes the
  request so the desk row shows the outcome too. **Triage is the exception:**
  it always runs on the independent one-shot agent, whatever the chat state,
  because its artifacts are desk cells, not conversation output.

The attached chat's loop, after opening the desk URL:

```sh
python3 <PLUGIN_ROOT>/server/chatdesk.py wait --repo <owner/repo> --timeout 540
```

Run it with the host command timeout ABOVE the wait timeout (Claude Code:
Bash timeout 600000 ms). The command heartbeats while it blocks; the server
routes clicks to the chat only while that heartbeat is fresh.

- `{"idle": true}` → run the same command again. Tell the user once, at
  attach time, that you are listening; do not narrate every idle cycle.
- A request record → execute it in this conversation, by `kind`:
  - `analyze` → the `pr-analyze` skill on PR `n`; present the analysis;
  - `explain` → one Italian sentence for PR `n` (linked issue and diff file
    names only, read-only);
  - `issue-analyze` → the `issue-analyze` skill on `n`;
  - `order` → the pr-loop order flow for the order recorded under `orders.<n>`
    (the click was the go-ahead for that displayed proposal);
  - `run` → `pr-loop`/`issue-loop` with the `ns` and `batch` in `payload`.

  Present the outcome in chat AND publish it back in one move:

  ```sh
  python3 <PLUGIN_ROOT>/server/chatdesk.py result --repo <owner/repo> \
      --request <key> result.json
  ```

  `result.json` is the same structured JSON the one-shot agent would have
  returned for that kind (pr-analysis, pr-explanation, issue-analysis or
  operation-result schema). On a failure close the request with
  `python3 <PLUGIN_ROOT>/server/notify.py --repo <owner/repo> --failed <key> "why"`.
- When the user says stop, or before the session ends, run
  `python3 <PLUGIN_ROOT>/server/chatdesk.py detach --repo <owner/repo>`. A
  missed detach costs only the heartbeat TTL before the buttons fall back to
  one-shot agents.

Autonomy in attached mode is exactly the desk's: a click carries the same
authorization it would have given the one-shot agent — analysis is read-only,
an order or run click authorizes the named operation, and a merge is never
autonomous beyond what the skill already allows.

## Triage freshness

Startup and reload fetch facts but do not triage. An explicit triage click
forces a fresh provider read, publishes the deterministic grid immediately,
then starts a one-shot agent only when the exported `model_tasks` says model
work is still due. The engine re-verdicts changed rows on every read, so an
unchanged prior result remains reusable without spending tokens.

## Stop

The Stop button terminates the Python server and the agent jobs it started. A
job stopped this way is recorded as aborted, not left pending. No model or
watcher should remain resident merely because a browser tab is open.
