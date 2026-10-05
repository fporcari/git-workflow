---
name: git-desk
description: Launch the git desk — one local page over the repository's pull requests, issues and threads (tabs Pull request, Issue, Filoni). The Python server serves provider/cache JSON without keeping Codex or Claude active; the launching chat stays attached by default, so every click except triage is executed in that conversation, command and output visible there; a detached launch (opt-in) hands each click to an ephemeral one-shot agent instead. Use when the user asks for the desk, the PR desk, the issue desk, the review desk, or a PR or issue dashboard.
---

# Git desk

Read `<PLUGIN_ROOT>/refs/runtime.md` first.

## Launch

One server serves the whole desk: Pull request, Issue and Filoni are tabs of
the same page, read in one round trip. Launch it using the host procedure
from the runtime reference. Select the current host as its one-shot backend
and start it as a background process whose stderr goes to a log:

```bash
python3 <PLUGIN_ROOT>/server/prdesk.py --desk pr --agent <claude|codex> \
    >> "${TMPDIR:-/tmp}/git-workflow-pr-desk.log" 2>&1 &
```

Always `--desk pr`, also when the user asked for the issues: the page opens on
Pull request and the Issue tab is one click away. A second server on the same
repository would be a second owner of the same click ledger.

The scope is the checkout the desk is launched from; see *Scope* for an
organization or a folder of clones.

The URL is the last `pr desk on http://127.0.0.1:<port>` line the log
prints within a couple of seconds — read it, never assume 8399. The
default port is 8399, but a desk that finds it taken by another repo moves to
a free port the OS picks, and one that finds ITS OWN twin there (same repo,
same desk) prints the twin's URL and exits instead of starting a second
server. Do not pass `--port`: it is strict and fails on a busy port. The
server exits by itself after an hour without a request and with no job
running (`--idle-exit 0` disables), so a desk left behind never squats the
port of the next one.

Open that URL in the Browser pane beside the chat on Claude Code —
`preview_start` with `url`, the tool `runtime.md` → *Desks* names; a link
alone is not the deliverable. The page is local, served by the process you
just started: no login, nothing to ask first. Codex: the browser panel. There
is no fixed-port launch configuration: the port is known only once the server
has bound it.

