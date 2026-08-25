# Host runtime

The workflow semantics are portable. Only the mechanics in this file vary by
host.

## Plugin root

`<PLUGIN_ROOT>` is a placeholder, not an environment variable. Resolve it from
the loaded `SKILL.md`: it is the plugin directory containing `skills/`, `refs/`
and `server/`. Replace the placeholder with that absolute path before running a
command. Never assume `CLAUDE_PLUGIN_ROOT`, `CODEX_HOME` or another host-specific
variable in a shared skill.

## Questions

Use the host's structured user-input tool when it supports the question. When a
batch needs multi-select and the available tool cannot express it, print the
numbered proposals and accept one compact typed answer such as `1,3 vai; 2 no`.
Never split one batch into several questions merely to fit a tool schema.

## Desks

Start the requested desk with `python3 <PLUGIN_ROOT>/server/prdesk.py --chat`
and the appropriate `--desk` value.

- Claude Code: use its browser-preview launch configuration when available.
- Codex: start the process in a persistent terminal session, then open the
  localhost URL with the Codex browser panel/tool when available. Otherwise
  give the URL to the user.

Without `--chat`, pass `--agent claude` when launched from Claude Code and
`--agent codex` when launched from Codex. `--agent auto` is the compatibility
fallback for a manual launch.

The attached chat must keep consuming `watch_inbox.py` events while the desk is
live. Claude may park a background watcher whose exit wakes the session. Codex
keeps the task active and waits on the watcher process in bounded intervals;
after processing and truncating the inbox it starts the watcher again. Do not
finish the task while an attached desk is meant to remain connected.

## Dedicated work

A desk click requesting a dedicated issue session is the user's explicit
request for that session.

- Claude Code: create its native spawn-task chip.
- Codex: create a Codex task with the host's thread/task tool. Use the saved
  repository project and its normal isolated worktree. The issue-work skill
  works in that existing isolated checkout and must not create a nested
  worktree.

If no dedicated-session tool exists, provide the complete prompt for the user
to start manually; do not silently run it in the supervising chat.

## Session metadata

Set a session/task title only when the host exposes a title tool. Missing title
support never blocks the workflow.
