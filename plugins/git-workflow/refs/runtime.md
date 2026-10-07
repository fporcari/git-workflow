# Host runtime

The workflow semantics are portable. Only the mechanics in this file vary by
host. Global and repository instructions override every skill's default mandate.
On Codex resolve `~/.codex/AGENTS.md` and read `git-workflow.md` beside its
real target before Git/GitHub work. If those instructions require confirmation
immediately before each merge, an invocation, batch selection or desk click
does not waive it: ask then in the attached chat; a detached one-shot returns
`needs-input` without merging.

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

Start the desk with `--desk pr` and the current host as its one-shot agent
backend; Da rivedere, Mie, Issue and A chi tocca are sections of that one
desk:

```sh
python3 <PLUGIN_ROOT>/server/prdesk.py --desk pr --agent codex
```

Start it in the background with stderr on a log and open the URL of the
`pr desk on http://127.0.0.1:<port>` line it prints — the port is chosen
at bind time (default when free, otherwise one the OS picks, or the running
twin's URL), so no launch configuration may hard-code it.

- Claude Code: call the plugin's `mcp__git-workflow__desk_pane` tool, which
  opens the `/desk` pane the plugin's mod draws beside the chat in the host's
  own theme, and keep the session id its answer names for the listener.
  Where the tool does not exist (a host that loads no mods), open the URL in
  the Browser pane beside the chat with `mcp__Claude_Browser__preview_start`,
  `url` set to the printed URL (`mcp__Claude_Browser__navigate` does the same
  once the pane is open). Either pane IS the desk; a pasted link is not it.
  The page is local, served on 127.0.0.1 by the process just started: no
  login, nothing to state or ask first — open it. Not the `launch.json`
  browser-preview recipe: it needs a port known in advance.
- Codex: start the process in a persistent terminal session, then call
  `mcp__codex_app__open_in_codex` with target `{"type":"browser","url":<printed URL>}`.
  The opening URL is local; no login is needed. Use the returned URL, never
  a guessed port. If the tool is unavailable, give the URL to the user.

Use `--agent claude` from Claude Code and `--agent codex` from Codex.
`--agent auto` is the compatibility fallback for a manual launch.

Every one-shot job kind may carry an explicit model profile without changing
the user's global host configuration. The scopes are `ANALYZE` (pr-analyze,
issue-analyze, explain), `TRIAGE` (both triages) and `OPERATION` (detached
pr-loop, issue-loop and orders):

- `GIT_WORKFLOW_<SCOPE>_MODEL` and `GIT_WORKFLOW_<SCOPE>_EFFORT` apply to both;
- `GIT_WORKFLOW_CODEX_<SCOPE>_MODEL` / `_EFFORT` override them for Codex;
- `GIT_WORKFLOW_CLAUDE_<SCOPE>_MODEL` / `_EFFORT` override them for Claude.

Effort accepts the common portable values `low`, `medium`, `high`, `xhigh` and
`max`. With no variables set, Claude jobs default to `opus` — `ANALYZE` and
`OPERATION` at `high`, `TRIAGE` at `medium` — and Codex jobs use `gpt-5.6-sol` for `ANALYZE`/high and `TRIAGE`/medium,
and `gpt-6-astra` for `OPERATION`/high. Host-specific overrides win over
shared overrides, which win over defaults. Claude aliases never enter a
Codex command by default.

## Opening a URL

A verification that serves the PR from its worktree opens **that** URL, never
the instance the user has running:

- Claude Code: `navigate` (or `preview_start` with `url`) on the localhost
  URL the launch recipe printed.
- Codex: open the URL in the browser panel/tool when available; otherwise
  give it to the user and wait.

## Model policy

