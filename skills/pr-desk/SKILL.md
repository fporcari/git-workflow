---
name: pr-desk
description: Launch the PR desk — the dashboard of the pull request queue, attached to this chat. At startup it runs pr-triage by itself and fills its grid; buttons (merge orders, pr-analyze, pr-run, reload triage) come back here as events. Use when the user asks for the PR desk or a PR dashboard.
---

# PR desk

Launch the PR desk server in chat mode (browser preview, `launch.json`):

```json
{"name": "pr-desk", "runtimeExecutable": "python3",
 "runtimeArgs": ["${CLAUDE_PLUGIN_ROOT}/server/prdesk.py", "--chat", "--desk", "pr"],
 "port": 8399}
```

Then follow `../review-desk/SKILL.md` sections 2–3: park the watcher
(**one per repo** — skip if a sibling desk already parked it) and process
the events it prints. The desk enqueues its own `pr-triage` at startup:
expect that event immediately and run it report-only with the export.
