"""Explicit one-shot agent jobs for the detached review desk.

Every click gets a request JSON under the private runtime directory. Codex or
Claude Code runs only for that request, returns structured output, and exits.
The server validates the result and is the only writer of durable desk state.
"""

import json
import os
import queue
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

import deskstate
import safejson

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
# Broad `gh api` would permit writes, so every provider call stays explicit.
READ_TOOLS = (
    "Bash(gh api graphql:*),Bash(gh pr diff:*),"
    "Bash(gh api -X GET repos/*/compare/*:*),"
    "Bash(gh api -X GET repos/*/contents/*:*),"
    "Bash(git cat-file:*),Bash(git show:*),Bash(git diff:*),Read,Grep,Glob,"
    "mcp__sourcerer__kb_ask,mcp__sourcerer__kb_find_skills,"
    "mcp__sourcerer__sem_ask_codebase,"
    "mcp__sourcerer__code_batch_search_code,"
    "mcp__sourcerer__code_search_code,"
    "mcp__sourcerer__code_find_usages,"
    "mcp__sourcerer__code_get_symbol_source,"
    "mcp__sourcerer__code_get_module_source,"
    "mcp__sourcerer__code_get_usage_examples"
)
TRIAGE_TOOLS = (READ_TOOLS + ",Bash(gh issue view:*),Bash(git show:*),"
                "Bash(git log:*)")
# `--allowedTools` only ADDS auto-approvals: a host whose own settings allow
# `gh` or `git` broadly would let a read-only job merge. These are denied
# outright, whatever the host allows.
WRITE_TOOLS = (
    "Edit,Write,NotebookEdit,"
    "Bash(git push:*),Bash(git commit:*),Bash(git merge:*),Bash(git rebase:*),"
    "Bash(gh pr merge:*),Bash(gh pr review:*),Bash(gh pr comment:*),"
    "Bash(gh pr edit:*),Bash(gh pr close:*),Bash(gh pr ready:*),"
    "Bash(gh pr create:*),Bash(gh issue comment:*),Bash(gh issue edit:*),"
    "Bash(gh issue close:*),Bash(gh issue create:*),"
    "Bash(gh api -X POST:*),Bash(gh api -X PATCH:*),Bash(gh api -X PUT:*),"
    "Bash(gh api -X DELETE:*),Bash(gh api --method:*),Bash(gh api -f:*),"
    "Bash(gh api -F:*),Bash(gh api --field:*),Bash(gh api --raw-field:*),"
    "Bash(gh api --input:*)"
)


ANALYZE_TIMEOUT = deskstate.ANALYZE_TIMEOUT
OPERATION_TIMEOUT = deskstate.OPERATION_TIMEOUT
JOB_RETENTION = 7 * 24 * 60 * 60
SCHEMA = PLUGIN_ROOT / "server" / "schemas" / "pr-analysis.json"
EXPLAIN_SCHEMA = PLUGIN_ROOT / "server" / "schemas" / "pr-explanation.json"
TRIAGE_SCHEMA = PLUGIN_ROOT / "server" / "schemas" / "triage-result.json"
OPERATION_SCHEMA = PLUGIN_ROOT / "server" / "schemas" / "operation-result.json"
ISSUE_SCHEMA = PLUGIN_ROOT / "server" / "schemas" / "issue-analysis.json"

_running = {}
_children = {}
_reconciled = set()
_lock = threading.Lock()
MAX_EVENTS = 40


def job_path(repo, job_id):
    return deskstate.runtime_path(repo, "job-%s.json" % job_id)


def _update(repo, job_id, **fields):
    def mutate(record):
        record.update(fields)
        return dict(record)
    return safejson.update(job_path(repo, job_id), mutate, indent=1)


def resolve_agent(agent):
    if agent != "auto":
        if not shutil.which(agent):
            raise RuntimeError("%s executable not found" % agent)
        return agent
    configured = os.environ.get("GIT_WORKFLOW_AGENT")
    if configured in ("claude", "codex") and shutil.which(configured):
        return configured
    preferred = (("codex", "claude") if os.environ.get("CODEX_THREAD_ID")
                 else ("claude", "codex"))
    for candidate in preferred:
        if shutil.which(candidate):
            return candidate
    raise RuntimeError("neither claude nor codex executable was found")