The model follows the reader of the output. Output a human reads — replies to
reviews, PR bodies, proposals, and the merges and realigns Lane A performs
without asking again — wants the strongest model: open the launching chat on
Claude `fable` or Codex `gpt-6-astra`, at effort `high`, and give `OPERATION` the same where the account has
fable (`GIT_WORKFLOW_CLAUDE_OPERATION_MODEL=fable`; the shipped default stays
`opus` because a model the account lacks kills the job at launch). Output a schema reads wants `opus`: `ANALYZE` at `high`
(claims verified against the code), `TRIAGE` at `medium` (a classification over
a grid the server already computed). A background subagent spawned for an
analysis is `opus` too, named explicitly in the delegation call rather than
inherited. `sonnet` is not in the palette: a wrong answer on somebody else's PR
is public and has no repair. The profiles enforce the jobs' model; the chat's
is enforced only where the host can: on Claude Code the plugin ships a
PreToolUse hook (`hooks/hooks.json`) that blocks `pr-loop` below Opus or
Fable, fail-closed. Codex can load shared hooks on the tested desktop
installation; the Skill matcher does not establish a native model gate.
Native one-shot profiles select Codex models, and live chats follow host
instructions. A PR's description may be rewritten when the change moved
(`gh pr edit <n> --body-file <f>`); warn the user first when the rewrite
would make existing review comments read as nonsense. Rewriting another
author's description remains outside automatic actions.

The server is detached from the launching conversation. It reads provider
cache, rows and job JSON files by itself; it never starts a model because a
page is open or polling, and at boot it starts only the read-only jobs of
the preparation for the analyses that are new or changed (`--no-prepare`
turns that off); while somebody polls it, it reads the provider again every
30 minutes and prepares what moved (`--refresh-after`). The launching conversation stays
ATTACHED by default: on Claude Code through one persistent `Monitor` running
`chatdesk.py listen`, on Codex through the `chatdesk.py wait --timeout 50` loop, yielding within
60 seconds and resuming the same command session until it returns.
The Monitor dies at the tool's 30-minute cap, and a desk that died with it
died under the user's hands: on that expiry the chat starts `chatdesk.py doze`
as a background shell, which has no cap, keeps the chat attached, and ends at
the next click with `⏰ sveglia` — the one cue to arm the Monitor again. When
the desk itself is gone (`■ desk chiuso`), or the ear fails, the chat runs
`chatdesk.py close` — server terminated, chat retitled ` · closed` — and arms
nothing. A listener refused because another live chat owns the desk is not a
failed ear: that chat reports the owner and closes nothing, and `close` itself
never terminates a desk another live chat listens to.
Each listener passes a stable conversation `--session` and an explicit
`--desk pr|issue|both`; one live conversation owns each desk. While that
heartbeat is fresh the server routes each non-triage click to its owning
conversation, which executes it there (see "Attached chat" in the git-desk
skill). With no chat attached — a detached launch, or a session that ended —
analyze, explain and workflow buttons each start one ephemeral CLI process,
wait through the corresponding job JSON, then let the process exit. Triage
always stays on the independent one-shot agent, and so does any click a
listening chat is not there to take. A request the chat claimed stays its own for the budget the
same click would have had as a one-shot job; past that it reads as stale.

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

## Telling the user

A batch interrupts the user twice (`refs/batch.md`): when its digest is ready
and when its work is done. Each time, reach him where he is:

- Claude Code: `PushNotification`, a deferred tool — load it with ToolSearch
  before the call. One short line: the loop's tag and what waits for him
  (`PR · digest pronto: 3 da decidere`).
- Codex: no push tool; the chat message and the desk feed line are the
  notice.

Never for anything else: a notification per agent is the noise the batch
exists to remove.

## Dedicated work

A desk click requesting a dedicated issue session is the user's explicit
request for that session.

- Claude Code: create its native spawn-task chip.
- Codex: call `mcp__codex_app__list_projects`, then
  `mcp__codex_app__create_thread` for the exact saved repository project. Use the saved
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
- Codex: `mcp__codex_app__set_thread_title`, omitting `threadId` to title
  this chat. Use `CODEX_THREAD_ID` as the stable listener session id.

The desk titles the launching chat so it can be found again in the session
list: `Git desk · <owner/repo> · <YYYY-MM-DD HH:MM>` on opening, date and time
from `date '+%F %H:%M'`, and the same title with ` · closed` appended once the
desk is gone. The triages title theirs by date only.

Missing title support never blocks the workflow.
