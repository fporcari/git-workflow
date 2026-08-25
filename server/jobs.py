"""Headless Claude jobs for the review desk — the Analyze button.

The desk's Analyze button spawns a read-only `claude -p` run following the
pr-analyze skill: it reads the PR through gh, verifies the description's
claims, and returns the pr-loop Lane B block (what / history / propose).

Execution of the block's proposal is NOT spawned from here: the go-ahead is
recorded as a pending order in the desk state file, and a chat session (or
/pr-loop) picks it up. See orders() below.
"""

import json
import subprocess
import threading
import uuid
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
READ_TOOLS = ("Bash(gh pr view:*),Bash(gh pr diff:*),Bash(gh pr checks:*),"
              "Bash(gh api:*),Bash(gh issue view:*),Bash(gh search:*),"
              "Read,Grep,Glob,Write")
ANALYZE_TIMEOUT = 900

_jobs = {}
_lock = threading.Lock()


def _run(job_id, prompt, tools, timeout, cwd):
    cmd = ["claude", "-p", prompt, "--output-format", "json",
           "--allowedTools", tools]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout, cwd=cwd)
        if out.returncode:
            raise RuntimeError(out.stderr.strip()[:600] or "claude exited %s" % out.returncode)
        payload = json.loads(out.stdout)
        result = payload.get("result", "")
        with _lock:
            _jobs[job_id].update(status="done", result=result,
                                 cost=payload.get("total_cost_usd"))
    except Exception as exc:
        with _lock:
            _jobs[job_id].update(status="error", error=str(exc)[:600])


def _spawn(kind, key, prompt, tools, timeout, cwd):
    with _lock:
        for job in _jobs.values():
            if job["key"] == key and job["status"] == "running":
                return job["id"]
        job_id = uuid.uuid4().hex[:12]
        _jobs[job_id] = {"id": job_id, "kind": kind, "key": key, "status": "running"}
    threading.Thread(target=_run, args=(job_id, prompt, tools, timeout, cwd),
                     daemon=True).start()
    return job_id


def get(job_id):
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def analyze_pr(repo, n, me, cwd):
    prompt = (
        "Read %s/skills/pr-analyze/SKILL.md and follow it exactly for PR #%s of %s "
        "(the user's login is %s). Work through gh only, read-only: never post, push, "
        "merge or edit anything on the PR. The only file you may write is the desk "
        "state file the skill names. Your final message must be exactly the JSON "
        "object the skill specifies, with no fences and no prose around it."
        % (PLUGIN_ROOT, n, repo, me))
    return _spawn("analyze", "pr:%s:analyze" % n, prompt, READ_TOOLS,
                  ANALYZE_TIMEOUT, cwd)