# claude --json-schema validates against a registry without the 2020-12
# meta-schema, so the $schema annotation the files carry for codex is dropped
def claude_schema(schema):
    document = json.loads(schema.read_text())
    document.pop("$schema", None)
    return json.dumps(document)


EFFORTS = {"low", "medium", "high", "xhigh", "max"}


def _profile(agent, scope):
    if not scope:
        return None, None
    prefix = "GIT_WORKFLOW_%s_%s_" % (agent.upper(), scope.upper())
    shared = "GIT_WORKFLOW_%s_" % scope.upper()
    model = os.environ.get(prefix + "MODEL") or os.environ.get(shared + "MODEL")
    effort = os.environ.get(prefix + "EFFORT") or os.environ.get(shared + "EFFORT")
    return model, effort if effort in EFFORTS else None


def command(agent, prompt, tools, cwd, output_path=None, schema=SCHEMA,
            read_only=True, profile=None):
    model, effort = _profile(agent, profile)
    if agent == "claude":
        cmd = ["claude", "-p", prompt, "--output-format", "stream-json",
               "--verbose", "--no-session-persistence"]
        if model:
            cmd += ["--model", model]
        if effort:
            cmd += ["--effort", effort]
        if schema:
            cmd += ["--json-schema", claude_schema(schema)]
        if read_only:
            cmd += ["--allowedTools", tools, "--disallowedTools", WRITE_TOOLS]
        else:
            cmd += ["--tools", "default", "--permission-mode", "auto"]
        return cmd
    cmd = ["codex", "exec", "--ephemeral", "-C", cwd]
    cmd += ["--json"]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["-c", 'model_reasoning_effort="%s"' % effort]
    if read_only:
        cmd += ["--sandbox", "read-only"]
    else:
        cmd += ["--approve-for-me", "--add-dir", str(deskstate.STATE_DIR),
                "--add-dir", str(deskstate.RUNTIME_DIR)]
    if schema:
        cmd += ["--output-schema", str(schema)]
    if output_path:
        cmd += ["--output-last-message", output_path]
    return cmd + [prompt]


def parse_structured(agent, stdout, output_path=None):
    raw = stdout
    if agent == "claude":
        envelopes = []
        for line in stdout.splitlines():
            try:
                envelopes.append(json.loads(line))
            except ValueError:
                pass
        envelope = next((event for event in reversed(envelopes)
                         if event.get("type") == "result"),
                        envelopes[-1] if envelopes else {})
        raw = envelope.get("structured_output") or envelope.get("result") or ""
    elif output_path:
        raw = Path(output_path).read_text()
    return raw if isinstance(raw, dict) else json.loads(raw)


def _short(value, limit=180):
    text = " ".join(str(value or "").split())
    lowered = text.lower()
    if any(marker in lowered for marker in
           ("authorization:", "github_pat_", "ghp_", "sk-ant-", "api_key=")):
        return "sensitive command hidden"
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _tool_progress(name, inputs):
    inputs = inputs if isinstance(inputs, dict) else {}
    value = (inputs.get("command") or inputs.get("file_path") or
             inputs.get("path") or inputs.get("pattern") or
             inputs.get("query") or inputs.get("description"))
    detail = _short(value)
    label = name or "tool"
    if detail:
        label = "%s · %s" % (label, detail)
    lowered = detail.lower()
    stage = ("testing" if any(word in lowered for word in
                              ("pytest", "unittest", "test", "ruff", "lint"))
             else "inspecting" if name in ("Read", "Grep", "Glob") or
             lowered.startswith(("gh ", "git ", "rg ", "sed ", "cat "))
             else "working")
    return {"stage": stage, "detail": label}


def stream_failure(agent, stdout):
    """Both CLIs keep the fatal reason in their JSON stream, not on stderr."""
    reason = ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        if agent == "codex":
            item = event.get("item")
            item = item if isinstance(item, dict) else event
            if item.get("type") == "error":
                reason = item.get("message") or reason
            continue
        if event.get("type") == "result" and event.get("is_error"):
            reason = event.get("result") or reason
        elif event.get("is_api_error_message"):
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") == "text" and block.get("text"):
                    reason = block["text"]
    return " ".join(str(reason).split())


