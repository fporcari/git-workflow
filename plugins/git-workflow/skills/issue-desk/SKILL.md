---
name: issue-desk
description: Launch the issue desk. The Python server serves provider/cache JSON without keeping Codex or Claude active; the launching chat stays attached by default, so every click except triage is executed in that conversation, command and output visible there; a detached launch (opt-in) hands each click to an ephemeral one-shot agent instead. Use when the user asks for the issue desk or an issue dashboard.
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

Open the printed localhost URL. The launching chat stays **attached by
default**: the desk is the remote, this conversation is where the work
happens. Every click except triage arrives here as the command it stands for
(`/pr-loop 1099 1055 batch=4`, `/pr-analyze 1099`, `/issue-analyze 7`) and is
executed here, reasoning and output included, while the desk window sits in
any browser — or gets ignored.

Open that chat on `fable` at effort `high`: what it produces is read by humans
and acts without a second ask (`runtime.md` → *Model policy*). The one-shot jobs
keep their own profiles.

- **Claude Code**: right after opening the URL, arm ONE persistent monitor
  and end the turn:

  ```
  Monitor(command="python3 <PLUGIN_ROOT>/server/chatdesk.py listen --repo <owner/repo>",
          description="desk clicks · <owner/repo>", persistent=true, timeout_ms=3600000)
  ```

  Tell the user once that the desk is open and its clicks land here. Each
  click then comes back as a notification; follow "Attached chat" in
  `../review-desk/SKILL.md` to execute and publish it. One monitor per
  repository: both desks share the state file, so a second desk on the same
  repo from the same chat reuses the running monitor.
- **Codex** (no monitor): follow the `wait` loop in the same section.
- **Detached (opt-in)**: only when the user says to open the desk and leave
  ("apri e basta", "detached"). Arm nothing; every button starts its own
  one-shot agent and the session may end.

Either way the server itself is detached — `../review-desk/SKILL.md` is the
job contract shared by both desks.

The desk does **not** triage at startup: it fetches the provider itself and
paints in seconds, cross-check and shortlist included — both are filters, not
verdicts, and it recomputes them on every read. `issue-triage` arrives only
when the user presses ↻. The server exports a fresh rows JSON and starts one
ephemeral `issue-triage` process only for the issues that still need model
work. The process must not write desk state; the server validates its
structured result and persists only the requested issue records.
