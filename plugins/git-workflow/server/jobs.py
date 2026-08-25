"""Read-only headless agent jobs for the review desk Analyze button.

The desk spawns Claude Code or Codex in a read-only sandbox, then persists the
validated Lane B result itself. The agent never writes desk state.

Execution of the block's proposal is NOT spawned from here: the go-ahead is
recorded as a pending order in the desk state file, and a chat session running
pr-loop picks it up.
"""

import json
import os
import shutil
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path

import deskstate

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
READ_TOOLS = ("Bash(gh pr view:*),Bash(gh pr diff:*),Bash(gh pr checks:*),"
              "Bash(gh api:*),Bash(gh issue view:*),Bash(gh search:*),"
              "Read,Grep,Glob")
ANALYZE_TIMEOUT = 900
SCHEMA = PLUGIN_ROOT / "server" / "schemas" / "pr-analysis.json"

_jobs = {}
_lock = threading.Lock()


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


def command(agent, prompt, tools, cwd, output_path=None):
    if agent == "claude":
        return ["claude", "-p", prompt, "--output-format", "json",
                "--json-schema", SCHEMA.read_text(), "--allowedTools", tools,
                "--no-session-persistence"]
    return ["codex", "exec", "--ephemeral", "--sandbox", "read-only",
            "-C", cwd, "--output-schema", str(SCHEMA),
            "--output-last-message", output_path, prompt]


def parse_result(agent, stdout, output_path=None, expected_n=None):
    raw = stdout
    if agent == "claude":
        envelope = json.loads(stdout)
        raw = envelope.get("structured_output") or envelope.get("result") or ""
    elif output_path:
        raw = Path(output_path).read_text()
    result = raw if isinstance(raw, dict) else json.loads(raw)
    required = {"n", "what", "history", "propose", "draft", "verified",
                "not_verified"}
    if not isinstance(result, dict) or not required <= set(result):
        raise ValueError("agent returned an invalid PR analysis")
    if expected_n is not None and result["n"] != expected_n:
        raise ValueError("agent returned analysis for PR #%s, expected #%s"
                         % (result["n"], expected_n))
    if not all(isinstance(result[key], str)
               for key in ("what", "history", "propose")):
        raise ValueError("agent returned non-text analysis fields")
    if not all(isinstance(result[key], list)
               for key in ("verified", "not_verified")):
        raise ValueError("agent returned invalid verification lists")
    return result


def persist(repo, result):
    def mutate(state):
        record = {"analysis": "%s\n%s" % (result["what"], result["history"]),
                  "next": result["propose"],
                  "verified": result["verified"],
                  "not_verified": result["not_verified"]}
        if result.get("draft"):
            record["draft"] = result["draft"]
        state.setdefault("prs", {})[str(result["n"])] = record
    deskstate.update(repo, mutate)


def _run(job_id, agent, repo, n, prompt, tools, timeout, cwd):
    output_path = None
    try:
        agent = resolve_agent(agent)
        if agent == "codex":
            descriptor, output_path = tempfile.mkstemp(
                prefix="git-workflow-analysis-", suffix=".json")
            os.close(descriptor)
        cmd = command(agent, prompt, tools, cwd, output_path)
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout, cwd=cwd)
        if out.returncode:
            raise RuntimeError(out.stderr.strip()[:600] or
                               "%s exited %s" % (agent, out.returncode))
        result = parse_result(agent, out.stdout, output_path, n)
        persist(repo, result)
        with _lock:
            _jobs[job_id].update(status="done", result=result, agent=agent)
    except Exception as exc:
        with _lock:
            _jobs[job_id].update(status="error", error=str(exc)[:600])
    finally:
        if output_path:
            try:
                Path(output_path).unlink()
            except OSError:
                pass


def _spawn(kind, key, agent, repo, n, prompt, tools, timeout, cwd):
    with _lock:
        for job in _jobs.values():
            if job["key"] == key and job["status"] == "running":
                return job["id"]
        job_id = uuid.uuid4().hex[:12]
        _jobs[job_id] = {"id": job_id, "kind": kind, "key": key, "status": "running"}
    threading.Thread(target=_run,
                     args=(job_id, agent, repo, n, prompt, tools, timeout, cwd),
                     daemon=True).start()
    return job_id


def get(job_id):
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def analyze_pr(repo, n, me, cwd, agent="auto"):
    prompt = (
        "Read %s/skills/pr-analyze/SKILL.md and follow it exactly for PR #%s of %s "
        "(the user's login is %s). Work through gh only, read-only: never post, push, "
        "merge, edit or write any file. Your final message must be exactly the JSON "
        "object the skill specifies, with no fences and no prose around it."
        % (PLUGIN_ROOT, n, repo, me))
    return _spawn("analyze", "pr:%s:analyze" % n, agent, repo, n, prompt,
                  READ_TOOLS, ANALYZE_TIMEOUT, cwd)
