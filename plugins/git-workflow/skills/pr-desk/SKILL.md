---
name: pr-desk
description: Launch the PR desk. The Python server serves provider/cache JSON without keeping Codex or Claude active; the launching chat stays attached by default, so every click except triage is executed in that conversation, command and output visible there; a detached launch (opt-in) hands each click to an ephemeral one-shot agent instead. Use when the user asks for the PR desk or a PR dashboard.
---

# PR desk

Read `<PLUGIN_ROOT>/refs/runtime.md` first.

Launch the PR desk using the host procedure from the runtime reference.
Select the current host as its one-shot backend and start it as a background
process whose stderr goes to a log:

```bash
python3 <PLUGIN_ROOT>/server/prdesk.py --desk pr --agent <claude|codex> \
    >> "${TMPDIR:-/tmp}/git-workflow-pr-desk.log" 2>&1 &
```

The URL is the last `pr desk on http://127.0.0.1:<port>` line the log
prints within a couple of seconds — read it, never assume 8399. The
default port is 8399, but a desk that finds it taken by another repo or by
the sibling desk moves to a free port the OS picks, and one that finds ITS OWN
twin there (same repo, same desk) prints the twin's URL and exits instead of
starting a second server. Do not pass `--port`: it is strict and fails on a
busy port. The server exits by itself after an hour without a request and
with no job running (`--idle-exit 0` disables), so a desk left behind never
squats the port of the next one.

Open that URL in the Browser pane beside the chat on Claude Code —
`preview_start` with `url`, the tool `runtime.md` → *Desks* names; a link
alone is not the deliverable. The page is local, served by the process you
just started: no login, nothing to ask first. Codex: the browser panel. There
is no fixed-port launch configuration: the port is known only once the server
has bound it.

Then title this chat, when the host has a title tool (`runtime.md` →
*Session metadata*): `PR desk · <owner/repo> · <YYYY-MM-DD HH:MM>`, date
and time from `date '+%F %H:%M'`, never from memory. When the desk closes —
the monitor ends with `■ desk chiuso`, or the user says stop — set the same
title again with ` · closed` appended: the session list then tells an open
desk from a spent one.

The launching chat stays **attached by
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
  Monitor(command="python3 <PLUGIN_ROOT>/server/chatdesk.py listen --repo <owner/repo> --session <session-id> --desk pr",
          description="desk clicks · <owner/repo>", persistent=true, timeout_ms=3600000)
  ```

  Tell the user once that the desk is open and its clicks land here. Each
  click then comes back as a notification; follow "Attached chat" in
  `../review-desk/SKILL.md` to execute and publish it. One monitor per
  session and repository; use the stable session identity described in
  `../review-desk/SKILL.md`. A second desk in this chat requires restarting
  that monitor with `--desk both` and the same session ID. A desk already
  owned by another live chat rejects attachment: report the owner, never
  silently take it over. The monitor ends when its selected desk closes
  (both selected desks for `--desk both`),
  with a `■ desk chiuso` notification: retitle the chat ` · closed` and arm
  nothing else.
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
