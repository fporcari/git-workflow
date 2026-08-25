"""Review desk — local dashboard server for the git-workflow plugin.

Zero dependencies beyond the Python standard library and the provider's own
CLI/API (gh for GitHub, a token for Forgejo). Read-only: it renders the queue,
it never posts, merges or edits anything.

    python3 prdesk.py [--repo owner/repo] [--provider github|forgejo]
                      [--port 8399] [--me login]
"""

import argparse
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import deskstate
import inbox
import jobs
from providers import get_provider
from verdicts import decorate, fallback_chase, handoff, issue_handoff, issue_type

STATIC = Path(__file__).resolve().parent / "static"
CACHE_TTL = 120


def detect_repo():
    out = subprocess.run(("git", "remote", "get-url", "origin"),
                         capture_output=True, text=True)
    if out.returncode:
        raise SystemExit("no --repo given and no git origin in the current directory")
    url = out.stdout.strip()
    tail = url.split(":")[-1] if url.startswith("git@") else urlparse(url).path.lstrip("/")
    return tail.removesuffix(".git")


class Desk:
    def __init__(self, provider, repo, me, cwd, chat=False):
        self.provider = provider
        self.repo = repo
        self.me = me
        self.cwd = cwd
        self.chat = chat
        self._cache = {}
        self._lock = threading.Lock()

    def _cached(self, key, loader, refresh):
        with self._lock:
            hit = self._cache.get(key)
            if hit and not refresh and time.time() - hit[0] < CACHE_TTL:
                return hit[1]
        data = loader()
        with self._lock:
            self._cache[key] = (time.time(), data)
        return data

    def queue(self, refresh=False):
        def load():
            rows = decorate(self.provider.queue(self.repo, self.me), self.me)
            for row in rows:
                row["action"] = handoff(row, self.repo,
                                        self.provider.merge_command(self.repo, row["n"]))
            return rows
        rows = self._cached("queue", load, refresh)
        state = deskstate.load(self.repo)
        deskstate.annotate_prs(rows, state)
        orders = state.get("orders") or {}
        for row in rows:
            row["order"] = orders.get(str(row["n"]))
        chase = state.get("chase") or fallback_chase(rows)
        return {"rows": rows, "chase": chase,
                "chase_verified": bool(state.get("chase")),
                "session": state.get("session")}

    def issues(self, refresh=False):
        def load():
            rows = self.provider.issues(self.repo)
            for row in rows:
                row["type"] = issue_type(row["labels"], row["title"])
                row["action"] = issue_handoff(row, self.repo)
            return rows
        rows = self._cached("issues", load, refresh)
        deskstate.annotate_issues(rows, deskstate.load(self.repo))
        return {"rows": rows}


class Handler(BaseHTTPRequestHandler):
    desk = None

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        url = urlparse(self.path)
        refresh = "refresh" in parse_qs(url.query)
        try:
            if url.path in ("/", "/index.html"):
                self._send(200, (STATIC / "index.html").read_bytes(),
                           "text/html; charset=utf-8")
            elif url.path == "/api/meta":
                self._send(200, {"repo": self.desk.repo, "me": self.desk.me,
                                 "provider": self.desk.provider.name,
                                 "chat": self.desk.chat,
                                 "generated": time.strftime("%H:%M:%S")})
            elif url.path == "/api/queue":
                self._send(200, dict(self.desk.queue(refresh),
                                     generated=time.strftime("%H:%M:%S")))
            elif url.path == "/api/issues":
                self._send(200, dict(self.desk.issues(refresh),
                                     generated=time.strftime("%H:%M:%S")))
            elif url.path.startswith("/api/job/"):
                job = jobs.get(url.path.rsplit("/", 1)[1])
                self._send(200 if job else 404, job or {"error": "unknown job"})
            else:
                self._send(404, {"error": "not found"})
        except Exception as exc:
            self._send(502, {"error": str(exc)})

    def do_POST(self):
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        try:
            parts = url.path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "pr"] and parts[3] == "analyze":
                n = int(parts[2])
                if self.desk.chat:
                    inbox.push(self.desk.repo, {"kind": "analyze", "n": n})
                    self._send(202, {"queued": True})
                else:
                    job_id = jobs.analyze_pr(self.desk.repo, n, self.desk.me,
                                             self.desk.cwd)
                    self._send(202, {"job": job_id})
            elif len(parts) == 4 and parts[:2] == ["api", "pr"] and parts[3] == "order":
                n = int(parts[2])
                order = deskstate.add_order(self.desk.repo, n,
                                            body.get("propose", ""),
                                            body.get("draft"),
                                            body.get("instruction", ""))
                if self.desk.chat:
                    inbox.push(self.desk.repo, {"kind": "order", "n": n})
                self._send(200, {"order": order, "queued": self.desk.chat})
            else:
                self._send(404, {"error": "not found"})
        except Exception as exc:
            self._send(502, {"error": str(exc)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="owner/repo (default: origin of the cwd)")
    parser.add_argument("--provider", default="github", choices=("github", "forgejo"))
    parser.add_argument("--port", type=int, default=8399)
    parser.add_argument("--me", help="login to triage for (default: the authenticated user)")
    parser.add_argument("--chat", action="store_true",
                        help="attached-chat mode: buttons enqueue events for the "
                             "Claude session that launched the desk, instead of "
                             "spawning headless runs")
    args = parser.parse_args()

    provider = get_provider(args.provider)
    repo = args.repo or detect_repo()
    me = args.me or provider.whoami()

    Handler.desk = Desk(provider, repo, me, str(Path.cwd()), chat=args.chat)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    sys.stderr.write("review desk on http://127.0.0.1:%s  repo=%s me=%s provider=%s\n"
                     % (args.port, repo, me, provider.name))
    sys.stderr.flush()
    server.serve_forever()


if __name__ == "__main__":
    main()
