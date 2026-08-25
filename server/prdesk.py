"""Review desk — local dashboard server for the git-workflow plugin.

Zero dependencies beyond the Python standard library and the provider's own
CLI/API (gh for GitHub, a token for Forgejo). Read-only against the provider:
it renders the queue, it never posts, merges or edits anything.

Speed shape (the desk used to be slow for structural reasons, not slow code):

  * FETCH AND TRIAGE ARE SEPARATE. The desk no longer opens by asking the
    chat for a triage and staring at an empty grid for minutes. It boots,
    prefetches the provider in the background, and paints real rows in
    seconds. The triage is a button, and it runs on the JSON already
    downloaded (`/api/rows` writes it out for the skill to read).
  * ONE ROUND TRIP. `/api/desk` returns meta + queue + issues + state
    together, so first paint is a single request instead of four.
  * DISK CACHE, stale-while-revalidate (cache.py), so a restart repaints
    instantly and never blocks on GitHub.
  * MERGE STATE IS PHASE TWO. It is the one expensive field; the table
    paints without it and fills in when it lands.
  * HTTP/1.1 keep-alive + ETags, so the UI's polling costs one 304.

    python3 prdesk.py [--repo owner/repo] [--provider github|forgejo|fixture]
                      [--port 8399] [--me login] [--chat] [--triage-at-boot]
"""

import argparse
import copy
import hashlib
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import cache
import deskstate
import inbox
import jobs
from providers import get_provider
from verdicts import decorate, fallback_chase, handoff, issue_handoff, issue_type

STATIC = Path(__file__).resolve().parent / "static"


def detect_repo():
    out = subprocess.run(("git", "remote", "get-url", "origin"),
                         capture_output=True, text=True)
    if out.returncode:
        raise SystemExit("no --repo given and no git origin in the current directory")
    url = out.stdout.strip()
    tail = url.split(":")[-1] if url.startswith("git@") else urlparse(url).path.lstrip("/")
    return tail.removesuffix(".git")


class Desk:
    def __init__(self, provider, repo, me, cwd, chat=False, kind="pr"):
        self.provider = provider
        self.repo = repo
        self.me = me
        self.cwd = cwd
        self.chat = chat
        self.kind = kind
        self.timings = {}

    # ---- provider reads, all through the disk cache -------------------

    LOADERS = ("queue", "mergestates", "issues")

    def _timed(self, key, loader, refresh):
        t0 = time.time()
        data, age, source = cache.get(self.repo, key, loader, refresh)
        entry = {"ms": round((time.time() - t0) * 1000),
                 "age": round(age), "source": source}
        # the priming pass pays the cost; the assembling read that follows is
        # a cache hit and must not overwrite what the round actually cost
        if entry["ms"] >= self.timings.get(key, {}).get("ms", -1):
            self.timings[key] = entry
        return data

    def _raw_queue(self, refresh=False):
        return self._timed("queue", lambda: self.provider.queue(self.repo, self.me), refresh)

    def _mergestates(self, refresh=False):
        return self._timed("mergestates",
                           lambda: self.provider.mergestates(self.repo, self.me), refresh)

    def _raw_issues(self, refresh=False):
        return self._timed("issues", lambda: self.provider.issues(self.repo), refresh)

    def prime(self, refresh=False):
        """Pay every cache miss CONCURRENTLY. Assembling a snapshot reads
        three keys; done one after the other, three cold misses add up to
        more than the old desk's parallel fetch. GitHub answers them in
        parallel just fine — it is only concurrency PER SEARCH QUERY that it
        throttles."""
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(self._raw_queue, refresh),
                       pool.submit(self._mergestates, refresh),
                       pool.submit(self._raw_issues, refresh)]
            for future in futures:
                future.result()

    def prefetch(self):
        """Warm every key behind the browser, at boot. Merge state last: it
        is the slow one and the table is useful without it."""
        cache.warm(self.repo, "queue", lambda: self.provider.queue(self.repo, self.me))
        cache.warm(self.repo, "issues", lambda: self.provider.issues(self.repo))
        cache.warm(self.repo, "mergestates",
                   lambda: self.provider.mergestates(self.repo, self.me))

    # ---- assembled views ---------------------------------------------

    def queue(self, refresh=False):
        raw = self._raw_queue(refresh)
        rows = [dict(row) for row in raw["rows"]]
        states = self._mergestates(refresh) or {}
        for row in rows:
            if row.get("merge") is None:
                row["merge"] = states.get(str(row["n"]))
        decorate(rows, self.me)
        for row in rows:
            row["action"] = handoff(row, self.repo,
                                    self.provider.merge_command(self.repo, row["n"]))
        state = deskstate.load(self.repo)
        deskstate.annotate_prs(rows, state)
        orders = state.get("orders") or {}
        for row in rows:
            row["order"] = orders.get(str(row["n"]))
        chase = state.get("chase") or fallback_chase(rows)
        return {"rows": rows, "total": raw.get("total", len(rows)),
                "truncated": raw.get("truncated", False),
                "mergestate_pending": not states,
                "chase": chase,
                "chase_verified": bool(state.get("chase")),
                "session": state.get("session"),
                "grid": state.get("grid"),
                "situa": state.get("situa")}

    def issues(self, refresh=False):
        rows = [dict(row) for row in self._raw_issues(refresh)]
        for row in rows:
            row["type"] = issue_type(row["labels"], row["title"])
            row["action"] = issue_handoff(row, self.repo)
        deskstate.annotate_issues(rows, deskstate.load(self.repo))
        return {"rows": rows}

    def live_state(self):
        st = deskstate.load(self.repo)
        age = deskstate.watcher_age(self.repo)
        sp = deskstate.state_path(self.repo)
        busy = sp.exists() and (time.time() - sp.stat().st_mtime) < 180
        return {"feed": (st.get("feed") or [])[-50:],
                "grid": st.get("grid"), "situa": st.get("situa"),
                "chase": st.get("chase") or {},
                "session": st.get("session"), "pong": st.get("pong"),
                "watcher": {"alive": self.chat and age is not None and age < 10,
                            "age": age, "chat": self.chat, "busy": bool(busy)}}

    def snapshot(self, refresh=False):
        """Everything the UI needs, in one response."""
        self.timings = {}
        self.prime(refresh)
        # prime() already paid the refresh; these two read the warm cache
        queue = self.queue()
        issues = self.issues(refresh)
        return {"meta": {"repo": self.repo, "me": self.me,
                         "provider": self.provider.name,
                         "chat": self.chat, "desk": self.kind},
                "queue": queue, "issues": issues, "state": self.live_state(),
                "timings": dict(self.timings),
                "generated": time.strftime("%H:%M:%S")}

    def export_rows(self):
        """Write the downloaded queue where the triage skill can read it, so
        the triage costs no provider round trip of its own."""
        payload = {"repo": self.repo, "me": self.me,
                   "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "queue": self.queue()["rows"], "issues": self.issues()["rows"]}
        path = deskstate.STATE_DIR / ("%s__rows.json" % self.repo.replace("/", "__"))
        deskstate.STATE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=1))
        return path