Then title this chat, when the host has a title tool (`runtime.md` →
*Session metadata*): `Git desk · <scope> · <YYYY-MM-DD HH:MM>` (`<scope>` is
the `repo=` value of the log line), date
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
          description="desk clicks · <owner/repo>", persistent=true, timeout_ms=1800000)
  ```

  On a desk whose log line lists `repos=`, the ear is `--scope <scope>`
  instead of `--repo <owner/repo>`, in this call and in every `doze` and
  `close` below; `result` and `fail` keep `--repo`, the one the click names.

  Tell the user once that the desk is open and its clicks land here. Each
  click then comes back as a notification; follow *Attached chat* below to
  execute and publish it. One monitor per session and repository, with the
  session identity *Attached chat* describes. A desk already owned by another
  live chat rejects attachment: report the owner, never silently take it over.

  `1800000` is the tool's ceiling: a larger number is capped without a word.

  **The monitor's expiry is a doze, not the desk's death.** The monitor ends
  three ways:

  - `■ desk chiuso` (⏻, idle exit, kill): the desk is gone. Run

    ```sh
    python3 <PLUGIN_ROOT>/server/chatdesk.py close --repo <owner/repo> \
        --session <session-id> --desk pr
    ```

    then retitle the chat ` · closed` and arm nothing else. `close` terminates
    whatever server is still up and detaches; on a desk already gone it is a
    no-op.
  - the 30-minute expiry: the desk is still up and the user may click at any
    time. Start the doze as a background shell — Bash with
    `run_in_background`, not a Monitor: a background shell has no cap —

    ```sh
    python3 <PLUGIN_ROOT>/server/chatdesk.py doze --repo <owner/repo> \
        --session <session-id> --desk pr
    ```

    It keeps the chat attached, so clicks still queue for this conversation,
    claims nothing, and ends with `⏰ sveglia · <command>` at the next click:
    arm the monitor again, the same call as above, and it claims that click.
    It ends with `■ desk chiuso` when the desk goes: `close`, retitle, arm
    nothing.
  - the listener refusing to attach (`desk already attached to session
    <id>`): another live chat owns this desk. Report the owner and arm
    nothing — never `close`: that desk is the other chat's work.
  - any other error: `close`, and report it. `close` leaves up a desk
    another live chat listens to, and says so.

  One ear at a time — the monitor or the doze, never both — and the monitor
  is armed again only on `⏰ sveglia`, never on an expiry.
- **Codex** (no monitor): follow the `wait` loop in *Attached chat*.
- **Detached (opt-in)**: only when the user says to open the desk and leave
  ("apri e basta", "detached"). Arm nothing; every button starts its own
  one-shot agent and the session may end.

Either way the server itself is detached.

## Runtime contract

Normal page loads and the 30-second refresh perform no model call. Python
serves these local artifacts:

- provider cache and a cheap open-item membership snapshot;
- a rows export consumed by explicit triage jobs;
- durable triage, analysis and order state;
- one request/result JSON for each explicit agent job.

Fresh membership is checked independently from the detailed provider cache.
Rows no longer open are filtered before they reach the browser; a newly open
row forces the detailed queue forward. This prevents a merged PR from being
resurrected by a stale search result.

## Explicit jobs

Only these user actions may start an agent process:

- PR analyze or explain;
- issue analyze;
- PR or issue triage;
- PR loop, issue loop, or an individual order.

Each click creates one runtime job JSON, starts exactly one ephemeral `codex
exec` or `claude -p` process, requires structured output, and exits. Read-only
jobs receive read-only tool permissions. Workflow jobs receive normal host
permissions because the click explicitly authorizes the named operation.
Never use a persistent model session or resume a previous session.

While the process runs, its public JSON event stream is normalized into the
job file: elapsed time, current phase, and a bounded list of tool/command
activity. The desk may poll that local JSON once a second while a job is
active. Never persist thinking blocks, raw tool output, credentials, or the
full prompt as progress. A page reload restores active jobs from the server.

The agent returns data only. It must not edit desk JSON. The Python server
validates that the result refers to the requested PRs/issues and then persists
the allowed fields. A provider refresh is requested only when an operation
reports that it changed provider state.

A completed `pr-loop`, `issue-loop` or order job asks every open tab for one
fresh provider read when its result reports a provider mutation. Refreshing
facts never means pressing triage and never spends model tokens.

## Attached chat (the default)

The launching chat is the workplace: it stays attached unless the user asked
for a detached launch. The server is detached either way; what non-triage
buttons do depends on whether a chat is attached at click time:

- **No chat attached** (heartbeat stale): every button behaves as above — one
  ephemeral one-shot process per click, report card included.
- **A chat is attached**: analyze, explain, order and run clicks are enqueued
  as `requests` records with `via: "chat-session"`, `desk`, `session` and a unique `id` instead of starting a process. The
  attached chat claims them, executes the named skill IN the conversation —
  where the user reads the output — then publishes the result and closes the
  request so the desk row shows the outcome too. **Triage is the exception:**
  it always runs on the independent one-shot agent, whatever the chat state,
  because its artifacts are desk cells, not conversation output.

Only a chat that is heartbeating takes a NEW click. `chatdesk.py listen`
heartbeats for as long as its monitor lives, work included, so clicks made
while the chat is busy queue behind the conversation and are executed in
order; the monitor dies with the session, the heartbeat stops with it, and
within a minute every button is back on one-shot agents. `chatdesk.py wait`
(hosts without a monitor) heartbeats only while it blocks: a click that lands
while that chat is working starts a one-shot agent instead. A click the chat
never claims is handed back the same way.

A claimed request keeps its button locked for the same budget the click would
have had as a one-shot job (`GIT_WORKFLOW_ANALYZE_TIMEOUT` for analyze,
explain and issue-analyze; `GIT_WORKFLOW_OPERATION_TIMEOUT` for order and
run). Past that, the record reads as stale and the button accepts a new
press: a chat silent for longer than the job it replaces is presumed dead.

An analyze click is answered before the desk reads the provider: the record
sits in `preparing` while a server thread gathers the probe and the keys, and
becomes claimable only when its payload is in. A context the desk cannot read
closes the request as failed without involving the chat.

Use the host's current conversation ID as `<session-id>`. If unavailable,
generate one UUID once (`python3 -c 'import uuid; print(uuid.uuid4())'`) and
keep that literal for this conversation. Pass it to every listen, wait,
result, fail and detach command. Never use an empty value, the repository
name or a reused example ID. One live session owns each desk; a conflicting
attachment exits with the owner's ID. Report that conflict; do not detach
another chat or promise its clicks will arrive here. A duplicate listener for
the same session also exits instead of racing.

Requests are pinned at enqueue time and claimed one at a time. Keep the
returned `id` as `<request-id>` for result/fail: the key alone is reusable
and does not identify a particular click. Older listeners cannot claim this
protocol; restart desks and listeners after updating the plugin.

- **Claude Code**: the one persistent `Monitor` of *Launch*. It does not
  occupy the turn: the user keeps talking here, and each click arrives as a
  notification of two lines — the command it stands for and the request
  record as JSON. Never arm a second one.
- **Codex**: the blocking form,
  `python3 <PLUGIN_ROOT>/server/chatdesk.py wait --repo <owner/repo> --session <session-id> --desk pr --timeout 50`,
  in a persistent host command session. Yield within 60 seconds, retain its
  session id, and resume that same process until it returns; never start a
  second waiter while the first is alive. `{"idle": true}` →
  run it again; tell the user once that you are listening, do not narrate
  every idle cycle. `{"closed": true}` → the desk is gone: retitle the
  chat ` · closed` and stop.

On a request, first echo its command line as the desk composed it — the
reader sees `▶ /pr-loop 1099 1055 batch=4`, not a request key — then execute
it in this conversation, by `kind`:
  - `analyze` → the `pr-analyze` skill on PR `n`; present the analysis;
  - `explain` → one Italian sentence for PR `n` (linked issue and diff file
    names only, read-only);
  - `issue-analyze` → the `issue-analyze` skill on `n`;
  - `order` → the pr-loop order flow for the order recorded under `orders.<n>`
    (the click was the go-ahead for that displayed proposal);
  - `run` → `pr-loop`/`issue-loop` with the `ns` and `batch` in `payload`.

  An analyze request may carry `payload.context`, the same compact desk probe
  a one-shot job receives. Treat it as `<desk_context>` from `pr-analyze`:
  consume it first and do not request its fields again.

  Present the outcome in chat AND publish it back in one move:

  ```sh
  python3 <PLUGIN_ROOT>/server/chatdesk.py result --repo <owner/repo> \
      --session <session-id> --request <key> --request-id <request-id> result.json
  ```

  `result.json` is the same structured JSON the one-shot agent would have
  returned for that kind (pr-analysis, pr-explanation, issue-analysis or
  operation-result schema); an operation's `status` (`done`, `needs-input`,
  `failed`) is what the row shows. On a failure close the request with
  `python3 <PLUGIN_ROOT>/server/chatdesk.py fail --repo <owner/repo> --session <session-id> --request <key> --request-id <request-id> "why"`.
  Both commands heartbeat on the way out, so go straight back to `wait`.
- An order or run that stops on `needs-input` asks its question HERE, in the
  conversation, and publishes the same request key and ID again once the user has
  answered and the operation is finished: only a `needs-input` result may be resumed. A replaced or expired request
  is rejected before any result is persisted.
- When the user says stop: TaskStop the monitor or the doze (Claude Code), then
  `python3 <PLUGIN_ROOT>/server/chatdesk.py close --repo <owner/repo> --session <session-id> --desk pr`
  on either host, and retitle the chat ` · closed`. `detach` alone drops only
  the mark — the buttons fall back to one-shot agents and the server stays up
  until its idle exit; use it when the user wants the desk to outlive this
  conversation, which a detached launch already gives him.

Autonomy in attached mode is exactly the desk's: a click carries the same
authorization it would have given the one-shot agent — analysis is read-only,
an order or run click authorizes the named operation, and a merge is never
autonomous beyond what the skill already allows.

## Scope

One desk may cover several repositories: an organization (`--org
[host/]owner`, repeatable), a folder of clones (launched from a folder that
holds clones and is not itself a checkout, such as `~/Development/erpy-org`,
or `--folder DIR`), or several `--repo`. Each repository keeps its own cache,
state file, jobs and click ledger, exactly as if it had a desk of its own; the
server merges what they serve into one page — every row named `repo #n` —
and sends each click to the repository it names. The log line then reads
`repo=<scope name> … repos=<owner/repo>,…`.

