"""Explicit one-shot agent jobs for the detached review desk.

Every click gets a request JSON under the private runtime directory. Codex or
Claude Code runs only for that request, returns structured output, and exits.
The server validates the result and is the only writer of durable desk state.
"""

import json
import os
import queue
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
# `gh api` alone would allow -X POST: the analysis is read-only, so the
# allowlist names the two calls the skill actually makes
READ_TOOLS = ("Bash(gh api graphql:*),Bash(gh pr diff:*),Read,Grep,Glob")
TRIAGE_TOOLS = (READ_TOOLS + ",Bash(gh issue view:*),Bash(git show:*),"
                "Bash(git log:*)")
ANALYZE_TIMEOUT = 900
SCHEMA = PLUGIN_ROOT / "server" / "schemas" / "pr-analysis.json"
EXPLAIN_SCHEMA = PLUGIN_ROOT / "server" / "schemas" / "pr-explanation.json"
TRIAGE_SCHEMA = PLUGIN_ROOT / "server" / "schemas" / "triage-result.json"
OPERATION_SCHEMA = PLUGIN_ROOT / "server" / "schemas" / "operation-result.json"
ISSUE_SCHEMA = PLUGIN_ROOT / "server" / "schemas" / "issue-analysis.json"

_running = {}
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


def command(agent, prompt, tools, cwd, output_path=None, schema=SCHEMA,
            read_only=True):
    if agent == "claude":
        cmd = ["claude", "-p", prompt, "--output-format", "stream-json",
               "--verbose", "--no-session-persistence"]
        if schema:
            cmd += ["--json-schema", schema.read_text()]
        if read_only:
            cmd += ["--allowedTools", tools]
        else:
            cmd += ["--tools", "default", "--permission-mode", "auto"]
        return cmd
    cmd = ["codex", "exec", "--ephemeral", "-C", cwd]
    cmd += ["--json"]
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


def _execute(cmd, cwd, timeout, agent, progress):
    process = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1)
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


def persist(repo, result, analysis_key=None):
    def mutate(state):
        record = {"author": result["author"],
                  "problem": result["problem"],
                  "history": result["history"],
                  "analysis": "%s\n%s" % (result["problem"], result["history"]),
                  "next": result["propose"],
                  "verified": result["verified"],
                  "not_verified": result["not_verified"]}
        if analysis_key:
            record["analysis_key"] = analysis_key
        if result.get("draft"):
            record["draft"] = result["draft"]
        target = state.setdefault("prs", {}).setdefault(str(result["n"]), {})
        if not result.get("draft"):
            target.pop("draft", None)
        target.update(record)
    deskstate.update(repo, mutate)


def persist_explanation(repo, result, n, what_key):
    if result.get("n") != n or not isinstance(result.get("what"), str):
        raise ValueError("agent returned an invalid PR explanation")
    what = result["what"].strip()
    if not what:
        raise ValueError("agent returned an empty PR explanation")
    def mutate(state):
        record = state.setdefault("prs", {}).setdefault(str(n), {})
        record.update(what=what, what_key=what_key)
    deskstate.update(repo, mutate)


def persist_issue_analysis(repo, result, n):
    if result.get("n") != n:
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
    if result.get("status") not in ("done", "needs-input", "failed"):
        raise ValueError("agent returned an invalid operation status")
    if not isinstance(result.get("report"), str) or not result["report"].strip():
        raise ValueError("agent returned an empty operation report")
    if not isinstance(result.get("provider_changed"), bool):
        raise ValueError("agent returned an invalid provider change flag")
    return result


def persist_operation(repo, result, key, n=None):
    if n is not None:
        def mutate(state):
            order = state.setdefault("orders", {}).setdefault(str(n), {})
            order.update(status=result["status"], report=result["report"])
        deskstate.update(repo, mutate)
    if result["provider_changed"]:
        deskstate.request_provider_refresh(repo)


