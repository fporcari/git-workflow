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

Start the requested desk with the appropriate `--desk` value and a one-shot
agent backend:

```sh
python3 <PLUGIN_ROOT>/server/prdesk.py --desk pr --agent codex
python3 <PLUGIN_ROOT>/server/prdesk.py --desk issue --agent claude
```

- Claude Code: use its browser-preview launch configuration when available.
- Codex: start the process in a persistent terminal session, then open the
  localhost URL with the Codex browser panel/tool when available. Otherwise
  give the URL to the user.

Use `--agent claude` from Claude Code and `--agent codex` from Codex.
`--agent auto` is the compatibility fallback for a manual launch.

Read-only PR analysis may use an explicit model profile without changing the
user's global host configuration:

- `GIT_WORKFLOW_ANALYZE_MODEL` and `GIT_WORKFLOW_ANALYZE_EFFORT` apply to both;
- `GIT_WORKFLOW_CODEX_ANALYZE_MODEL` / `_EFFORT` override them for Codex;
- `GIT_WORKFLOW_CLAUDE_ANALYZE_MODEL` / `_EFFORT` override them for Claude.

Effort accepts the common portable values `low`, `medium`, `high`, `xhigh` and
`max`. With no variables set, each host keeps its configured defaults.

The server is detached from the launching conversation. It reads provider
cache, rows and job JSON files by itself, and it never starts a model merely
because the desk is open or polling. With no chat attached, analyze, explain,
triage and workflow buttons each start one ephemeral CLI process, wait through
the corresponding job JSON, then let the process exit. The launching
conversation may finish as soon as it has opened the desk — or stay ATTACHED
via `chatdesk.py wait`, in which case the server routes every non-triage click
to that conversation instead (see "Attached chat" in the review-desk skill).
Triage always stays on the independent one-shot agent.

Active jobs expose their elapsed time, phase and sanitized public tool events
through the same JSON. The browser reads that local progress once a second
only while work is running; it never receives thinking blocks or raw command
output.

`--chat` is accepted only so old launch commands do not fail; it has no effect.

## Background delegation

When a workflow explicitly calls for a background subagent, use the host's
internal delegation mechanism: Claude Code's Agent tool in background mode, or
Codex's collaboration/subagent tool. The subagent reports back to the
supervising session and shares its working context. Do not create a user-owned
Codex task/thread for this internal work.

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

Set a session/task title only when the host exposes a title tool.

- Claude Code: `mcp__ccd_session_mgmt__set_session_title`. It is a deferred
  tool, so it must be loaded with ToolSearch before the call, or declared in
  the `allowed-tools` of the command wrapper that loads the skill. A tool the
  skill does not name is a tool the model never looks for.
- Codex: the host's own thread/task title tool, when one is exposed.

Missing title support never blocks the workflow.
