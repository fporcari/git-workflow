---
name: issue-desk
description: Launch the issue desk — the dashboard of the repo's open issues, attached to this chat. At startup it runs issue-triage by itself and fills its shortlist; buttons (dedicated work sessions, issue-loop, reload triage) come back here as events. Use when the user asks for the issue desk or an issue dashboard.
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
the events it prints.

The desk does **not** triage at startup: it fetches the provider itself and
paints in seconds. `issue-triage` arrives only when the user presses ↻, and the
event carries `rows` — the path of the JSON the desk has already downloaded.
Run the skill on that file rather than re-querying.
