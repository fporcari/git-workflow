---
name: issue-desk
description: Launch the issue desk — the dashboard of the repo's open issues, attached to this chat. At startup it runs issue-triage by itself and fills its situa; buttons (dedicated work sessions, issue-run, reload triage) come back here as events. Use when the user asks for the issue desk or an issue dashboard.
---

# Issue desk

Launch the issue desk server in chat mode (browser preview, `launch.json`):

```json
{"name": "issue-desk", "runtimeExecutable": "python3",
 "runtimeArgs": ["${CLAUDE_PLUGIN_ROOT}/server/prdesk.py", "--chat", "--desk", "issue"],
 "port": 8398}
```

Then follow `../review-desk/SKILL.md` sections 2–3: park the watcher
(**one per repo** — skip if a sibling desk already parked it) and process
the events it prints. The desk enqueues its own `issue-triage` at startup:
expect that event immediately and run it report-only with the export.
