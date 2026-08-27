---
name: pr-desk
description: Launch the PR desk — the dashboard of the pull request queue, attached to this chat. Startup and reload fetch provider facts only; an explicit pr-triage button publishes the grid and chase on a fresh provider read, and from then on the engine re-verdicts moved PRs on every read — only never-triaged rows stay highlighted. Merge orders, pr-analyze and pr-loop also come back here as events. Use when the user asks for the PR desk or a PR dashboard.
---

# PR desk

Read `<PLUGIN_ROOT>/refs/runtime.md` first.

Launch the PR desk in attached-chat mode using the host procedure from the
runtime reference. The common command is:

```bash
python3 <PLUGIN_ROOT>/server/prdesk.py --chat --desk pr
```

Claude browser-preview configuration:

```json
{"name": "pr-desk", "runtimeExecutable": "python3",
 "runtimeArgs": ["<PLUGIN_ROOT>/server/prdesk.py", "--chat", "--desk", "pr"],
 "port": 8399}
```

Then follow `../review-desk/SKILL.md` sections 2–3: park the watcher
(**one per repo** — skip if a sibling desk already parked it) and process
the events it prints.

The desk does **not** triage at startup or reload: both are pure provider
fetches that paint in seconds. `pr-triage` arrives only when the user presses
its button. That press computes and publishes the whole deterministic grid on
the server, and the event carries `rows` — the path of the JSON the desk has
already downloaded. Run the skill on that file rather than re-querying, and
add only per-PR work: the grid is already on screen, and the file's
`needs_model` names the only rows still owing a reading. A PR the provider
moves is re-verdicted by the engine itself on every read — the published
triage never expires; only a PR no press has ever seen is `missing`.
