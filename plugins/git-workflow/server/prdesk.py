"""Review desk — local dashboard server for the git-workflow plugin.

Zero dependencies beyond the Python standard library and the provider's own
CLI/API (gh for GitHub, a token for Forgejo). Normal reads are local/provider
facts only. Explicit action buttons may start one ephemeral Codex or Claude
process; the Python server remains the sole writer of desk state.

Speed shape (the desk used to be slow for structural reasons, not slow code):

  * FETCH AND TRIAGE ARE SEPARATE. Boot and reload fetch provider facts and
    paint real rows. The explicit triage button runs on that downloaded JSON;
    each result is tied to a fingerprint, so a changed PR becomes stale rather
    than silently keeping yesterday's verdict.
  * ONE ROUND TRIP. `/api/desk` returns meta + queue + issues + state
    together, so first paint is a single request instead of four.
  * A SESSION CACHE, stale-while-revalidate (cache.py). Launching a desk
    clears it — starting the desk is a request for the truth now, not for
    yesterday's queue — and what it buys is everything that happens while
    the desk is up: a browser reload, the UI's polling, a second tab, the
    sibling desk on the same repo. The boot fetch is paid in the background
    before the browser is even open.
  * MERGE STATE IS PHASE TWO. It is the one expensive field; the table
    paints without it and fills in when it lands.
  * HTTP/1.1 keep-alive + ETags, so the UI's polling costs one 304.
  * THE TRIAGE IS COMPUTED AND PUBLISHED HERE. When the user presses
    pr-triage the desk computes the verdicts, the five blocks and the chase
    (verdicts.py, 0.07 ms for 52 PRs) and writes them to the state file
    itself. The model is asked only for what a field cannot answer, one PR at
    a time. Nothing of this runs at boot: a computed grid is not a triage
    until he asks for one.
  * THE MERGE GATE IS READ, not guessed (gate.py). Where a base restricts
    who may push, "approved + CLEAN + mine" is not the user's merge — the
    old field-only verdict said `A1 → merge it` there and was wrong.

    python3 prdesk.py [--repo owner/repo] [--provider github|forgejo|fixture]
                      [--port 8399] [--me login] [--agent auto|codex|claude]
"""

import argparse
import copy
import hashlib
import json
import secrets
import signal
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
import gate as gatelib
import issuecheck
import jobs
import notify
import verdicts
from providers import get_provider
from verdicts import decorate, handoff, issue_handoff, issue_type

STATIC = Path(__file__).resolve().parent / "static"

# one decision group holds four options: a wider batch becomes hard to scan
MAX_BATCH = 4


def detect_repo():
    out = subprocess.run(("git", "remote", "get-url", "origin"),
                         capture_output=True, text=True)
    if out.returncode:
        raise SystemExit("no --repo given and no git origin in the current directory")
    url = out.stdout.strip()
    tail = url.split(":")[-1] if url.startswith("git@") else urlparse(url).path.lstrip("/")
    return tail.removesuffix(".git")


# the fields a verdict is a function of, and nothing else: hashing the whole
# row made a title edit or a body edit expire a triage that would not change
VERDICT_FIELDS = ("author", "draft", "base", "head", "merge", "decision",
                  "req", "reviews", "unresolved", "incomplete", "assignees",
                  "conflict_kind", "last")
GATE_FIELDS = ("branch", "protected", "can_land", "landers", "approvals",
               "codeowners_required", "owners", "per_path", "dismiss_stale",
               "conversation_resolution")


def triage_key(row, gate):
    """Fingerprint the provider facts THIS row's verdict reads."""
    payload = [[row.get(f) for f in VERDICT_FIELDS],
               [(gate or {}).get(f) for f in GATE_FIELDS]]
    return fingerprint(payload)


def fingerprint(payload):
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                     default=str).encode()
    return hashlib.sha256(raw).hexdigest()[:20]


