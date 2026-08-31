---
name: issue-desk
description: Launch the detached issue desk. The Python server serves provider/cache JSON without keeping Codex or Claude active; only explicit analyze, triage or workflow clicks start an ephemeral agent process — or, when the launching chat stays attached, run in that conversation with the output presented there (triage always stays on the independent agent). Use when the user asks for the issue desk or an issue dashboard.
---

# Issue desk

Read `<PLUGIN_ROOT>/refs/runtime.md` first.

Launch the issue desk using the host procedure from the runtime reference.
Select the current host as its one-shot backend:

```bash
python3 <PLUGIN_ROOT>/server/prdesk.py --desk issue --agent <claude|codex>
```

Claude browser-preview configuration:

```json
{"name": "issue-desk", "runtimeExecutable": "python3",
 "runtimeArgs": ["<PLUGIN_ROOT>/server/prdesk.py", "--desk", "issue", "--agent", "claude"],
 "port": 8398}
```

Open the printed localhost URL, then pick the mode:

- **Attached (default in an interactive chat)**: stay in this conversation and
  serve the desk's clicks — follow "Attached chat" in
  `../review-desk/SKILL.md`. Non-triage buttons are executed here, and their
  output is presented here.
- **Detached** (the user dismisses you, or the launch is headless): the
  session may end; every button starts its own one-shot agent. Do not wait on
  the server and do not start a watcher in this mode.

Either way the server itself is detached — `../review-desk/SKILL.md` is the
job contract shared by both desks.

The desk does **not** triage at startup: it fetches the provider itself and
paints in seconds, cross-check and shortlist included — both are filters, not
verdicts, and it recomputes them on every read. `issue-triage` arrives only
when the user presses ↻. The server exports a fresh rows JSON and starts one
ephemeral `issue-triage` process only for the issues that still need model
work. The process must not write desk state; the server validates its
structured result and persists only the requested issue records.
