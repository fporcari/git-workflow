---
name: issue-desk
description: Launch the issue desk. The Python server serves provider/cache JSON without keeping Codex or Claude active; the launching chat stays attached by default, so every click except triage is executed in that conversation, command and output visible there; a detached launch (opt-in) hands each click to an ephemeral one-shot agent instead. Use when the user asks for the issue desk or an issue dashboard.
---

# Issue desk

Read `<PLUGIN_ROOT>/refs/runtime.md` first.

Launch the issue desk using the host procedure from the runtime reference.
Select the current host as its one-shot backend and start it as a background
process whose stderr goes to a log:

```bash
python3 <PLUGIN_ROOT>/server/prdesk.py --desk issue --agent <claude|codex> \
    >> "${TMPDIR:-/tmp}/git-workflow-issue-desk.log" 2>&1 &
```

The URL is the last `issue desk on http://127.0.0.1:<port>` line the log
prints within a couple of seconds — read it, never assume 8398. The
default port is 8398, but a desk that finds it taken by another repo or by
the sibling desk moves to a free port the OS picks, and one that finds ITS OWN
twin there (same repo, same desk) prints the twin's URL and exits instead of
starting a second server. Do not pass `--port`: it is strict and fails on a
busy port. The server exits by itself after an hour without a request and
with no job running (`--idle-exit 0` disables), so a desk left behind never
squats the port of the next one.

Open that URL with `navigate` (or `preview_start` with `url`) on Claude Code,
with the browser panel on Codex. There is no fixed-port launch configuration:
the port is known only once the server has bound it.

Then title this chat, when the host has a title tool (`runtime.md` →
*Session metadata*): `Issue desk · <owner/repo> · <YYYY-MM-DD HH:MM>`, date
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
  Monitor(command="python3 <PLUGIN_ROOT>/server/chatdesk.py listen --repo <owner/repo>",
          description="desk clicks · <owner/repo>", persistent=true, timeout_ms=3600000)
  ```

  Tell the user once that the desk is open and its clicks land here. Each
  click then comes back as a notification; follow "Attached chat" in
  `../review-desk/SKILL.md` to execute and publish it. One monitor per
  repository: both desks share the state file, so a second desk on the same
  repo from the same chat reuses the running monitor. The monitor ends by
  itself once the last desk of the repository is gone (⏻, idle exit, kill),
  with a `■ desk chiuso` notification: retitle the chat ` · closed` and arm
  nothing else.
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