def model_keys(row, gate=None, gate_known=True):
    """Independent validity keys for the three facts a model can add.

    A title edit does not stale a conflict reading, and a new review does not
    stale a one-line explanation. Keeping one fingerprint for all three was
    the source of both wasted reads and stale facts surviving where they
    mattered.
    """
    explained_issues = [
        {"issue": item.get("issue"), "title": item.get("title")}
        for item in row.get("closes") or []]
    problem_key = fingerprint([
        row.get("author"), row.get("head"), row.get("summary"),
        explained_issues])
    history_fields = ("draft", "base", "base_head", "head", "merge",
                      "decision", "req", "reviews", "unresolved", "incomplete",
                      "assignees", "last")
    history_key = (fingerprint(
        [[row.get(f) for f in history_fields],
         [(gate or {}).get(f) for f in GATE_FIELDS]])
        if gate_known else None)
    analysis_key = (fingerprint([problem_key, history_key])
                    if history_key else None)
    return {
        "what": fingerprint([row.get("title"), row.get("summary"),
                             explained_issues]),
        "problem": problem_key,
        "history": history_key,
        "analysis": analysis_key,
        "conflict": fingerprint([row.get("base"), row.get("base_head"),
                                 row.get("head"), row.get("merge")]),
    }


def row_number(row):
    """The PR number of a stored grid row, or None. The state file is written
    by a session that can die mid-write, so a malformed row costs that row and
    never the whole desk."""
    try:
        return int(row["n"])
    except (TypeError, ValueError, KeyError):
        return None


def triage_records(grid):
    """The published verdicts, by PR number."""
    return {row_number(row): row
            for block in (grid or {}).get("blocks", [])
            for row in block.get("rows", []) or []
            if row_number(row) is not None}


def model_tasks(rows, notes, me):
    """The exact model-owned artifacts that are absent or out of date."""
    out = {}
    for row in rows:
        note = (notes or {}).get(str(row["n"])) or {}
        keys = row["model_keys"]
        tasks = []
        if row.get("author") == me and row.get("merge") == "DIRTY":
            if note.get("conflict_key") != keys["conflict"]:
                tasks.append("conflict")
        elif row.get("autorun") == "asks":
            if note.get("analysis_key") != keys["analysis"]:
                tasks.append("analysis")
        if tasks:
            out[str(row["n"])] = tasks
    return out