def progress_event(agent, line):
    """Normalize public CLI activity; thinking and raw tool output stay out."""
    try:
        event = json.loads(line)
    except (TypeError, ValueError):
        return None
    if agent == "codex":
        kind = event.get("type")
        if kind == "thread.started":
            return {"stage": "starting", "detail": "Codex session started"}
        if kind == "turn.started":
            return {"stage": "working", "detail": "Agent turn started"}
        if kind == "turn.completed":
            return {"stage": "finalizing", "detail": "Agent turn completed"}
        if kind in ("item.started", "item.completed"):
            item = event.get("item") or {}
            item_type = item.get("type")
            if item_type == "command_execution":
                progress = _tool_progress("Command", {"command": item.get("command")})
                if kind == "item.completed":
                    progress["detail"] = "Completed · %s" % progress["detail"]
                return progress
            if item_type == "mcp_tool_call":
                return _tool_progress(
                    "%s.%s" % (item.get("server") or "MCP",
                                item.get("tool") or "tool"), item.get("arguments"))
            if item_type == "web_search":
                return {"stage": "inspecting", "detail": "Web search"}
        if kind == "error":
            return {"stage": "working", "detail": "Codex reported a recoverable error"}
        return None

    kind = event.get("type")
    if kind == "system" and event.get("subtype") == "init":
        return {"stage": "starting", "detail": "Claude session started"}
    if kind == "assistant":
        for block in (event.get("message") or {}).get("content") or []:
            if block.get("type") == "tool_use":
                return _tool_progress(block.get("name"), block.get("input"))
        return None
    if kind == "rate_limit_event":
        return {"stage": "waiting", "detail": "Waiting for Claude capacity"}
    if kind == "result":
        return {"stage": "finalizing", "detail": "Claude result received"}
    return None


def _record_progress(repo, job_id, event=None, elapsed=0):
    def mutate(record):
        progress = record.setdefault("progress", {})
        progress["elapsed"] = int(elapsed)
        progress["updated_at"] = time.strftime("%H:%M:%S")
        if event:
            progress.update(event)
            events = record.setdefault("events", [])
            if not events or events[-1].get("detail") != event.get("detail"):
                events.append(dict(event, at=time.strftime("%H:%M:%S")))
                del events[:-MAX_EVENTS]
    safejson.update(job_path(repo, job_id), mutate, indent=1)


