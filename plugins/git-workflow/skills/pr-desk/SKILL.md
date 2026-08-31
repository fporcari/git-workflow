---
name: pr-desk
description: Launch the detached PR desk. The Python server serves provider/cache JSON without keeping Codex or Claude active; only explicit analyze, explain, triage or workflow clicks start an ephemeral agent process — or, when the launching chat stays attached, run in that conversation with the output presented there (triage always stays on the independent agent). Use when the user asks for the PR desk or a PR dashboard.
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

Open the printed localhost URL. The default is detached:

- **Detached (default)**: the session may end; every button starts its own
  one-shot agent. Do not wait on the server and do not start a watcher.
- **Attached (explicit opt-in)**: only when the user asks to keep results in
  this conversation, follow "Attached chat" in `../review-desk/SKILL.md`.
  Non-triage buttons are then executed here and presented here.

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