def _run(job_id, key, agent, repo, prompt, tools, timeout, cwd, schema,
         parser, persister, read_only):
    output_path = None
    started = time.monotonic()
    try:
        agent = resolve_agent(agent)
        _update(repo, job_id, agent=agent)
        _record_progress(
            repo, job_id,
            {"stage": "starting", "detail": "%s process starting" % agent}, 0)
        if agent == "codex":
            descriptor, output_path = tempfile.mkstemp(
                prefix="git-workflow-result-", suffix=".json")
            os.close(descriptor)
        cmd = command(agent, prompt, tools, cwd, output_path, schema, read_only)
        out = _execute(
            cmd, cwd, timeout, agent,
            lambda event, elapsed: _record_progress(
                repo, job_id, event, elapsed))
        if out.returncode:
            raise RuntimeError(out.stderr.strip()[:600] or
                               "%s exited %s" % (agent, out.returncode))
        result = parser(agent, out.stdout, output_path)
        persister(repo, result)
        status = result.get("status", "done") if isinstance(result, dict) else "done"
        _record_progress(
            repo, job_id,
            {"stage": status, "detail": "Job finished"},
            time.monotonic() - started)
        _update(repo, job_id, status=status, result=result, agent=agent)
    except Exception as exc:
        _record_progress(
            repo, job_id,
            {"stage": "error", "detail": _short(exc)},
            time.monotonic() - started)
        _update(repo, job_id, status="error", error=str(exc)[:600])
    finally:
        with _lock:
            _running.pop((repo, key), None)
        if output_path:
            try:
                Path(output_path).unlink()
            except OSError:
                pass


def _spawn(kind, key, agent, repo, request, prompt, tools, timeout, cwd,
           schema, parser, persister, read_only=True):
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
                           cwd, schema, parser, persister, read_only),
                     daemon=True).start()
    return job_id


def get(repo, job_id):
    path = job_path(repo, job_id)
    return safejson.read(path) if path.exists() else None


def active(repo):
    with _lock:
        ids = [job_id for (target, _), job_id in _running.items()
               if target == repo]
    return [record for record in (get(repo, job_id) for job_id in ids) if record]


def analyze_pr(repo, n, me, cwd, agent="auto", analysis_key=None):
    prompt = (
        "Read %s/skills/pr-analyze/SKILL.md and follow it exactly for PR #%s of %s "
        "(the user's login is %s). Work through gh only, read-only: never post, push, "
        "merge, edit or write any file. Your final message must be exactly the JSON "
        "object the skill specifies, with no fences and no prose around it."
        % (PLUGIN_ROOT, n, repo, me))
    parser = lambda selected, stdout, output: parse_result(  # noqa: E731
        selected, stdout, output, n)
    persister = lambda target, result: persist(  # noqa: E731
        target, result, analysis_key)
    return _spawn("analyze", "pr:%s:analyze" % n, agent, repo,
                  {"n": n, "analysis_key": analysis_key}, prompt,
                  READ_TOOLS, ANALYZE_TIMEOUT, cwd, SCHEMA, parser, persister)


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


def triage(repo, flow, rows_path, me, cwd, agent="auto"):
    exported = json.loads(Path(rows_path).read_text())
    skill = "pr-triage" if flow == "pr-triage" else "issue-triage"
    prompt = (
        "Read %s/skills/%s/SKILL.md and use the already fetched JSON at %s for "
        "%s (login %s). This is detached desk mode: do not write files, state, "
        "comments or provider data. Work only the model-owned items named by "
        "model_tasks or shortlist. Return exactly the JSON required by %s; use "
        "null and empty arrays for PR fields irrelevant to an item's task."
        % (PLUGIN_ROOT, skill, rows_path, repo, me, TRIAGE_SCHEMA))
    parser = lambda selected, stdout, output: parse_structured(  # noqa: E731
        selected, stdout, output)
    persister = lambda target, result: persist_triage(  # noqa: E731
        target, result, flow, exported)
    return _spawn("triage", flow, agent, repo,
                  {"flow": flow, "rows": str(rows_path)}, prompt,
                  TRIAGE_TOOLS, ANALYZE_TIMEOUT, cwd, TRIAGE_SCHEMA,
                  parser, persister)


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
        "Return exactly the JSON required by %s. provider_changed is true only "
        "if you actually changed GitHub or Forgejo."
        % (PLUGIN_ROOT, skill, repo, me, mandate, OPERATION_SCHEMA))
    persister = lambda target, result: persist_operation(  # noqa: E731
        target, result, "order:%s" % n if n is not None else flow, n)
    key = "order:%s" % n if n is not None else flow
    return _spawn("operation", key, agent, repo, dict(payload, flow=flow),
                  prompt, "", ANALYZE_TIMEOUT, cwd, OPERATION_SCHEMA,
                  parse_operation, persister, read_only=False)