class Desk:
    def __init__(self, provider, repo, me, cwd, chat=False, kind="pr", agent="auto"):
        self.provider = provider
        self.repo = repo
        self.me = me
        self.cwd = cwd
        self.chat = chat
        self.kind = kind
        self.agent = agent
        self.write_token = secrets.token_urlsafe(32)
        self.timings = {}
        self._default = None

    # ---- provider reads, all through the disk cache -------------------

    LOADERS = ("queue", "mergestates", "issues")

    def _timed(self, key, loader, refresh):
        t0 = time.time()
        data, age, source = cache.get(self.repo, key, loader, refresh)
        entry = {"ms": round((time.time() - t0) * 1000),
                 "age": round(age), "source": source,
                 "stamp": time.strftime("%Y-%m-%dT%H:%M:%S",
                                        time.localtime(time.time() - age))}
        # the priming pass pays the cost; the assembling read that follows is
        # a cache hit and must not overwrite what the round actually cost
        if entry["ms"] >= self.timings.get(key, {}).get("ms", -1):
            self.timings[key] = entry
        return data

    def _raw_queue(self, refresh=False):
        return self._timed("queue", lambda: self.provider.queue(self.repo, self.me), refresh)

    def _membership(self, refresh=False):
        return self._timed(
            "membership",
            lambda: self.provider.open_numbers(self.repo, self.me), refresh)

    def _mergestates(self, refresh=False):
        return self._timed("mergestates",
                           lambda: self.provider.mergestates(self.repo, self.me), refresh)

    def _raw_issues(self, refresh=False):
        return self._timed("issues", lambda: self.provider.issues(self.repo), refresh)

    def _gate(self, branch, refresh=False):
        """One base branch's gate, cached under its own key.

        Per branch, not per queue: the gate of a base is the same fact no
        matter which PRs happen to target it today, and keying it per branch
        is what lets the default branch be warm before the queue has even
        landed. A base whose gate is not in yet simply has no notes for a
        beat — the verdict falls back to its field-only reading and corrects
        itself, exactly as the merge state does."""
        return self._timed("gate:%s" % branch,
                           lambda: self.provider.gates(
                               self.repo, self.me, [branch]).get(branch), refresh)

    def _gates(self, bases, refresh=False):
        """Whatever is already known, warming the rest behind the caller."""
        gates = {}
        for branch in sorted({b for b in bases if b}):
            hit = cache.peek(self.repo, "gate:%s" % branch)
            if hit and not refresh:
                if hit[1]:
                    gates[branch] = hit[1]
                continue
            cache.warm(self.repo, "gate:%s" % branch,
                       lambda b=branch: self.provider.gates(
                           self.repo, self.me, [b]).get(b))
        return gates

    def _all_gates(self, bases, refresh=False):
        """Every base's gate, waited for — the triage export cannot leave one
        out. Concurrently: they are independent API reads."""
        bases = sorted({b for b in bases if b})
        if not bases:
            return {}
        with ThreadPoolExecutor(max_workers=min(len(bases), 8)) as pool:
            got = list(pool.map(lambda b: (b, self._gate(b, refresh)), bases))
        return {branch: gate for branch, gate in got if gate}

    def _relations(self, refresh=False):
        """Who commented where, and every remote branch — neither needs the
        queue, so both belong in the first wave."""
        return self._timed(
            "relations",
            lambda: {"relations": self.provider.issue_relations(self.repo, self.me),
                     "branches": self.provider.remote_branches(self.cwd)},
            refresh)

    def _crosscheck(self, queue_rows, refresh=False):
        got = self._relations(refresh)
        return issuecheck.collect(got["relations"], got["branches"], queue_rows)

    def prime(self, refresh=False):
        """Pay every cache miss CONCURRENTLY, in two waves.

        Assembling a snapshot reads five keys; done one after the other the
        cold misses add up to more than the old desk's parallel fetch. GitHub
        answers concurrent requests fine — it is only concurrency PER SEARCH
        QUERY that it throttles.

        Everything here is INDEPENDENT — nothing waits on the queue. Two
        serial waves would add up (measured: 4.9s + 5.2s = 10.2s), so the one
        thing that genuinely needs the queue, the gate of a base nobody has
        seen before, is left to fill in behind the paint like the merge state
        does. The default branch is primed here because it is almost always
        the only base that matters.
        """
        jobs = [lambda: self._raw_queue(refresh),
                lambda: self._membership(True),
                lambda: self._raw_issues(refresh),
                lambda: self._mergestates(refresh),
                lambda: self._relations(refresh),
                lambda: self._gate(self.default_branch(), refresh)]
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            for future in [pool.submit(job) for job in jobs]:
                try:
                    future.result()
                except Exception:
                    pass          # a gate or a cross-check that fails is a
                                  # missing annotation, never a blank desk

    def default_branch(self):
        if self._default is None:
            try:
                self._default = self.provider.default_branch(self.repo)
            except Exception:
                self._default = "main"
        return self._default

    def prefetch(self):
        """Warm every key behind the browser, at boot, in one background
        thread that runs the same two waves prime() does."""
        threading.Thread(target=self._prefetch_quietly, daemon=True).start()

    def _prefetch_quietly(self):
        try:
            self.prime()
        except Exception:
            pass

    # ---- assembled views ---------------------------------------------

    def _queue_facts(self, refresh=False, complete_gates=False, state=None):
        raw = self._raw_queue(refresh)
        membership = set(self._membership(refresh))
        known = {row["n"] for row in raw["rows"]}
        if membership - known:
            raw = self._raw_queue(True)
        rows = [dict(row) for row in raw["rows"]]
        rows = [row for row in rows if row["n"] in membership]
        raw = dict(raw, total=min(raw.get("total", len(rows)), len(membership)))
        states = self._mergestates(refresh) or {}
        notes = (state if state is not None else deskstate.load(self.repo))
        notes = notes.get("prs") or {}
        for row in rows:
            if row.get("merge") is None:
                row["merge"] = states.get(str(row["n"]))
            note = notes.get(str(row["n"])) or {}
            conflict_key = model_keys(row)["conflict"]
            row["conflict_kind"] = (
                note.get("conflict_kind")
                if note.get("conflict_key") == conflict_key else None)
        bases = sorted({row.get("base") for row in rows if row.get("base")})
        gates = (self._all_gates(bases, refresh) if complete_gates
                 else self._gates(bases, refresh)) or {}
        for row in rows:
            gate = gates.get(row.get("base"))
            row["gate"] = gatelib.notes(gate)
            row["triage_key"] = triage_key(row, gate)
            row["model_keys"] = model_keys(
                row, gate, complete_gates or row.get("base") in gates)
        return rows, raw, states, gates

    def _apply_triage(self, rows, grid, gates, notes=None):
        """Live verdicts on the rows a triage or an analyze has seen.

        The grid has been pure engine output since 0.17 — nothing in it is a
        model's to protect — so a published row whose provider facts moved is
        RE-VERDICTED here (decorate, 0.07 ms) instead of expiring. `stale` is
        gone as a state: what the fingerprint used to guard, the engine now
        recomputes on every read. `missing` is a row neither a triage press
        nor an analyze has ever seen, and is what the next press works: an
        analysis is strictly more than a triage cell, so the PR somebody
        flagged and the user analyzed directly does not come back as
        "da triagiare", and keeps its cell when its facts move, exactly like a
        row the grid holds (analysis_stale marks the panel, not the cell).
        """
        records = triage_records(grid)
        analyzed = {int(n) for n, note in (notes or {}).items()
                    if (note or {}).get("analysis_key") and n.isdigit()}
        seen = set(records) | analyzed
        counts = {"current": 0, "missing": 0, "stale": 0}
        if seen:
            decorate(rows, self.me, gates)
        for row in rows:
            if row["n"] in seen:
                status = "current"
                row["action"] = handoff(
                    row, self.repo,
                    self.provider.merge_command(self.repo, row["n"]))
            else:
                status = "missing"
                row.update(state="untriaged", autorun="-", waiting_on=None,
                           action=None, todo="da triagiare")
            row["triage_status"] = status
            counts[status] += 1
        return counts

    def _current_grid(self, grid, rows, state):
        """The published grid, rebuilt on the live verdicts, with the model's
        one-line `what` where it wrote one. Only the `generated` stamp is the
        press's own; the blocks follow the provider."""
        if not grid:
            return None
        current = [row for row in rows if row["triage_status"] == "current"]
        notes = state.get("prs") or {}
        visible = {"generated": grid.get("generated"),
                   "blocks": verdicts.blocks(current)}
        live = {row["n"]: row for row in current}
        for block in visible["blocks"]:
            for row in block["rows"]:
                note = notes.get(str(row["n"])) or {}
                current_key = live[row["n"]]["model_keys"]["what"]
                if note.get("what_key") == current_key:
                    row["what"] = note.get("what") or row.get("what")
        return visible

    def queue(self, refresh=False):
        state = deskstate.load(self.repo)
        rows, raw, states, gates = self._queue_facts(refresh, state=state)
        counts = self._apply_triage(rows, state.get("grid"), gates,
                                    state.get("prs"))
        deskstate.annotate_prs(rows, state)
        for row in rows:
            note = row.get("skill") or {}
            keys = row["model_keys"]
            row["analysis_stale"] = bool(
                note.get("analysis") and keys["analysis"] and
                note.get("analysis_key") != keys["analysis"])
            row["what_stale"] = bool(
                note.get("what") and note.get("what_key") != keys["what"])
            row["conflict_stale"] = bool(
                note.get("conflict_kind") and
                note.get("conflict_key") != keys["conflict"])
        deskstate.annotate_requests(rows, state)
        orders = state.get("orders") or {}
        for row in rows:
            row["order"] = orders.get(str(row["n"]))
        complete = counts["missing"] == 0
        triaged = [row for row in rows if row["triage_status"] == "current"]
        return {"rows": rows, "total": raw.get("total", len(rows)),
                "truncated": raw.get("truncated", False),
                "mergestate_pending": not states,
                "chase": verdicts.chase(triaged, self.me) if triaged else {},
                "session": state.get("session"),
                "gates": gates,
                "grid": self._current_grid(state.get("grid"), rows, state),
                "triage_complete": complete,
                "triage_counts": counts}

    def issues(self, refresh=False):
        raw = self._raw_issues(refresh)
        rows = [dict(row) for row in raw["rows"]]
        state = deskstate.load(self.repo)
        notes = state.get("issues") or {}
        for row in rows:
            record = notes.get(str(row["n"])) or {}
            # a reading of the body beats a guess from the labels, so the
            # model's type wins where it wrote one — and there is one type
            # per row, not one here and another in the analysis panel
            row["type"] = record.get("type") or issue_type(row["labels"],
                                                           row["title"])
            row["impact"] = record.get("impact")
            # an analysis is dated, and the issue's own last activity says
            # whether it has been overtaken. No fingerprint to copy: the
            # date is a fact the model already knows how to write.
            at = record.get("at")
            row["analysis_at"] = at
            row["analysis_stale"] = bool(
                at and row.get("updated") and at[:10] < row["updated"][:10])
            row["action"] = issue_handoff(row, self.repo)
        deskstate.annotate_issues(rows, state)
        deskstate.annotate_requests(rows, state)
        try:
            check = self._crosscheck(self._raw_queue(False)["rows"], refresh)
            issuecheck.annotate(rows, check)
            shortlist = issuecheck.shortlist_export(rows)
        except Exception as exc:
            for row in rows:
                row.setdefault("cross", {"branches": [], "open_prs": [],
                                         "seen_by_me": None, "mine": None,
                                         "note": "cross-check non disponibile: %s"
                                                 % str(exc)[:80]})
            shortlist = None
        picked = {r["n"] for r in (shortlist or {}).get("rows", [])}
        for row in rows:
            row["in_shortlist"] = row["n"] in picked
        return {"rows": rows, "total": raw.get("total", len(rows)),
                "truncated": raw.get("truncated", False),
                "shortlist": shortlist,
                "ranked": bool(shortlist and any(r.get("impact")
                                                 for r in shortlist["rows"]))}

    def live_state(self):
        st = deskstate.load(self.repo)
        active_jobs = jobs.active(self.repo)
        ledger = st.get("requests") or {}
        flows = {key.split(":", 1)[1]: deskstate.effective(rec)
                 for key, rec in ledger.items()
                 if key.startswith(("triage:", "run:"))}
        grid = st.get("grid") or {}
        chat = deskstate.chat_attached(self.repo, st)
        return {"feed": (st.get("feed") or [])[-50:], "flows": flows,
                "chat": {"attached": bool(chat),
                         "at": (chat or {}).get("at")},
                "runs": st.get("runs") or {},
                "working": deskstate.working(self.repo, st),
                "provider_refresh": st.get("provider_refresh"),
                # the stamp, not the grid: the page reads the reconciled rows
                # from /api/queue rather than matching fingerprints a second
                # time in its own copy of the rule
                "triage": {"generated": grid.get("generated")},
                "chase": st.get("chase") or {},
                "session": st.get("session"), "pong": st.get("pong"),
                "agent": {"mode": "on-demand", "selected": self.agent,
                          "busy": bool(active_jobs), "jobs": active_jobs}}

    def snapshot(self, refresh=False):
        """Everything the UI needs, in one response."""
        self.timings = {}
        self.prime(refresh)
        # prime() already paid the refresh; these two read the warm cache
        queue = self.queue()
        issues = self.issues(refresh)
        return {"meta": {"repo": self.repo, "me": self.me,
                         "provider": self.provider.name,
                         "chat": False, "detached": True, "desk": self.kind,
                         "write_token": self.write_token},
                "queue": queue, "issues": issues, "state": self.live_state(),
                "timings": dict(self.timings),
                "generated": time.strftime("%H:%M:%S")}

    def run_triage(self):
        """The explicit triage run, in full: compute the verdicts, PUBLISH the
        keyed grid and the chase blocks, and write the rows file the skill
        reads.

        The grid is deterministic (verdicts.py, 0.07 ms for 52 PRs) and the
        desk owns it end to end. Handing it to a model to copy back verbatim
        cost a turn and lost a row whenever the copy slipped; what a model
        adds is written per PR, in `prs.<n>` — the one-line `what`, the
        analysis, and `conflict_kind`, the single fact the engine cannot read
        off a field. Publishing stays explicit: nothing here runs at boot.

        The press pays a FRESH provider read: publishing on a
        stale-while-revalidate snapshot meant the refresh it triggered landed
        seconds later and contradicted the grid it had just published. And it
        is incremental for the model: `model_tasks` names the exact stale
        artifacts, so an unrelated title edit never re-buys an analysis and
        a same-day push never preserves one.
        """
        state = deskstate.load(self.repo)
        rows, _, _, gates = self._queue_facts(refresh=True,
                                              complete_gates=True, state=state)
        decorate(rows, self.me, gates)
        for row in rows:
            row["action"] = handoff(
                row, self.repo,
                self.provider.merge_command(self.repo, row["n"]))
        grid = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "blocks": verdicts.blocks(rows)}
        chase = verdicts.chase(rows, self.me)

        if self.kind == "pr":       # the issue desk shares the state file and
            def publish(state):     # must not publish a PR grid nobody ran
                state["grid"] = grid
                state["chase"] = chase
            deskstate.update(self.repo, publish)

        issues = self.issues(refresh=self.kind == "issue")
        tasks = model_tasks(rows, state.get("prs"), self.me)
        payload = {"repo": self.repo, "me": self.me,
                   "generated": grid["generated"],
                   "queue": rows, "issues": issues["rows"],
                   "grid": grid, "chase": chase, "gates": gates,
                   "model_tasks": tasks,
                   "needs_model": [int(n) for n in tasks],
                   # the numbers only: every one of them is already a full row
                   # under "issues", and repeating them doubled the payload
                   "shortlist": [r["n"] for r in (issues["shortlist"] or {}).get("rows", [])]}
        path = deskstate.runtime_path(self.repo, "rows.json")
        path.write_text(json.dumps(payload, indent=1))
        return path


