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

from providers import get_provider
from verdicts import decorate, issue_type

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
    def __init__(self, provider, repo, me):
        self.provider = provider
        self.repo = repo
        self.me = me
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
            rows = self.provider.queue(self.repo, self.me)
            return decorate(rows, self.me)
        return self._cached("queue", load, refresh)

    def issues(self, refresh=False):
        def load():
            rows = self.provider.issues(self.repo)
            for row in rows:
                row["type"] = issue_type(row["labels"], row["title"])
            return rows
        return self._cached("issues", load, refresh)


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
                                 "generated": time.strftime("%H:%M:%S")})
            elif url.path == "/api/queue":
                self._send(200, {"rows": self.desk.queue(refresh),
                                 "generated": time.strftime("%H:%M:%S")})
            elif url.path == "/api/issues":
                self._send(200, {"rows": self.desk.issues(refresh),
                                 "generated": time.strftime("%H:%M:%S")})
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
    args = parser.parse_args()

    provider = get_provider(args.provider)
    repo = args.repo or detect_repo()
    me = args.me or provider.whoami()

    Handler.desk = Desk(provider, repo, me)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    sys.stderr.write("review desk on http://127.0.0.1:%s  repo=%s me=%s provider=%s\n"
                     % (args.port, repo, me, provider.name))
    sys.stderr.flush()
    server.serve_forever()


if __name__ == "__main__":
    main()
