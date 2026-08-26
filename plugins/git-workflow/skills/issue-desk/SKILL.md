---
name: issue-desk
description: Launch the issue desk — the dashboard of the repo's open issues, attached to this chat. It reads the issues itself and paints in seconds, computing the cross-check and the shortlist without a model on every read; buttons (dedicated work sessions, issue-analyze, issue-loop, issue-triage on the rows already downloaded) come back here as events. Use when the user asks for the issue desk or an issue dashboard.
---

# Issue desk

Read `<PLUGIN_ROOT>/refs/runtime.md` first.

Launch the issue desk in attached-chat mode using the host procedure from the
runtime reference. The common command is:

```bash
python3 <PLUGIN_ROOT>/server/prdesk.py --chat --desk issue
```

Claude browser-preview configuration:

```json
{"name": "issue-desk", "runtimeExecutable": "python3",
 "runtimeArgs": ["<PLUGIN_ROOT>/server/prdesk.py", "--chat", "--desk", "issue"],
 "port": 8398}
```

Then follow `../review-desk/SKILL.md` sections 2–3: park the watcher
(**one per repo** — skip if a sibling desk already parked it) and process
the events it prints.

The desk does **not** triage at startup: it fetches the provider itself and
paints in seconds, cross-check and shortlist included — both are filters, not
verdicts, and it recomputes them on every read. `issue-triage` arrives only
when the user presses ↻, and the event carries `rows` — the path of the JSON
the desk has already downloaded. Run the skill on that file rather than
re-querying, and write back only per issue (`issues.<n>`): the impact rank,
the type you verified, the finding, and `at`. Never a copy of the shortlist.