def stop_server(server):
    jobs.shutdown()
    server.shutdown()


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

    def _valid_host(self):
        port = self.server.server_address[1]
        return self.headers.get("Host", "").lower() in (
            "127.0.0.1:%s" % port, "localhost:%s" % port)

    def _send_tagged(self, body):
        """304 when the caller already has this payload — the UI polls a few
        times a minute and most polls change nothing. Anything that moves on
        every request is stripped from the tag first, or it would never
        match: the clock and timings."""
        payload = json.dumps(body).encode()
        stable = copy.deepcopy({k: v for k, v in body.items()
                                if k not in self.VOLATILE})
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
        if not self._valid_host():
            self._send(403, {"error": "invalid Host header"})
            return
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
                                 "write_token": self.desk.write_token,
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
                job = jobs.get(self.desk.repo, url.path.rsplit("/", 1)[1])
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
            deskstate.update(self.desk.repo, lambda state: None)
            out["state_file"] = {"ok": True,
                                 "path": str(deskstate.state_path(self.desk.repo))}
        except Exception as exc:
            out["state_file"] = {"ok": False, "error": str(exc)[:200]}
        hit = cache.peek(self.desk.repo, "queue")
        out["cache"] = {"ok": bool(hit), "age": round(hit[0]) if hit else None,
                        "path": str(cache.cache_path(self.desk.repo))}
        try:
            out["agent"] = {"ok": True,
                            "selected": jobs.resolve_agent(self.desk.agent),
                            "mode": "on-demand"}
        except Exception as exc:
            out["agent"] = {"ok": False, "error": str(exc)[:200],
                            "mode": "on-demand"}
        return out

    def do_POST(self):
        if not self._valid_host():
            self._send(403, {"error": "invalid Host header"})
            return
        url = urlparse(self.path)
        try:
            if self.headers.get("X-Git-Workflow-Token") != self.desk.write_token:
                self._send(403, {"error": "invalid write token"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > 65536:
                self._send(413, {"error": "request body too large"})
                return
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            if not isinstance(body, dict):
                self._send(400, {"error": "JSON object required"})
                return
            parts = url.path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "pr"] and parts[3] == "analyze":
                self._analyze_pr(int(parts[2]))
            elif len(parts) == 4 and parts[:2] == ["api", "pr"] and parts[3] == "explain":
                # the one line the factual row cannot write: what this PR is
                # FOR, in the user's language. One row, one sentence — instead
                # of the whole queue's titles rewritten every refresh.
                n = int(parts[2])
                what_key = self._model_key(n, "what")
                if self._chat_handoff("explain", n, {"what_key": what_key},
                                      "spiegazione #%s" % n):
                    return
                job_id = jobs.explain_pr(
                    self.desk.repo, n, self.desk.me, self.desk.cwd,
                    self.desk.agent, what_key)
                self._send(202, {"job": job_id})
            elif len(parts) == 4 and parts[:2] == ["api", "pr"] and parts[3] == "order":
                n = int(parts[2])
                order = deskstate.add_order(self.desk.repo, n, body.get("propose", ""),
                                            body.get("draft"), body.get("instruction", ""))
                if self._chat_handoff("order", n, {"flow": "order", "n": n},
                                      "ordine #%s" % n):
                    return
                job_id = jobs.operation(
                    self.desk.repo, "order", {"n": n}, self.desk.me,
                    self.desk.cwd, self.desk.agent)
                self._send(202, {"order": order, "job": job_id})
            elif len(parts) == 4 and parts[:2] == ["api", "issue"] and parts[3] == "analyze":
                n = int(parts[2])
                if self._chat_handoff("issue-analyze", n, {},
                                      "issue-analyze #%s" % n):
                    return
                job_id = jobs.analyze_issue(
                    self.desk.repo, n, self.desk.me, self.desk.cwd,
                    self.desk.agent)
                self._send(202, {"job": job_id})
            elif parts == ["api", "fetch"]:
                # explicit re-read of the provider, on the caller's demand
                self._send(200, dict(self.desk.snapshot(refresh=True), refetched=True))
            elif parts == ["api", "rows"]:
                path = self.desk.run_triage()
                self._send(200, {"path": str(path)})
            elif parts == ["api", "shutdown"]:
                self._send(200, {"bye": True})
                threading.Thread(target=stop_server,
                                 args=(self.server,), daemon=True).start()
            elif parts == ["api", "run"]:
                flow = body.get("flow", "pr-loop")
                if flow not in ("pr-loop", "issue-loop"):
                    self._send(400, {"error": "unknown run flow"})
                    return
                # rows chosen by hand: the loop works exactly those, in this
                # order, and stops. Clamped here too — the page is an input.
                ns = [int(n) for n in (body.get("ns") or [])]
                batch = max(1, min(int(body.get("batch") or 1), MAX_BATCH))
                label = "%s · %d scelte" % (flow, len(ns)) if ns else flow
                if self._chat_handoff(
                        "run", None,
                        {"flow": flow, "ns": ns, "batch": batch}, label,
                        key="run:%s" % flow):
                    return
                job_id = jobs.operation(
                    self.desk.repo, flow, {"ns": ns, "batch": batch},
                    self.desk.me, self.desk.cwd, self.desk.agent)
                self._send(202, {"job": job_id, "flow": flow,
                                 "ns": ns, "batch": batch, "label": label})
            elif parts == ["api", "ping"]:
                self._send(200, {"pong": body.get("token") or "",
                                 "mode": "detached"})
            elif parts == ["api", "triage"]:
                flow = body.get("flow", "pr-triage")
                if flow not in ("pr-triage", "issue-triage"):
                    self._send(400, {"error": "unknown triage flow"})
                    return
                # the fresh read and the grid are the job's first phase: they
                # cost seconds on a real queue, and a click must not hold them
                # open. The model is asked only for what the export still owes
                job_id = jobs.triage(
                    self.desk.repo, flow, self.desk.run_triage, self.desk.me,
                    self.desk.cwd, self.desk.agent)
                self._send(202, {"job": job_id})
            else:
                self._send(404, {"error": "not found"})
        except Exception as exc:
            self._send(502, {"error": str(exc)})

    def _analyze_pr(self, n):
        # the payload is a callable: an attached chat needs the context in its
        # record, a one-shot job reads it in its own thread, and a click that
        # goes nowhere pays for neither
        if self._chat_handoff("analyze", n, self._analysis_payload(n),
                              "pr-analyze #%s" % n):
            return
        job_id = jobs.analyze_pr(self.desk.repo, n, self.desk.me,
                                 self.desk.cwd, self.desk.agent,
                                 lambda: self._analysis_inputs(n))
        self._send(202, {"job": job_id})

    def _analysis_payload(self, n):
        def build():
            keys, context = self._analysis_inputs(n)
            return {"analysis_keys": keys, "context": context}
        return build

    def _analysis_inputs(self, n):
        row = next((row for row in self.desk.queue()["rows"]
                    if row["n"] == n), None)
        if not row:
            return {}, {"probe": None}
        keys = row.get("model_keys") or {}
        if keys.get("analysis") is None:
            rows, _, _, gates = self.desk._queue_facts(complete_gates=True)
            decorate(rows, self.desk.me, gates)
            row = next((item for item in rows if item["n"] == n), row)
            keys = row.get("model_keys") or {}
        try:
            probe = self.desk.provider.analysis_probe(self.desk.repo, n)
        except Exception as exc:
            probe = {"fresh": False, "error": str(exc)[:160]}
        if (probe and probe.get("fresh") and probe.get("head")
                and probe["head"] != row.get("head")):
            rows, _, _, gates = self.desk._queue_facts(
                refresh=True, complete_gates=True)
            decorate(rows, self.desk.me, gates)
            row = next((item for item in rows if item["n"] == n), row)
            keys = row.get("model_keys") or {}
        note = ((deskstate.load(self.desk.repo).get("prs") or {})
                .get(str(n)) or {})
        cached_problem = (note.get("problem")
                          if note.get("problem_key") == keys.get("problem")
                          else None)
        keys = dict(keys, problem_head=row.get("head"))
        context = {
            "row": {key: row.get(key) for key in (
                "n", "title", "summary", "author", "created", "base",
                "base_head", "head", "merge", "decision", "req", "reviews",
                "unresolved", "threads", "closes", "last")},
            "probe": probe,
            "cached_problem": cached_problem,
            "previous_problem": note.get("problem"),
            "previous_problem_head": note.get("problem_head"),
        }
        return keys, context

    def _chat_handoff(self, kind, n, payload, label, key=None):
        """Route the click to the attached chat instead of a one-shot agent.

        Triage never comes through here: its artifacts are the desk's own
        cells, so it stays on the independent agent whatever the chat state.

        Only a chat that is heartbeating may take a NEW click: a conversation
        that ended mid-request leaves a `taken` record nothing expires, and
        routing to it enqueues clicks nobody will ever read — no job, no
        progress, a mute panel. A click it never claimed goes back too.

        A callable payload reads the provider, seconds on a cold desk: the
        record is enqueued as `preparing` at once and a thread fills it in,
        so the click is answered in milliseconds here as it is for a job.

        Returns the response sent, or None when no chat is listening and the
        caller must start the job as before."""
        key = key or deskstate.request_key(kind, n)
        deskstate.reclaim_request(self.desk.repo, key)
        if not deskstate.chat_listening(self.desk.repo):
            return None
        lazy = callable(payload)
        record, created = deskstate.request(
            self.desk.repo, key, kind, n, label, via="chat",
            payload=None if lazy else payload,
            status="preparing" if lazy else "queued")
        if created:
            notify.notify(self.desk.repo,
                          "%s → in coda alla chat collegata" % label, n)
            if lazy:
                threading.Thread(target=self._prepare_request,
                                 args=(key, payload), daemon=True).start()
        response = {"queued": True, "via": "chat", "request": key,
                    "created": created, "at": record["at"]}
        self._send(202, response)
        return response

    def _prepare_request(self, key, build):
        try:
            payload = build()
        except Exception as exc:
            deskstate.close_request(self.desk.repo, key, "failed",
                                    "contesto non letto: %s" % str(exc)[:160])
            return
        deskstate.ready_request(self.desk.repo, key, payload)

    def _model_key(self, n, artifact):
        row = next((row for row in self.desk.queue()["rows"]
                    if row["n"] == n), None)
        key = (row or {}).get("model_keys", {}).get(artifact)
        if key is not None or artifact != "analysis":
            return key
        rows, _, _, gates = self.desk._queue_facts(complete_gates=True)
        decorate(rows, self.desk.me, gates)
        row = next((row for row in rows if row["n"] == n), None)
        return (row or {}).get("model_keys", {}).get(artifact)


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
    parser.add_argument("--chat", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--agent", default="auto", choices=("auto", "claude", "codex"),
                        help="headless agent used outside chat mode")
    parser.add_argument("--keep-state", action="store_true",
                        help="keep the previous session's analyses/feed instead "
                             "of starting empty")
    parser.add_argument("--no-prefetch", action="store_true",
                        help="do not warm the provider cache at boot")
    parser.add_argument("--keep-cache", action="store_true",
                        help="reuse the previous run's provider cache instead "
                             "of reading the provider again (offline work)")
    args = parser.parse_args()

    provider = get_provider(args.provider)
    repo = args.repo or detect_repo()
    me = args.me or provider.whoami()
    port = args.port or (8399 if args.desk == "pr" else 8398)
    swept = deskstate.sweep_legacy()
    jobs.reconcile(repo)
    if not args.keep_state:
        deskstate.reset(repo)
    cache_action = "kept" if args.keep_cache else cache.reset(repo)

    desk = Desk(provider, repo, me, str(Path.cwd()), chat=False,
                kind=args.desk, agent=args.agent)
    Handler.desk = desk
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    stopping = threading.Event()

    def request_stop(*_):
        if not stopping.is_set():
            stopping.set()
            threading.Thread(target=stop_server,
                             args=(server,), daemon=True).start()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    if not args.no_prefetch:
        desk.prefetch()
    sys.stderr.write("%s desk on http://127.0.0.1:%s  repo=%s me=%s provider=%s "
                     "cache=%s\n"
                     % (args.desk, port, repo, me, provider.name, cache_action))
    if swept:
        sys.stderr.write("swept %d session file(s) the old layout left in "
                         "~/.local/state\n" % swept)
    sys.stderr.flush()
    try:
        server.serve_forever()
    finally:
        jobs.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