- **The ear is the scope's.** `listen`, `doze`, `wait` and `close` take
  `--scope <scope name>` instead of `--repo`: one heartbeat covers every
  member, and one `close` stops the one server behind them.
- **A click says where it belongs.** A record claimed off a scope carries
  `repo` and `cwd`, and its command line carries `--repo`
  (`▶ /pr-analyze 79 --repo erpy/erpy-engine`). Run the skill against that
  repository and, for anything that touches a checkout, from `cwd` — its
  clone. `cwd` null means the scope has no clone of it: read and analyze
  through the provider, never write; the desk already refuses order and run
  clicks there, and says so on the row.
- **Publish to the member.** `result` and `fail` take `--repo <record.repo>`,
  never `--scope`: the outcome lands in that repository's ledger.
- **A run is one per repository.** Rows picked across repositories become
  one `run` request per repository, each with its own `ns`, in click order.
- **Triage is one per repository too.** The triage press starts one job per
  member; each publishes its own grid, and the page shows them merged.

A desk over a scope refuses to open while any member already has a live desk
of the same kind: two servers on one ledger would steal each other's clicks.

## Triage

The desk does **not** triage at startup or reload: both are pure provider
fetches that paint in seconds. Triage arrives only when the user presses its
button, and the tab decides which one.

- **Pull request** → `pr-triage`. The press reads the provider fresh,
  computes and publishes the whole deterministic grid on the server, then
  starts one ephemeral `pr-triage` process only if `model_tasks` names stale
  artifacts. The process reads the exported rows file; the server validates
  its structured result and writes the durable state. A PR the provider moves
  is re-verdicted by the engine itself on every read.
- **Issue** → `issue-triage`, the ↻ that orders the shortlist. The cross-check
  and the shortlist are filters, not verdicts: the desk recomputes them on
  every read. The press exports a fresh rows JSON and starts one ephemeral
  `issue-triage` process only for the issues that still need model work. The
  process must not write desk state; the server validates its structured
  result and persists only the requested issue records.

An unchanged prior result remains reusable without spending tokens.

## Stop

The Stop button terminates the Python server and the agent jobs it started. A
job stopped this way is recorded as aborted, not left pending. No model or
watcher should remain resident merely because a browser tab is open.
