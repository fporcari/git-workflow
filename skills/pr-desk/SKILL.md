---
name: pr-desk
description: Launch the PR desk — the dashboard of the pull request queue, attached to this chat. It reads the queue itself and paints in seconds, computing the triage grid, the merge gate and the chase blocks without a model; buttons (merge orders, pr-analyze, pr-loop, pr-triage on the rows already downloaded) come back here as events. Use when the user asks for the PR desk or a PR dashboard.
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
the events it prints.

The desk does **not** triage at startup: it fetches the provider itself and
paints in seconds. `pr-triage` arrives only when the user presses ↻, and the
event carries `rows` — the path of the JSON the desk has already downloaded.
Run the skill on that file rather than re-querying.
