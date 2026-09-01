---
name: pr-desk
description: Launch the PR desk. The Python server serves provider/cache JSON without keeping Codex or Claude active; the launching chat stays attached by default, so every click except triage is executed in that conversation, command and output visible there; a detached launch (opt-in) hands each click to an ephemeral one-shot agent instead. Use when the user asks for the PR desk or a PR dashboard.
---

# PR desk

Read `<PLUGIN_ROOT>/refs/runtime.md` first.

Launch the PR desk using the host procedure from the runtime reference. Select
the current host as its one-shot backend:

```bash
python3 <PLUGIN_ROOT>/server/prdesk.py --desk pr --agent <claude|codex>
```

Claude browser-preview configuration:

```json
{"name": "pr-desk", "runtimeExecutable": "python3",
 "runtimeArgs": ["<PLUGIN_ROOT>/server/prdesk.py", "--desk", "pr", "--agent", "claude"],
 "port": 8399}
```

Open the printed localhost URL. The launching chat stays **attached by
default**: the desk is the remote, this conversation is where the work
happens. Every click except triage arrives here as the command it stands for
(`/pr-loop 1099 1055 batch=4`, `/pr-analyze 1099`, `/issue-analyze 7`) and is
executed here, reasoning and output included, while the desk window sits in
any browser — or gets ignored.

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

The desk does **not** triage at startup or reload: both are pure provider
fetches that paint in seconds. `pr-triage` arrives only when the user presses
its button. That press reads the provider fresh, computes and publishes the
whole deterministic grid on the server, then starts one ephemeral
`pr-triage` process only if `model_tasks` names stale artifacts. The process
reads the exported rows file; the server validates its structured result and
writes the durable state. A PR the provider moves is re-verdicted by the
engine itself on every read.

A completed `pr-loop`, `issue-loop` or order job asks every open tab for one
fresh provider read when its result reports a provider mutation. Refreshing
facts never means pressing triage and never spends model tokens.
