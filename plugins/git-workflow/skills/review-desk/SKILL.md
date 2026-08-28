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

## Triage freshness

Startup and reload fetch facts but do not triage. An explicit triage click
forces a fresh provider read, publishes the deterministic grid immediately,
then starts a one-shot agent only when the exported `model_tasks` says model
work is still due. The engine re-verdicts changed rows on every read, so an
unchanged prior result remains reusable without spending tokens.

## Stop

The Stop button terminates the Python server only. Agent jobs already running
finish independently and leave their final JSON for the next launch. No model
or watcher should remain resident merely because a browser tab is open.
