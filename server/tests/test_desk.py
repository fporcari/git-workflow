"""Desk regression tests — stdlib unittest, fixture provider, no network.

    python3 -m unittest discover -s tests -v      (from server/)

Covers the row contract the skills read, the verdict engine, the cache's
stale-while-revalidate and single-flight behaviour, and every HTTP endpoint
including the 304 path.
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# every test writes its state under a throwaway HOME
_HOME = tempfile.mkdtemp(prefix="deskstate-")
os.environ["HOME"] = _HOME

import cache            # noqa: E402
import deskstate        # noqa: E402
import prdesk           # noqa: E402
import verdicts         # noqa: E402
from providers import get_provider  # noqa: E402

deskstate.STATE_DIR = Path(_HOME) / ".local" / "state" / "git-workflow"
cache.STATE_DIR = deskstate.STATE_DIR
REPO = "genropy/genropy"


def fresh_desk(**kw):
    cache.clear(REPO)
    provider = get_provider("fixture")
    return prdesk.Desk(provider, REPO, provider.whoami(), str(ROOT), **kw)


class RowContract(unittest.TestCase):
    """The shape every skill and the UI read. Breaking it breaks them."""

    PR_KEYS = {"n", "title", "created", "author", "draft", "base", "merge",
               "decision", "req", "reviews", "unresolved", "threads", "closes",
               "last", "url", "todo", "state", "autorun", "action"}
    ISSUE_KEYS = {"n", "title", "created", "author", "labels", "assignees",
                  "comments", "url", "type", "action"}

    def test_queue_rows_carry_every_field(self):
        queue = fresh_desk().queue()
        self.assertTrue(queue["rows"])
        for row in queue["rows"]:
            self.assertTrue(self.PR_KEYS <= set(row), self.PR_KEYS - set(row))
            self.assertIn(row["state"], ("ready", "attention", "waiting", "decision"))

    def test_issue_rows_carry_every_field(self):
        rows = fresh_desk().issues()["rows"]
        self.assertTrue(rows)
        for row in rows:
            self.assertTrue(self.ISSUE_KEYS <= set(row), self.ISSUE_KEYS - set(row))
            self.assertIn(row["type"], ("DEFECT", "REQUEST", "QUESTION", "DOCS",
                                        "UNCLASSIFIED"))

    def test_queue_reports_its_own_total(self):
        queue = fresh_desk().queue()
        self.assertEqual(queue["total"], len(queue["rows"]))
        self.assertFalse(queue["truncated"])

    def test_issues_report_their_own_total(self):
        got = fresh_desk().issues()
        self.assertEqual(got["total"], len(got["rows"]))
        self.assertFalse(got["truncated"])

    def test_a_truncated_issue_list_is_reported(self):
        """228 open issues served as 100 used to read as "100 aperte"."""
        desk = fresh_desk()
        desk.provider.data["issues_total"] = 228
        desk.provider.data["issues_truncated"] = True
        got = desk.issues()
        self.assertTrue(got["truncated"])
        self.assertEqual(got["total"], 228)


class MergeStatePhase(unittest.TestCase):
    """Phase two must land, and the table must be honest while it has not."""

    def test_own_prs_get_their_merge_state(self):
        desk = fresh_desk()
        rows = desk.queue()["rows"]
        mine = [r for r in rows if r["author"] == desk.me]
        self.assertTrue(mine)
        self.assertTrue(all(r["merge"] for r in mine))

    def test_missing_phase_two_never_invents_a_verdict(self):
        row = {"author": "me", "draft": False, "merge": None, "decision": "APPROVED",
               "req": [], "reviews": [{"who": "x", "state": "APPROVED"}],
               "unresolved": 0, "last": None}
        todo, state, autorun = verdicts.verdict(row, "me")
        self.assertNotEqual(autorun, "A1")       # never claim mergeable on no data
        self.assertEqual(state, "decision")


class Verdicts(unittest.TestCase):
    def test_approved_and_clean_is_a1(self):
        row = {"author": "me", "draft": False, "merge": "CLEAN", "decision": "APPROVED",
               "req": [], "reviews": [{"who": "x", "state": "APPROVED"}],
               "unresolved": 0, "last": None}
        self.assertEqual(verdicts.verdict(row, "me")[2], "A1")

    def test_dirty_branch_is_a3(self):
        row = {"author": "me", "draft": False, "merge": "DIRTY", "decision": None,
               "req": [], "reviews": [], "unresolved": 0, "last": None}
        self.assertEqual(verdicts.verdict(row, "me")[2], "A3")

    def test_review_requested_of_me_wins(self):
        row = {"author": "you", "draft": False, "merge": "CLEAN", "decision": None,
               "req": ["me"], "reviews": [], "unresolved": 0, "last": None}
        self.assertEqual(verdicts.verdict(row, "me")[1], "attention")

    def test_an_approval_never_opens_a_conversation(self):
        row = {"author": "me", "draft": False, "merge": "CLEAN", "decision": "APPROVED",
               "req": [], "reviews": [{"who": "x", "state": "APPROVED"}],
               "unresolved": 0, "last": {"who": "x", "ch": "approved", "t": "2026-01-01"}}
        self.assertEqual(verdicts.verdict(row, "me")[2], "A1")


class Cache(unittest.TestCase):
    def test_hit_then_stale_then_refresh(self):
        cache.clear(REPO)
        calls = []
        loader = lambda: (calls.append(1), {"v": len(calls)})[1]   # noqa: E731
        data, age, source = cache.get(REPO, "k", loader)
        self.assertEqual((source, data["v"]), ("miss", 1))
        data, age, source = cache.get(REPO, "k", loader)
        self.assertEqual((source, data["v"]), ("hit", 1))
        data, age, source = cache.get(REPO, "k", loader, refresh=True)
        self.assertEqual((source, data["v"]), ("miss", 2))

    def test_stale_serves_at_once_and_refreshes_behind(self):
        cache.clear(REPO)
        calls = []
        loader = lambda: (calls.append(1), {"v": len(calls)})[1]   # noqa: E731
        cache.get(REPO, "k", loader)
        blob = json.loads(cache.cache_path(REPO).read_text())
        blob["k"]["at"] -= cache.FRESH + 5          # age it past FRESH
        cache.cache_path(REPO).write_text(json.dumps(blob))
        t0 = time.time()
        data, age, source = cache.get(REPO, "k", loader)
        self.assertEqual(source, "stale")
        self.assertLess(time.time() - t0, 0.5)      # served without waiting
        for _ in range(50):
            if len(calls) > 1:
                break
            time.sleep(0.02)
        self.assertEqual(len(calls), 2)             # refreshed behind the caller

    def test_concurrent_misses_collapse_into_one_load(self):
        cache.clear(REPO)
        calls = []

        def loader():
            calls.append(1)
            time.sleep(0.3)
            return {"v": len(calls)}

        threads = [threading.Thread(target=lambda: cache.get(REPO, "sf", loader))
                   for _ in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(calls), 1)

    def test_survives_a_corrupt_file(self):
        cache.cache_path(REPO).write_text("{not json")
        self.assertIsNone(cache.peek(REPO, "k"))


class Http(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        prdesk.Handler.desk = fresh_desk(chat=False, kind="pr")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), prdesk.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def url(self, path):
        return "http://127.0.0.1:%s%s" % (self.port, path)

    def get(self, path, etag=None):
        req = Request(self.url(path))
        if etag:
            req.add_header("If-None-Match", etag)
        try:
            with urlopen(req, timeout=30) as resp:
                return resp.status, resp.headers.get("ETag"), resp.read()
        except HTTPError as exc:          # urllib raises on 304
            return exc.code, exc.headers.get("ETag"), exc.read()

    def post(self, path, body=None):
        data = json.dumps(body or {}).encode()
        req = Request(self.url(path), data=data,
                      headers={"Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_index_is_served(self):
        status, _, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"<!doctype html>", body[:40].lower())

    def test_desk_is_one_round_trip(self):
        status, etag, body = self.get("/api/desk")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(set(payload) >= {"meta", "queue", "issues", "state",
                                         "timings", "generated"}, True)
        self.assertTrue(payload["queue"]["rows"])
        self.assertTrue(payload["issues"]["rows"])
        self.assertTrue(etag)

    def test_unchanged_payload_answers_304(self):
        _, etag, _ = self.get("/api/desk")
        status, _, body = self.get("/api/desk", etag)
        self.assertEqual(status, 304)
        self.assertEqual(body, b"")

    def test_a_ticking_watcher_age_does_not_defeat_the_304(self):
        deskstate.heartbeat_path(REPO).parent.mkdir(parents=True, exist_ok=True)
        deskstate.heartbeat_path(REPO).touch()
        _, etag, _ = self.get("/api/state")
        time.sleep(1.05)                      # the age in seconds has moved
        status, _, _ = self.get("/api/state", etag)
        self.assertEqual(status, 304)

    def test_the_watcher_age_still_reaches_the_client(self):
        deskstate.heartbeat_path(REPO).touch()
        _, _, body = self.get("/api/state")
        self.assertIsNotNone(json.loads(body)["watcher"]["age"])

    def test_a_cold_snapshot_pays_its_misses_in_parallel(self):
        os.environ["DESK_FIXTURE_LATENCY"] = "0.4"
        try:
            desk = fresh_desk()
            t0 = time.time()
            desk.snapshot()
            elapsed = time.time() - t0
        finally:
            os.environ.pop("DESK_FIXTURE_LATENCY", None)
        # three 0.4s loaders: ~0.4s in parallel, ~1.2s one after the other
        self.assertLess(elapsed, 0.9, "%.2fs — the misses ran serially" % elapsed)

    def test_keepalive_is_on(self):
        self.assertEqual(prdesk.Handler.protocol_version, "HTTP/1.1")

    def test_state_endpoint_shape(self):
        _, _, body = self.get("/api/state")
        payload = json.loads(body)
        self.assertIn("watcher", payload)
        self.assertIn("feed", payload)

    def test_rows_export_is_what_the_triage_reads(self):
        status, payload = self.post("/api/rows")
        self.assertEqual(status, 200)
        exported = json.loads(Path(payload["path"]).read_text())
        self.assertEqual(exported["repo"], REPO)
        self.assertTrue(exported["queue"])
        self.assertTrue(exported["issues"])

    def test_chat_only_endpoints_refuse_outside_chat_mode(self):
        for path in ("/api/run", "/api/triage", "/api/ping", "/api/issue/1/analyze"):
            status, payload = self.post(path)
            self.assertEqual(status, 409, path)
            self.assertIn("error", payload)

    def test_order_is_recorded_for_pr_run(self):
        status, payload = self.post("/api/pr/1152/order", {"propose": "merge it"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["order"]["status"], "pending")
        self.assertEqual(deskstate.load(REPO)["orders"]["1152"]["propose"], "merge it")

    def test_unknown_path_is_404(self):
        status, payload = self.post("/api/nope")
        self.assertEqual(status, 404)

    def test_selftest_reports_the_cache(self):
        _, _, body = self.get("/api/selftest")
        payload = json.loads(body)
        self.assertIn("cache", payload)
        self.assertIn("provider", payload)


class BootDoesNotTriage(unittest.TestCase):
    """Point of the redesign: the desk fetches at boot, it does not sit on an
    empty grid waiting for a chat-side triage."""

    def test_triage_at_boot_is_opt_in(self):
        import argparse
        source = Path(ROOT / "prdesk.py").read_text()
        self.assertIn("--triage-at-boot", source)
        self.assertIn("args.chat and args.triage_at_boot", source)

    def test_prefetch_fills_the_cache(self):
        desk = fresh_desk()
        desk.prefetch()
        for _ in range(100):
            if cache.peek(REPO, "queue") and cache.peek(REPO, "issues"):
                break
            time.sleep(0.02)
        self.assertIsNotNone(cache.peek(REPO, "queue"))
        self.assertIsNotNone(cache.peek(REPO, "issues"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