class Handler(BaseHTTPRequestHandler):
    desk = None
    protocol_version = "HTTP/1.1"      # keep-alive: the UI polls every few seconds

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", etag=None):
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        if etag:
            self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(payload)

    VOLATILE = ("generated", "timings")

    def _send_tagged(self, body):
        """304 when the caller already has this payload — the UI polls a few
        times a minute and most polls change nothing. Anything that moves on
        every request is stripped from the tag first, or it would never
        match: the clock, the timings, and the watcher's age in seconds."""
        payload = json.dumps(body).encode()
        stable = copy.deepcopy({k: v for k, v in body.items()
                                if k not in self.VOLATILE})
        for holder in (stable, stable.get("state") or {}):
            watcher = holder.get("watcher")
            if isinstance(watcher, dict) and "age" in watcher:
                holder["watcher"] = dict(watcher, age=None)
        etag = '"%s"' % hashlib.sha1(json.dumps(stable, sort_keys=True, default=str
                                                ).encode()).hexdigest()[:16]
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send(200, payload, etag=etag)

    def do_GET(self):
        url = urlparse(self.path)
        refresh = "refresh" in parse_qs(url.query)
        try:
            if url.path in ("/", "/index.html"):
                self._send(200, (STATIC / "index.html").read_bytes(),
                           "text/html; charset=utf-8")
            elif url.path == "/api/desk":
                self._send_tagged(self.desk.snapshot(refresh))
            elif url.path == "/api/meta":
                self._send(200, {"repo": self.desk.repo, "me": self.desk.me,
                                 "provider": self.desk.provider.name,
                                 "chat": self.desk.chat, "desk": self.desk.kind,
                                 "generated": time.strftime("%H:%M:%S")})
            elif url.path == "/api/queue":
                self._send_tagged(dict(self.desk.queue(refresh),
                                       generated=time.strftime("%H:%M:%S")))
            elif url.path == "/api/issues":
                self._send_tagged(dict(self.desk.issues(refresh),
                                       generated=time.strftime("%H:%M:%S")))
            elif url.path == "/api/feed":
                feed = deskstate.load(self.desk.repo).get("feed") or []
                self._send(200, {"feed": feed[-50:]})
            elif url.path == "/api/state":
                self._send_tagged(self.desk.live_state())
            elif url.path == "/api/selftest":
                self._send(200, self._selftest())
            elif url.path.startswith("/api/job/"):
                job = jobs.get(url.path.rsplit("/", 1)[1])
                self._send(200 if job else 404, job or {"error": "unknown job"})
            else:
                self._send(404, {"error": "not found"})
        except Exception as exc:
            self._send(502, {"error": str(exc)})

    def _selftest(self):
        out = {}
        try:
            out["provider"] = {"ok": True, "login": self.desk.provider.whoami()}
        except Exception as exc:
            out["provider"] = {"ok": False, "error": str(exc)[:200]}
        try:
            st = deskstate.load(self.desk.repo)
            deskstate.save(self.desk.repo, st)
            out["state_file"] = {"ok": True,
                                 "path": str(deskstate.state_path(self.desk.repo))}
        except Exception as exc:
            out["state_file"] = {"ok": False, "error": str(exc)[:200]}
        hit = cache.peek(self.desk.repo, "queue")
        out["cache"] = {"ok": bool(hit), "age": round(hit[0]) if hit else None,
                        "path": str(cache.cache_path(self.desk.repo))}
        age = deskstate.watcher_age(self.desk.repo)
        out["watcher"] = {"ok": self.desk.chat and age is not None and age < 10,
                          "age": age, "chat": self.desk.chat}
        return out

    def do_POST(self):
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        try:
            parts = url.path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "pr"] and parts[3] == "analyze":
                self._analyze_pr(int(parts[2]))
            elif len(parts) == 4 and parts[:2] == ["api", "pr"] and parts[3] == "order":
                n = int(parts[2])
                order = deskstate.add_order(self.desk.repo, n, body.get("propose", ""),
                                            body.get("draft"), body.get("instruction", ""))
                if self.desk.chat:
                    inbox.push(self.desk.repo, {"kind": "order", "n": n})
                self._send(200, {"order": order, "queued": self.desk.chat})
            elif len(parts) == 4 and parts[:2] == ["api", "issue"] and parts[3] == "analyze":
                self._chat_only({"kind": "issue-analyze", "n": int(parts[2])},
                                "issue-analyze needs the desk launched from a chat session")
            elif parts == ["api", "fetch"]:
                # explicit re-read of the provider, on the caller's demand
                self._send(200, dict(self.desk.snapshot(refresh=True), refetched=True))
            elif parts == ["api", "rows"]:
                path = self.desk.export_rows()
                self._send(200, {"path": str(path)})
            elif parts == ["api", "shutdown"]:
                if self.desk.chat:
                    inbox.push(self.desk.repo, {"kind": "shutdown"})
                self._send(200, {"bye": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            elif parts == ["api", "run"]:
                self._chat_only({"kind": "run", "flow": body.get("flow", "pr-run")},
                                "run needs the desk launched from a chat session")
            elif parts == ["api", "ping"]:
                self._chat_only({"kind": "ping", "token": body.get("token") or ""},
                                "il ping ha senso solo in modalità chat")
            elif parts == ["api", "triage"]:
                # the triage reads the JSON the desk already downloaded
                path = self.desk.export_rows()
                self._chat_only({"kind": "triage", "flow": body.get("flow", "pr-triage"),
                                 "rows": str(path)},
                                "triage needs the desk launched from a chat session")
            else:
                self._send(404, {"error": "not found"})
        except Exception as exc:
            self._send(502, {"error": str(exc)})

    def _chat_only(self, event, complaint):
        if not self.desk.chat:
            self._send(409, {"error": complaint})
            return
        inbox.push(self.desk.repo, event)
        self._send(202, {"queued": True})

    def _analyze_pr(self, n):
        if self.desk.chat:
            inbox.push(self.desk.repo, {"kind": "analyze", "n": n})
            self._send(202, {"queued": True})
        else:
            job_id = jobs.analyze_pr(self.desk.repo, n, self.desk.me, self.desk.cwd)
            self._send(202, {"job": job_id})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="owner/repo (default: origin of the cwd)")
    parser.add_argument("--provider", default="github",
                        choices=("github", "forgejo", "fixture"))
    parser.add_argument("--desk", default="pr", choices=("pr", "issue"),
                        help="which desk this server is: pr (default) or issue")
    parser.add_argument("--port", type=int, default=0,
                        help="default: 8399 for the pr desk, 8398 for the issue desk")
    parser.add_argument("--me", help="login to triage for (default: the authenticated user)")
    parser.add_argument("--chat", action="store_true",
                        help="attached-chat mode: buttons enqueue events for the "
                             "Claude session that launched the desk, instead of "
                             "spawning headless runs")
    parser.add_argument("--keep-state", action="store_true",
                        help="keep the previous session's analyses/feed instead "
                             "of starting empty")
    parser.add_argument("--triage-at-boot", action="store_true",
                        help="ask the chat for a triage as soon as the desk starts "
                             "(off by default: the desk fetches first and the "
                             "triage is a button on the downloaded rows)")
    parser.add_argument("--no-prefetch", action="store_true",
                        help="do not warm the provider cache at boot")
    args = parser.parse_args()

    provider = get_provider(args.provider)
    repo = args.repo or detect_repo()
    me = args.me or provider.whoami()
    port = args.port or (8399 if args.desk == "pr" else 8398)
    if not args.keep_state:
        deskstate.reset(repo)

    desk = Desk(provider, repo, me, str(Path.cwd()), chat=args.chat, kind=args.desk)
    Handler.desk = desk
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    if not args.no_prefetch:
        desk.prefetch()
    if args.chat and args.triage_at_boot:
        flow = "pr-triage" if args.desk == "pr" else "issue-triage"
        inbox.push(repo, {"kind": "triage", "flow": flow})
    sys.stderr.write("%s desk on http://127.0.0.1:%s  repo=%s me=%s provider=%s\n"
                     % (args.desk, port, repo, me, provider.name))
    sys.stderr.flush()
    server.serve_forever()


if __name__ == "__main__":
    main()