def _process_identity(pid):
    try:
        started = subprocess.run(
            ("ps", "-o", "lstart=", "-p", str(pid)), capture_output=True,
            text=True, timeout=2)
        command_line = subprocess.run(
            ("ps", "-o", "command=", "-p", str(pid)), capture_output=True,
            text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None
    if started.returncode or command_line.returncode:
        return None
    started_at = started.stdout.strip()
    command_line = command_line.stdout.strip()
    if not started_at or not command_line:
        return None
    return {"started": started_at, "command": command_line}


def _is_our_agent(record):
    try:
        pid = int(record["pid"])
    except (KeyError, TypeError, ValueError):
        return False
    identity = _process_identity(pid)
    if not identity or identity["started"] != record.get("pid_started"):
        return False
    try:
        command_parts = shlex.split(identity["command"])
    except ValueError:
        return False
    agent = record.get("agent")
    return agent in ("codex", "claude") and any(
        Path(part).name == agent for part in command_parts)


def _execute(cmd, cwd, timeout, agent, progress, started=None):
    process = subprocess.Popen(
        cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, bufsize=1)
    if started:
        try:
            started(process)
        except Exception:
            process.terminate()
            process.wait()
            raise
    messages = queue.Queue()

    def read_stream(name, stream):
        for line in iter(stream.readline, ""):
            messages.put((name, line))
        messages.put((name, None))

    readers = [threading.Thread(target=read_stream, args=("stdout", process.stdout),
                                daemon=True),
               threading.Thread(target=read_stream, args=("stderr", process.stderr),
                                daemon=True)]
    for reader in readers:
        reader.start()
    started = time.monotonic()
    ended, stdout, stderr, tick = set(), [], [], -1
    timed_out = False
    while len(ended) < 2:
        elapsed = time.monotonic() - started
        if elapsed > timeout and process.poll() is None:
            timed_out = True
            process.kill()
        try:
            name, line = messages.get(timeout=0.25)
        except queue.Empty:
            second = int(elapsed)
            if second != tick:
                tick = second
                progress(None, elapsed)
            continue
        if line is None:
            ended.add(name)
            continue
        (stdout if name == "stdout" else stderr).append(line)
        if name == "stdout":
            progress(progress_event(agent, line), elapsed)
    returncode = process.wait()
    for reader in readers:
        reader.join(timeout=1)
    process.stdout.close()
    process.stderr.close()
    if timed_out:
        raise subprocess.TimeoutExpired(cmd, timeout)
    return subprocess.CompletedProcess(
        cmd, returncode, "".join(stdout), "".join(stderr))


def parse_result(agent, stdout, output_path=None, expected_n=None):
    result = parse_structured(agent, stdout, output_path)
    required = {"n", "author", "problem", "history", "propose", "draft",
                "verified", "not_verified"}
    if not isinstance(result, dict) or not required <= set(result):
        raise ValueError("agent returned an invalid PR analysis")
    if expected_n is not None and result["n"] != expected_n:
        raise ValueError("agent returned analysis for PR #%s, expected #%s"
                         % (result["n"], expected_n))
    if not all(isinstance(result[key], str)
               for key in ("author", "problem", "history", "propose")):
        raise ValueError("agent returned non-text analysis fields")
    if not all(result[key].strip()
               for key in ("author", "problem", "history", "propose")):
        raise ValueError("agent returned empty analysis fields")
    if (result["draft"] is not None and
            not isinstance(result["draft"], str)):
        raise ValueError("agent returned an invalid draft")
    if not all(isinstance(result[key], list) and
               all(isinstance(item, str) for item in result[key])
               for key in ("verified", "not_verified")):
        raise ValueError("agent returned invalid verification lists")
    return result


def persist(repo, result, analysis_keys=None):
    def mutate(state):
        record = {"author": result["author"],
                  "problem": result["problem"],
                  "history": result["history"],
                  "analysis": "%s\n%s" % (result["problem"], result["history"]),
                  "next": result["propose"],
                  "verified": result["verified"],
                  "not_verified": result["not_verified"]}
        keys = ({"analysis": analysis_keys} if isinstance(analysis_keys, str)
                else (analysis_keys or {}))
        for source, target in (("analysis", "analysis_key"),
                               ("problem", "problem_key"),
                               ("history", "history_key")):
            if keys.get(source):
                record[target] = keys[source]
        if keys.get("problem_head"):
            record["problem_head"] = keys["problem_head"]
        if result.get("draft"):
            record["draft"] = result["draft"]
        target = state.setdefault("prs", {}).setdefault(str(result["n"]), {})
        if not result.get("draft"):
            target.pop("draft", None)
        target.update(record)
    deskstate.update(repo, mutate)


def persist_explanation(repo, result, n, what_key):
    if (not isinstance(result, dict) or result.get("n") != n
            or not isinstance(result.get("what"), str)):
        raise ValueError("agent returned an invalid PR explanation")
    what = result["what"].strip()
    if not what:
        raise ValueError("agent returned an empty PR explanation")
    def mutate(state):
        record = state.setdefault("prs", {}).setdefault(str(n), {})
        record.update(what=what, what_key=what_key)
    deskstate.update(repo, mutate)


def persist_issue_analysis(repo, result, n):
    if not isinstance(result, dict) or result.get("n") != n:
        raise ValueError("agent returned analysis for the wrong issue")
    required = ("type", "finding", "size", "phase")
    if not all(result.get(key) for key in required):
        raise ValueError("agent returned an incomplete issue analysis")
    def mutate(state):
        record = state.setdefault("issues", {}).setdefault(str(n), {})
        record.update({key: result[key] for key in required})
        record["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        for key in ("problem", "cause", "propose", "verify", "decision"):
            if result.get(key):
                record[key] = result[key]
    deskstate.update(repo, mutate)


def persist_triage(repo, result, flow, exported):
    if result.get("flow") != flow:
        raise ValueError("agent returned the wrong triage flow")
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    if flow == "pr-triage":
        expected = {int(n): tasks
                    for n, tasks in exported.get("model_tasks", {}).items()}
        rows = {row["n"]: row for row in exported.get("queue", [])}
        items = {item["n"]: item for item in result.get("prs", [])}
        if set(items) != set(expected):
            raise ValueError("agent returned the wrong PR triage items")
        records = {}
        for n, tasks in expected.items():
            item = items[n]
            record = {"at": now}
            if "analysis" in tasks:
                required = ("author", "problem", "history", "propose")
                if not all(isinstance(item.get(key), str) and item[key].strip()
                           for key in required):
                    raise ValueError("agent returned an incomplete analysis for #%s" % n)
                record.update(
                    author=item["author"], problem=item["problem"],
                    history=item["history"],
                    analysis="%s\n%s" % (item["problem"], item["history"]),
                    next=item["propose"], verified=item["verified"],
                    not_verified=item["not_verified"],
                    analysis_key=rows[n]["model_keys"]["analysis"])
                if item.get("draft"):
                    record["draft"] = item["draft"]
            if "conflict" in tasks:
                if item.get("conflict_kind") not in ("mechanical", "substantive"):
                    raise ValueError("agent did not classify conflict #%s" % n)
                record.update(
                    conflict_kind=item["conflict_kind"],
                    conflict_key=rows[n]["model_keys"]["conflict"])
                if item.get("finding"):
                    record["conflict_finding"] = item["finding"]
            records[str(n)] = record
        def mutate(state):
            target = state.setdefault("prs", {})
            for n, record in records.items():
                target.setdefault(n, {}).update(record)
        deskstate.update(repo, mutate)
        return

    expected = set(exported.get("shortlist") or [])
    items = {item["n"]: item for item in result.get("issues", [])}
    if set(items) != expected:
        raise ValueError("agent returned the wrong issue triage items")
    def mutate(state):
        target = state.setdefault("issues", {})
        for n, item in items.items():
            target.setdefault(str(n), {}).update(
                type=item["type"], impact=item["impact"],
                finding=item["finding"], at=now)
    deskstate.update(repo, mutate)


def parse_operation(agent, stdout, output_path=None):
    result = parse_structured(agent, stdout, output_path)
    if (not isinstance(result, dict)
            or result.get("status") not in ("done", "needs-input", "failed")):
        raise ValueError("agent returned an invalid operation status")
    if not isinstance(result.get("report"), str) or not result["report"].strip():
        raise ValueError("agent returned an empty operation report")
    if not isinstance(result.get("provider_changed"), bool):
        raise ValueError("agent returned an invalid provider change flag")
    return result


def persist_operation(repo, result, n=None, flow=None):
    if n is not None:
        def mutate(state):
            order = state.setdefault("orders", {}).setdefault(str(n), {})
            order.update(status=result["status"], report=result["report"])
        deskstate.update(repo, mutate)
    # the loop's own report outlives its job file: the page has nowhere else
    # to read what a finished run decided, and terminal records get pruned
    label = "order:%s" % n if n is not None else (flow or "run")
    def keep(state):
        state.setdefault("runs", {})[label] = {
            "status": result["status"], "report": result["report"],
            "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    deskstate.update(repo, keep)
    # the server's word on the flow is the last one: a skill that closed the
    # ledger itself on the way out cannot leave "done" over a needs-input
    key = "order:%s" % n if n is not None else "run:%s" % (flow or "run")
    deskstate.close_request(repo, key, result["status"], result["report"])
    if result["provider_changed"]:
        deskstate.request_provider_refresh(repo)


def _run(job_id, key, agent, repo, prompt, tools, timeout, cwd, schema,
         parser, persister, read_only, profile=None, prepare=None):
    output_path = None
    started_at = time.monotonic()
    try:
        agent = resolve_agent(agent)
        _update(repo, job_id, agent=agent)
        if prepare:
            # the provider read this click needs is the job's FIRST PHASE, with
            # its own progress line — held inside the HTTP handler instead it
            # was ten seconds in which the desk showed nothing at all
            _record_progress(
                repo, job_id,
                {"stage": "inspecting", "detail": "reading the provider"}, 0)
            prompt, finished = prepare()
            if prompt is None:
                _record_progress(repo, job_id,
                                 {"stage": "done", "detail": finished["report"]},
                                 time.monotonic() - started_at)
                _update(repo, job_id, status="done", result=finished,
                        agent=agent)
                return
        _record_progress(
            repo, job_id,
            {"stage": "starting", "detail": "%s process starting" % agent},
            time.monotonic() - started_at)
        if agent == "codex":
            descriptor, output_path = tempfile.mkstemp(
                prefix="git-workflow-result-", suffix=".json")
            os.close(descriptor)
        cmd = command(agent, prompt, tools, cwd, output_path, schema, read_only,
                      profile)

        def process_started(process):
            identity = _process_identity(process.pid)
            with _lock:
                _children[job_id] = (repo, key, process)
            fields = {"pid": process.pid}
            if identity:
                fields["pid_started"] = identity["started"]
            _update(repo, job_id, **fields)

        out = _execute(
            cmd, cwd, timeout, agent,
            lambda event, elapsed: _record_progress(
                repo, job_id, event, elapsed), process_started)
        if out.returncode:
            exited = "%s exited %s" % (agent, out.returncode)
            reason = out.stderr.strip() or stream_failure(agent, out.stdout)
            raise RuntimeError(
                ("%s: %s" % (exited, reason))[:600] if reason else exited)
        result = parser(agent, out.stdout, output_path)
        persister(repo, result)
        status = result.get("status", "done") if isinstance(result, dict) else "done"
        _record_progress(
            repo, job_id,
            {"stage": status, "detail": "Job finished"},
            time.monotonic() - started_at)
        _update(repo, job_id, status=status, result=result, agent=agent)
    except Exception as exc:
        if isinstance(exc, subprocess.TimeoutExpired):
            budget = ("GIT_WORKFLOW_ANALYZE_TIMEOUT" if read_only else
                      "GIT_WORKFLOW_OPERATION_TIMEOUT")
            exc = RuntimeError("%s budget exceeded after %s seconds"
                               % (budget, timeout))
        if (get(repo, job_id) or {}).get("status") != "aborted":
            _record_progress(
                repo, job_id,
                {"stage": "error", "detail": _short(exc)},
                time.monotonic() - started_at)
            _update(repo, job_id, status="error", error=str(exc)[:600])
    finally:
        with _lock:
            if _running.get((repo, key)) == job_id:
                _running.pop((repo, key), None)
            _children.pop(job_id, None)
            _reconciled.discard((repo, job_id))
        if output_path:
            try:
                Path(output_path).unlink()
            except OSError:
                pass


def _spawn(kind, key, agent, repo, request, prompt, tools, timeout, cwd,
           schema, parser, persister, read_only=True, profile=None,
           prepare=None):
    with _lock:
        if (repo, key) in _running:
            return _running[(repo, key)]
        job_id = uuid.uuid4().hex[:12]
        _running[(repo, key)] = job_id
        safejson.write(
            job_path(repo, job_id),
            {"id": job_id, "kind": kind, "key": key, "status": "running",
             "request": dict(request, repo=repo),
             "progress": {"stage": "queued", "detail": "Job queued",
                          "elapsed": 0, "updated_at": time.strftime("%H:%M:%S")},
             "events": [],
             "at": time.strftime("%Y-%m-%dT%H:%M:%S")}, indent=1)
    threading.Thread(target=_run,
                     args=(job_id, key, agent, repo, prompt, tools, timeout,
                           cwd, schema, parser, persister, read_only, profile,
                           prepare),
                     daemon=True).start()
    return job_id


def get(repo, job_id):
    path = job_path(repo, job_id)
    return safejson.read(path) if path.exists() else None


def _mark_terminal(repo, job_id, status, detail):
    _record_progress(repo, job_id, {"stage": status, "detail": detail})
    _update(repo, job_id, status=status)


def reconcile(repo, retention=JOB_RETENTION):
    prefix = "%s__job-*.json" % repo.replace("/", "__")
    now = time.time()
    for path in deskstate.runtime_dir().glob(prefix):
        try:
            record = safejson.read(path)
            status = record.get("status")
            if (status in ("done", "error", "aborted", "orphaned") and
                    now - path.stat().st_mtime > retention):
                path.unlink()
                continue
            if status != "running":
                continue
            job_id = record.get("id") or path.stem.rsplit("job-", 1)[-1]
            key = record.get("key")
            if job_id and key and _is_our_agent(record):
                with _lock:
                    _running.setdefault((repo, key), job_id)
                    _reconciled.add((repo, job_id))
            else:
                _mark_terminal(
                    repo, job_id,
                    "orphaned", "Job process could not be verified")
        except (OSError, ValueError):
            continue


def active(repo):
    with _lock:
        ids = {job_id for (target, _), job_id in _running.items()
               if target == repo}
        reconciled = {job_id for target, job_id in _reconciled
                      if target == repo}
    for job_id in reconciled:
        record = get(repo, job_id)
        if record and _is_our_agent(record):
            continue
        if record and record.get("status") == "running":
            _mark_terminal(repo, job_id, "orphaned",
                           "Job process could not be verified")
        with _lock:
            _reconciled.discard((repo, job_id))
            key = (record or {}).get("key")
            if key and _running.get((repo, key)) == job_id:
                _running.pop((repo, key), None)
        ids.discard(job_id)
    return [record for record in (get(repo, job_id) for job_id in ids) if record]


def shutdown(grace=2):
    with _lock:
        running = list(_children.items())
    targets = []
    for job_id, (repo, key, process) in running:
        if process.poll() is None:
            try:
                process.terminate()
                targets.append((job_id, repo, key, process))
            except OSError:
                pass
    deadline = time.monotonic() + max(0, grace)
    while any(process.poll() is None for _, _, _, process in targets):
        if time.monotonic() >= deadline:
            break
        time.sleep(0.05)
    for _, _, _, process in targets:
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
    for job_id, repo, key, process in targets:
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        _mark_terminal(repo, job_id, "aborted", "Desk stopped the job")
        with _lock:
            if _running.get((repo, key)) == job_id:
                _running.pop((repo, key), None)
            _children.pop(job_id, None)
            _reconciled.discard((repo, job_id))
    return len(targets)


ANALYZE_PROMPT = (
    "Read %s/skills/pr-analyze/SKILL.md and follow it exactly for PR #%s of %s "
    "(the user's login is %s). Use the provider snapshot and diff plus exact "
    "local Git objects as the skill directs. Stay read-only: never fetch, post, "
    "push, merge, edit or write any file. The desk already gathered this compact "
    "evidence; use it before any provider call and do not repeat its fields: "
    "<desk_context>%s</desk_context>. Your final message must be exactly the JSON "
    "object the skill specifies, with no fences and no prose around it.")


def analyze_pr(repo, n, me, cwd, agent="auto", inputs=None):
    """`inputs` returns (keys, context) and READS THE PROVIDER — a probe plus,
    on a desk whose gates are not filled yet, the whole queue. It runs inside
    the job so the click is answered at once."""
    keys = {}

    def prepare():
        analysis_keys, context = inputs() if inputs else ({}, {})
        keys.update(analysis_keys or {})
        evidence = json.dumps(context or {}, separators=(",", ":"),
                              sort_keys=True)
        return ANALYZE_PROMPT % (PLUGIN_ROOT, n, repo, me, evidence), None

    parser = lambda selected, stdout, output: parse_result(  # noqa: E731
        selected, stdout, output, n)
    persister = lambda target, result: persist(  # noqa: E731
        target, result, dict(keys))
    return _spawn("analyze", "pr:%s:analyze" % n, agent, repo,
                  {"n": n}, None,
                  READ_TOOLS, ANALYZE_TIMEOUT, cwd, SCHEMA, parser, persister,
                  profile="ANALYZE", prepare=prepare)


def explain_pr(repo, n, me, cwd, agent="auto", what_key=None):
    prompt = (
        "Explain in one Italian sentence what PR #%s of %s is for. The desk has "
        "already established that its body is empty: read the linked issue and "
        "only the diff file names, never the diff contents. Work through gh "
        "read-only. Return exactly {\"n\": %s, \"what\": \"...\"}."
        % (n, repo, n))
    parser = lambda selected, stdout, output: parse_structured(  # noqa: E731
        selected, stdout, output)
    persister = lambda target, result: persist_explanation(  # noqa: E731
        target, result, n, what_key)
    return _spawn("explain", "pr:%s:explain" % n, agent, repo,
                  {"n": n, "what_key": what_key}, prompt,
                  READ_TOOLS, ANALYZE_TIMEOUT, cwd, EXPLAIN_SCHEMA,
                  parser, persister)


def analyze_issue(repo, n, me, cwd, agent="auto"):
    prompt = (
        "Read %s/skills/issue-analyze/SKILL.md and analyze issue #%s of %s "
        "(login %s) read-only in a fresh context. Do not branch, edit, post or "
        "write state. Return exactly the JSON required by %s."
        % (PLUGIN_ROOT, n, repo, me, ISSUE_SCHEMA))
    parser = lambda selected, stdout, output: parse_structured(  # noqa: E731
        selected, stdout, output)
    persister = lambda target, result: persist_issue_analysis(  # noqa: E731
        target, result, n)
    return _spawn("issue-analyze", "issue:%s:analyze" % n, agent, repo,
                  {"n": n}, prompt, TRIAGE_TOOLS, ANALYZE_TIMEOUT, cwd,
                  ISSUE_SCHEMA, parser, persister)


TRIAGE_PROMPT = (
    "Read %s/skills/%s/SKILL.md and use the already fetched JSON at %s for "
    "%s (login %s). This is detached desk mode: do not write files, state, "
    "comments or provider data. Work only the model-owned items named by "
    "model_tasks or shortlist. Return exactly the JSON required by %s; use "
    "null and empty arrays for PR fields irrelevant to an item's task.")


def triage(repo, flow, export, me, cwd, agent="auto"):
    """`export` performs the fresh provider read, publishes the grid and
    returns the rows file. It is the expensive half of a triage and it runs
    inside the job; the model is asked only for what the export still owes,
    and when it owes nothing the job ends without starting an agent."""
    skill = "pr-triage" if flow == "pr-triage" else "issue-triage"
    published = {}

    def prepare():
        rows_path = export()
        exported = json.loads(Path(rows_path).read_text())
        published["rows"] = exported
        due = (exported.get("shortlist") if flow == "issue-triage"
               else exported.get("model_tasks"))
        if not due:
            return None, {"status": "done", "provider_changed": False,
                          "report": "%s: griglia pubblicata, nessun lavoro "
                                    "del modello dovuto" % flow}
        return TRIAGE_PROMPT % (PLUGIN_ROOT, skill, rows_path, repo, me,
                                TRIAGE_SCHEMA), None

    parser = lambda selected, stdout, output: parse_structured(  # noqa: E731
        selected, stdout, output)
    persister = lambda target, result: persist_triage(  # noqa: E731
        target, result, flow, published["rows"])
    return _spawn("triage", flow, agent, repo, {"flow": flow}, None,
                  TRIAGE_TOOLS, ANALYZE_TIMEOUT, cwd, TRIAGE_SCHEMA,
                  parser, persister, prepare=prepare)


def operation(repo, flow, payload, me, cwd, agent="auto"):
    if flow not in ("pr-loop", "issue-loop", "order"):
        raise ValueError("unknown operation flow")
    n = int(payload["n"]) if flow == "order" else None
    skill = "pr-loop" if flow in ("pr-loop", "order") else "issue-loop"
    mandate = (
        "Execute only order #%s already recorded under orders.%s in the desk "
        "state. The user's Go click is the authorization for that displayed "
        "proposal. Recheck every gate fresh before acting."
        % (n, n) if flow == "order" else
        "Work exactly PR/issue numbers %s in that order, with batch %s. The Run "
        "click selected this scope. Execute only actions the skill classifies as "
        "already authorized; return needs-input for any further decision."
        % (payload.get("ns") or "the current queue", payload.get("batch", 1)))
    prompt = (
        "Read %s/skills/%s/SKILL.md and follow it for %s (login %s) in detached "
        "desk mode. %s Do not wait for chat input. Never add AI attribution. "
        "Never import the server modules, never write the desk state file and "
        "never call notify.py --done or --failed: the server persists your JSON "
        "and closes the job (notify.py --working progress lines are welcome). "
        "Return exactly the JSON required by %s. provider_changed is true only "
        "if you actually changed GitHub or Forgejo."
        % (PLUGIN_ROOT, skill, repo, me, mandate, OPERATION_SCHEMA))
    persister = lambda target, result: persist_operation(  # noqa: E731
        target, result, n, flow)
    key = "order:%s" % n if n is not None else flow
    return _spawn("operation", key, agent, repo, dict(payload, flow=flow),
                  prompt, "", OPERATION_TIMEOUT, cwd, OPERATION_SCHEMA,
                  parse_operation, persister, read_only=False)
