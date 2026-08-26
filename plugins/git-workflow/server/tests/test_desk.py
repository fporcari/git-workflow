"""Desk regression tests — stdlib unittest, fixture provider, no network.

    python3 -m unittest discover -s tests -v      (from server/)

Covers the row contract the skills read, the verdict engine, the cache's
stale-while-revalidate and single-flight behaviour, and every HTTP endpoint
including the 304 path.
"""

import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# every test writes its state under a throwaway HOME
_HOME = tempfile.mkdtemp(prefix="deskstate-")
os.environ["HOME"] = _HOME

import cache            # noqa: E402
import deskstate        # noqa: E402
import gate as gatelib  # noqa: E402
import inbox            # noqa: E402
import issuecheck       # noqa: E402
import jobs             # noqa: E402
import prdesk           # noqa: E402
import safejson         # noqa: E402
import verdicts         # noqa: E402
from providers import get_provider  # noqa: E402

deskstate.STATE_DIR = Path(_HOME) / ".local" / "state" / "git-workflow"
deskstate.RUNTIME_DIR = Path(_HOME) / "runtime"
REPO = "genropy/genropy"


def _write_orders(repo, first, count, state_dir, runtime_dir):
    deskstate.STATE_DIR = Path(state_dir)
    deskstate.RUNTIME_DIR = Path(runtime_dir)
    for n in range(first, first + count):
        deskstate.add_order(repo, n, "inspect", "", "worker")


def _write_cache_keys(repo, first, count, state_dir, runtime_dir):
    deskstate.STATE_DIR = Path(state_dir)
    deskstate.RUNTIME_DIR = Path(runtime_dir)
    for n in range(first, first + count):
        cache.store(repo, "key-%s" % n, n)


def fresh_desk(**kw):
    cache.clear(REPO)
    provider = get_provider("fixture")
    return prdesk.Desk(provider, REPO, provider.whoami(), str(ROOT), **kw)


class RowContract(unittest.TestCase):
    """The shape every skill and the UI read. Breaking it breaks them."""

    PR_KEYS = {"n", "title", "created", "author", "assignees", "draft", "base",
               "head", "incomplete", "merge",
               "decision", "req", "reviews", "unresolved", "threads", "closes",
               "last", "url", "todo", "state", "autorun", "action",
               "triage_key", "triage_status"}
    ISSUE_KEYS = {"n", "title", "created", "author", "labels", "assignees",
                  "comments", "url", "type", "action"}

    def test_queue_rows_carry_every_field(self):
        deskstate.save(REPO, {})
        queue = fresh_desk().queue()
        self.assertTrue(queue["rows"])
        for row in queue["rows"]:
            self.assertTrue(self.PR_KEYS <= set(row), self.PR_KEYS - set(row))
            self.assertEqual(row["state"], "untriaged")
            self.assertEqual(row["triage_status"], "missing")

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
               "assignees": ["me"],
               "req": [], "reviews": [{"who": "x", "state": "APPROVED"}],
               "unresolved": 0, "last": None}
        todo, state, autorun = verdicts.verdict(row, "me")
        self.assertNotEqual(autorun, "A1")       # never claim mergeable on no data
        self.assertEqual(state, "decision")


class Verdicts(unittest.TestCase):
    def test_approved_and_clean_is_a1(self):
        row = {"author": "me", "draft": False, "merge": "CLEAN", "decision": "APPROVED",
               "assignees": ["me"],
               "req": [], "reviews": [{"who": "x", "state": "APPROVED"}],
               "unresolved": 0, "last": None}
        self.assertEqual(verdicts.verdict(row, "me")[2], "A1")

    def test_dirty_branch_needs_conflict_inspection(self):
        row = {"author": "me", "draft": False, "merge": "DIRTY", "decision": None,
               "req": [], "reviews": [], "unresolved": 0, "last": None}
        self.assertEqual(verdicts.verdict(row, "me")[2], "asks")

    def test_only_a_known_mechanical_conflict_is_a3(self):
        row = {"author": "me", "draft": False, "merge": "DIRTY", "decision": None,
               "req": [], "reviews": [], "unresolved": 0, "last": None,
               "conflict_kind": "mechanical"}
        self.assertEqual(verdicts.verdict(row, "me")[2], "A3")

    def test_wrong_pr_assignee_blocks_a1(self):
        row = {"author": "me", "assignees": [], "draft": False, "merge": "CLEAN",
               "decision": "APPROVED", "req": [],
               "reviews": [{"who": "x", "state": "APPROVED"}],
               "unresolved": 0, "last": None}
        self.assertEqual(verdicts.verdict(row, "me")[2], "asks")

    def test_incomplete_nested_connections_block_a1(self):
        row = {"author": "me", "assignees": ["me"], "draft": False,
               "merge": "CLEAN", "decision": "APPROVED", "req": [],
               "reviews": [{"who": "x", "state": "APPROVED"}],
               "unresolved": 0, "last": None, "incomplete": True}
        self.assertEqual(verdicts.verdict(row, "me")[2], "asks")

    def test_review_requested_of_me_wins(self):
        row = {"author": "you", "draft": False, "merge": "CLEAN", "decision": None,
               "req": ["me"], "reviews": [], "unresolved": 0, "last": None}
        self.assertEqual(verdicts.verdict(row, "me")[1], "attention")

    def test_an_approval_never_opens_a_conversation(self):
        row = {"author": "me", "draft": False, "merge": "CLEAN", "decision": "APPROVED",
               "assignees": ["me"],
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
        repo = REPO + "-corrupt"
        cache.clear(repo)
        cache.cache_path(repo).write_text("{not json")
        self.assertIsNone(cache.peek(repo, "k"))


class CrossProcessFiles(unittest.TestCase):
    def run_workers(self, target, repo):
        context = multiprocessing.get_context("spawn")
        workers = [context.Process(
            target=target,
            args=(repo, i * 20, 20, str(deskstate.STATE_DIR),
                  str(deskstate.RUNTIME_DIR)))
                   for i in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
            self.assertEqual(worker.exitcode, 0)

    def test_state_updates_from_sibling_desks_are_not_lost(self):
        repo = REPO + "-multiprocess-state"
        deskstate.reset(repo)
        self.run_workers(_write_orders, repo)
        self.assertEqual(len(deskstate.load(repo)["orders"]), 80)

    def test_cache_updates_from_sibling_desks_are_not_lost(self):
        repo = REPO + "-multiprocess-cache"
        cache.clear(repo)
        self.run_workers(_write_cache_keys, repo)
        self.assertEqual(len(safejson.read(cache.cache_path(repo))), 80)


class HeadlessAgents(unittest.TestCase):
    RESULT = {"n": 17, "author": "alice", "problem": "problema",
              "history": "storia",
              "propose": "proposta", "draft": None,
              "verified": ["diff"], "not_verified": ["CI"]}

    def test_codex_command_is_ephemeral_and_read_only(self):
        command = jobs.command("codex", "prompt", jobs.READ_TOOLS,
                               "/tmp/repo", "/tmp/result.json")
        self.assertEqual(command[:2], ["codex", "exec"])
        self.assertIn("--ephemeral", command)
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertIn("--output-schema", command)

    def test_claude_command_has_no_write_tool(self):
        command = jobs.command("claude", "prompt", jobs.READ_TOOLS, "/tmp/repo")
        self.assertIn("--json-schema", command)
        self.assertNotIn("Write", command[command.index("--allowedTools") + 1])
        self.assertNotIn("gh pr view", jobs.READ_TOOLS)
        self.assertNotIn("gh pr checks", jobs.READ_TOOLS)

    def test_result_cannot_escape_to_another_pr(self):
        with self.assertRaisesRegex(ValueError, "expected #18"):
            jobs.parse_result("claude", json.dumps({
                "result": json.dumps(self.RESULT)}), expected_n=18)

    def test_result_requires_a_non_empty_decision_block(self):
        result = dict(self.RESULT, problem="")
        with self.assertRaisesRegex(ValueError, "empty analysis fields"):
            jobs.parse_result("claude", json.dumps({
                "result": json.dumps(result)}), expected_n=17)

    def test_valid_result_is_merged_into_desk_state(self):
        repo = REPO + "-headless-result"
        deskstate.reset(repo)
        jobs.persist(repo, self.RESULT)
        record = deskstate.load(repo)["prs"]["17"]
        self.assertEqual(record["next"], "proposta")
        self.assertEqual(record["author"], "alice")
        self.assertEqual(record["problem"], "problema")
        self.assertEqual(record["history"], "storia")
        self.assertIn("storia", record["analysis"])


class WhereThingsLive(unittest.TestCase):
    """Temp for what the machine can recreate; home for what a model made."""

    def test_the_session_files_live_under_the_temp_dir(self):
        for path in (cache.cache_path(REPO),
                     inbox.inbox_path(REPO),
                     deskstate.heartbeat_path(REPO),
                     deskstate.runtime_path(REPO, "rows.json")):
            self.assertTrue(str(path).startswith(str(deskstate.RUNTIME_DIR)), path)

    def test_the_model_s_work_stays_in_the_state_dir(self):
        self.assertTrue(str(deskstate.state_path(REPO)).startswith(
            str(deskstate.STATE_DIR)))

    def test_the_runtime_dir_is_private(self):
        got = deskstate.runtime_dir()
        self.assertTrue(got.is_dir())
        self.assertEqual(oct(got.stat().st_mode)[-3:], "700")

    def test_the_old_layout_is_swept_out_of_the_home_dir(self):
        """A stale cache left in ~/.local/state reads like live state."""
        deskstate.STATE_DIR.mkdir(parents=True, exist_ok=True)
        stale = deskstate.STATE_DIR / "owner__repo__cache.json"
        keep = deskstate.state_path("owner/repo")
        stale.write_text("{}")
        keep.write_text("{}")
        self.assertEqual(deskstate.sweep_legacy(), 1)
        self.assertFalse(stale.exists())
        self.assertTrue(keep.exists(), "the model's work must survive the sweep")

    def test_the_inbox_can_be_truncated_without_knowing_the_path(self):
        inbox.push(REPO, {"kind": "ping"})
        self.assertTrue(inbox.inbox_path(REPO).stat().st_size)
        inbox.truncate(REPO)
        self.assertFalse(inbox.inbox_path(REPO).stat().st_size)


class LaunchClearsTheCache(unittest.TestCase):
    """The cache is for what happens while the desk is up — a browser reload,
    the polling, a second tab — not for surviving a relaunch. Launching the
    desk means: read it again.

    Its own repo name: the desk's background warm threads outlive the test
    that started them and would write fresh entries into a shared cache.
    """

    REPO = REPO + "-launch"

    def seed(self, age):
        cache.clear(self.REPO)
        cache.store(self.REPO, "queue", {"rows": []})
        blob = json.loads(cache.cache_path(self.REPO).read_text())
        blob["queue"]["at"] -= age
        cache.cache_path(self.REPO).write_text(json.dumps(blob))

    def test_a_launch_drops_the_previous_run(self):
        self.seed(600)
        self.assertEqual(cache.reset(self.REPO), "cleared")
        self.assertIsNone(cache.peek(self.REPO, "queue"))

    def test_a_sibling_desk_starting_seconds_later_spares_it(self):
        """The PR desk and the issue desk start back to back on one repo and
        share this file: the second must not throw away the first's fetch."""
        self.seed(2)
        self.assertEqual(cache.reset(self.REPO), "spared")
        self.assertIsNotNone(cache.peek(self.REPO, "queue"))

    def test_an_empty_cache_is_not_an_error(self):
        cache.clear(self.REPO)
        self.assertEqual(cache.reset(self.REPO), "empty")

    def test_keep_cache_is_the_opt_out(self):
        source = Path(ROOT / "prdesk.py").read_text()
        self.assertIn("--keep-cache", source)
        self.assertIn('cache_action = "kept" if args.keep_cache else cache.reset(repo)',
                      source)

    def test_a_reload_while_the_desk_is_up_still_hits(self):
        cache.clear(REPO)
        desk = fresh_desk()
        desk.snapshot()
        _, _, source = cache.get(REPO, "queue", lambda: self.fail("refetched"))
        self.assertEqual(source, "hit")


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
        cls.server.server_close()

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

    def post(self, path, body=None, token=True):
        data = json.dumps(body or {}).encode()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Git-Workflow-Token"] = self.server.RequestHandlerClass.desk.write_token
        req = Request(self.url(path), data=data,
                      headers=headers)
        try:
            with urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_index_is_served(self):
        status, _, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"<!doctype html>", body[:40].lower())

    def test_posts_without_the_session_token_are_rejected(self):
        status, payload = self.post("/api/rows", token=False)
        self.assertEqual(status, 403)
        self.assertIn("token", payload["error"])

    def test_unknown_flow_is_rejected(self):
        status, payload = self.post("/api/run", {"flow": "anything"})
        self.assertEqual(status, 400)
        self.assertIn("flow", payload["error"])

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
    """Point of the redesign: boot fetches; only the button triages."""

    def test_boot_has_no_triage_switch_or_enqueue(self):
        source = Path(ROOT / "prdesk.py").read_text()
        self.assertNotIn("--triage-at-boot", source)
        self.assertNotIn("triage_at_boot", source)

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


class Gate(unittest.TestCase):
    """The reads that make a verdict honest. Where a base restricts who may
    push, the field-only verdict said `A1 -> merge it` on the user's own
    approved CLEAN PRs and was wrong."""

    APPROVED = {"n": 1, "author": "me", "draft": False, "merge": "CLEAN",
                "assignees": ["me"],
                "decision": "APPROVED", "req": [],
                "reviews": [{"who": "x", "state": "APPROVED"}],
                "unresolved": 0, "last": None, "base": "develop",
                "title": "t", "created": "2026-08-01"}

    def gate(self, **kw):
        base = {"branch": "develop", "protected": True, "landers": None,
                "can_land": True, "as_admin": False,
                "conversation_resolution": False}
        return dict(base, **kw)

    def test_without_a_gate_nothing_changes(self):
        self.assertEqual(verdicts.verdict(self.APPROVED, "me")[2], "A1")

    def test_a_restricted_base_is_not_my_merge(self):
        got = verdicts.verdict(self.APPROVED, "me",
                               self.gate(landers=["lander"], can_land=False))
        self.assertNotEqual(got[2], "A1")
        self.assertEqual(got[1], "waiting")
        self.assertIn("lander", got[0])

    def test_a_restriction_that_does_not_bind_an_admin_keeps_the_autorun(self):
        """Capability, not ownership: an admin the restriction does not bind
        still owns his own merge. Whose merge it is by convention is a house
        rule, not something to read off a protection setting."""
        got = verdicts.verdict(self.APPROVED, "me",
                               self.gate(landers=["lander"], can_land=True,
                                         as_admin=True))
        self.assertEqual(got[2], "A1")

    def test_but_the_note_says_the_branch_is_somebody_else_s(self):
        notes = gatelib.notes(self.gate(landers=["lander"], can_land=True,
                                        as_admin=True, codeowners_required=False,
                                        codeowners_path=None, dismiss_stale=False))
        joined = " ".join(notes)
        self.assertIn("lander", joined)
        self.assertIn("admin", joined)

    def test_being_on_the_list_keeps_the_autorun(self):
        got = verdicts.verdict(self.APPROVED, "me",
                               self.gate(landers=["me", "lander"], can_land=True))
        self.assertEqual(got[2], "A1")

    def test_conversation_resolution_is_named_as_a_block(self):
        row = dict(self.APPROVED, unresolved=2, merge="BLOCKED",
                   last={"who": "x", "ch": "inline", "t": "2026-08-02"})
        with_gate = verdicts.verdict(row, "me", self.gate(conversation_resolution=True))
        self.assertIn("bloccano il merge", with_gate[0])
        self.assertNotIn("bloccano", verdicts.verdict(row, "me")[0])

    def test_codeowners_parsing(self):
        owners, per_path = gatelib.parse_codeowners(
            "# comment\n*  @alice @bob\n")
        self.assertEqual(owners, ["alice", "bob"])
        self.assertFalse(per_path)
        owners, per_path = gatelib.parse_codeowners(
            "*.py @alice\ndocs/ @carol\n")
        self.assertEqual(owners, ["alice", "carol"])
        self.assertTrue(per_path, "per-path rules need a diff read to resolve")

    def test_codeowners_ref_is_sent_with_a_get_request(self):
        with mock.patch.object(gatelib, "_json", return_value=None) as query:
            gatelib._codeowners("owner/repo", "develop")
        self.assertTrue(query.call_args_list)
        for call in query.call_args_list:
            self.assertEqual(call.args[1:3], ("--method", "GET"))

    def test_notes_say_what_the_gate_means(self):
        notes = gatelib.notes(self.gate(landers=["lander"], can_land=False,
                                        codeowners_required=True,
                                        codeowners_path=None,
                                        conversation_resolution=True,
                                        dismiss_stale=True))
        joined = " ".join(notes)
        self.assertIn("lander", joined)
        self.assertIn("CODEOWNERS", joined)
        self.assertIn("azzera le approvazioni", joined)

    def test_an_unprotected_base_says_clean_means_nothing(self):
        notes = gatelib.notes({"branch": "feat/x", "protected": False})
        self.assertIn("non protetta", " ".join(notes))

    def test_an_admin_is_not_bound_by_a_restriction_unless_admins_are(self):
        """The two real shapes, side by side: the same restriction binds on a
        branch where enforce_admins is on and does not where it is off."""
        loose = gatelib.parse_codeowners  # keep the import honest
        self.assertTrue(callable(loose))
        for enforce, expected in ((False, True), (True, False)):
            protection = {
                "required_pull_request_reviews": {},
                "enforce_admins": {"enabled": enforce},
                "restrictions": {"users": [{"login": "other"}], "teams": [], "apps": []},
            }
            got = gatelib._shape(protection, (None, None), "admin", "me", "b")
            self.assertEqual(got["can_land"], expected,
                             "enforce_admins=%s" % enforce)
            self.assertEqual(got["as_admin"], expected)

    def test_a_team_restriction_is_unknown_until_membership_is_checked(self):
        protection = {
            "required_pull_request_reviews": {},
            "enforce_admins": {"enabled": True},
            "restrictions": {"users": [], "teams": [{"slug": "maintainers"}],
                             "apps": []},
        }
        got = gatelib._shape(protection, (None, None), "write", "me", "b")
        self.assertIsNone(got["can_land"])
        self.assertEqual(verdicts.verdict(self.APPROVED, "me", got)[2], "asks")

    def test_the_desk_attaches_the_gate_to_every_row(self):
        rows = fresh_desk().queue()["rows"]
        self.assertTrue(rows)
        self.assertTrue(all("gate" in row for row in rows))


class Blocks(unittest.TestCase):
    """Fetch carries facts; explicit triage publishes the verdict blocks."""

    def setUp(self):
        deskstate.save(REPO, {})

    def exported(self, desk):
        return json.loads(Path(desk.run_triage()).read_text())

    def test_every_row_lands_in_exactly_one_block(self):
        exported = self.exported(fresh_desk())
        rows, blocks = exported["queue"], exported["grid"]["blocks"]
        placed = [r["n"] for b in blocks for r in b["rows"]]
        self.assertEqual(sorted(placed), sorted(r["n"] for r in rows))
        self.assertEqual(len(placed), len(set(placed)), "a row in two blocks")

    def test_the_five_titles_are_always_present_and_in_order(self):
        blocks = self.exported(fresh_desk())["grid"]["blocks"]
        self.assertEqual([b["title"] for b in blocks], list(verdicts.BLOCK_TITLES))

    def test_an_a1_goes_first_and_a_waiting_row_goes_last(self):
        rows = [{"n": 1, "autorun": "A1", "state": "ready", "todo": "merge it"},
                {"n": 2, "autorun": "-", "state": "waiting", "todo": "waiting on x"}]
        self.assertEqual(verdicts.block_of(rows[0]), "Da mergiare subito")
        self.assertEqual(verdicts.block_of(rows[1]), "In attesa di altri")

    def test_fetch_does_not_publish_a_triage_grid(self):
        queue = fresh_desk().queue()
        self.assertIsNone(queue["grid"])
        self.assertFalse(queue["triage_complete"])
        self.assertEqual(queue["triage_counts"]["missing"], len(queue["rows"]))

    def test_publishing_the_export_marks_every_row_current(self):
        desk = fresh_desk()
        self.exported(desk)          # the run publishes the grid itself
        queue = desk.queue()
        self.assertTrue(queue["triage_complete"])
        self.assertTrue(all(r["triage_status"] == "current"
                            for r in queue["rows"]))
        self.assertEqual(len(queue["grid"]["blocks"]), 5)

    def test_a_mismatched_fingerprint_stales_only_that_pr(self):
        desk = fresh_desk()
        self.exported(desk)
        state = deskstate.load(REPO)
        first = next(r for b in state["grid"]["blocks"] for r in b["rows"])
        first["triage_key"] = "old"
        deskstate.save(REPO, state)
        queue = desk.queue()
        stale = [r for r in queue["rows"] if r["triage_status"] == "stale"]
        self.assertEqual([r["n"] for r in stale], [first["n"]])
        self.assertFalse(queue["triage_complete"])
        self.assertEqual(sum(len(b["rows"]) for b in queue["grid"]["blocks"]),
                         len(queue["rows"]) - 1)
        self.assertEqual(queue["chase"], {})


class TriageOwnership(unittest.TestCase):
    """The desk computes the grid and publishes it; a model adds per-PR facts.
    Copying a 50-row grid back verbatim through a model cost a turn and lost a
    row whenever the copy slipped."""

    def setUp(self):
        deskstate.save(REPO, {})

    def test_the_run_publishes_the_grid_and_the_chase_itself(self):
        desk = fresh_desk()
        desk.run_triage()
        state = deskstate.load(REPO)
        self.assertEqual(len(state["grid"]["blocks"]), 5)
        self.assertIn("chase", state)
        self.assertTrue(desk.queue()["triage_complete"])

    def test_the_issue_desk_never_publishes_a_pr_grid(self):
        fresh_desk(kind="issue").run_triage()
        self.assertNotIn("grid", deskstate.load(REPO))

    def test_the_model_line_reaches_the_published_grid(self):
        desk = fresh_desk()
        desk.run_triage()
        n = desk.queue()["rows"][0]["n"]
        state = deskstate.load(REPO)
        state["prs"] = {str(n): {"what": "riscrive il dispatch dei trigger"}}
        deskstate.save(REPO, state)
        rows = [row for block in desk.queue()["grid"]["blocks"]
                for row in block["rows"] if row["n"] == n]
        self.assertEqual(rows[0]["what"], "riscrive il dispatch dei trigger")

    def test_a_malformed_published_row_costs_that_row_only(self):
        desk = fresh_desk()
        desk.run_triage()
        state = deskstate.load(REPO)
        state["grid"]["blocks"][0]["rows"].append({"what": "no number here"})
        deskstate.save(REPO, state)
        queue = desk.queue()          # used to raise, and the desk went blank
        self.assertTrue(queue["rows"])

    def test_a_conflict_read_by_a_model_upgrades_the_verdict(self):
        """conflict_kind is the one fact the engine cannot read off a field —
        and until it had a writer, no DIRTY row could ever reach A3."""
        desk = fresh_desk()
        dirty = [r for r in desk.queue()["rows"]
                 if r["merge"] == "DIRTY" and r["author"] == desk.me
                 and not r["incomplete"]]
        self.assertTrue(dirty, "the fixture needs a DIRTY PR of his own")
        n = dirty[0]["n"]
        deskstate.save(REPO, {"prs": {str(n): {"conflict_kind": "mechanical"}}})
        desk.run_triage()
        row = next(r for r in desk.queue()["rows"] if r["n"] == n)
        self.assertEqual(row["autorun"], "A3")


class RelaunchStartsUntriaged(unittest.TestCase):
    """A relaunch is a request for the truth now. Re-publishing the grid is
    one press and no model turn, so nothing is carried over silently;
    --keep-state is the way to keep it."""

    def test_a_relaunch_drops_the_published_grid(self):
        desk = fresh_desk()
        desk.run_triage()
        deskstate.reset(REPO)
        self.assertNotIn("grid", deskstate.load(REPO))
        self.assertFalse(desk.queue()["triage_complete"])


class TriageKey(unittest.TestCase):
    """The fingerprint covers the fields a verdict READS. Hashing the whole
    row expired a triage on a title edit, which is churn, not news."""

    ROW = {"n": 1, "author": "me", "draft": False, "base": "develop",
           "merge": "CLEAN", "decision": "APPROVED", "req": [], "reviews": [],
           "unresolved": 0, "incomplete": False, "assignees": ["me"],
           "last": None, "title": "one", "summary": "body", "url": "u"}

    def test_an_edited_title_or_body_does_not_expire_a_triage(self):
        other = dict(self.ROW, title="two", summary="rewritten", url="v")
        self.assertEqual(prdesk.triage_key(self.ROW, None),
                         prdesk.triage_key(other, None))

    def test_a_changed_verdict_input_does(self):
        for field, value in (("merge", "DIRTY"), ("decision", None),
                             ("req", ["cgabriel"]), ("draft", True),
                             ("conflict_kind", "mechanical")):
            with self.subTest(field=field):
                self.assertNotEqual(
                    prdesk.triage_key(self.ROW, None),
                    prdesk.triage_key(dict(self.ROW, **{field: value}), None))

    def test_the_gate_is_part_of_it(self):
        self.assertNotEqual(
            prdesk.triage_key(self.ROW, None),
            prdesk.triage_key(self.ROW, {"branch": "develop", "can_land": False}))


class Chase(unittest.TestCase):
    def test_grouped_per_person_oldest_first_with_the_dates(self):
        rows = [{"n": 2, "state": "waiting", "waiting_on": "genro",
                 "author": "me", "created": "2026-05-01", "title": "b"},
                {"n": 1, "state": "waiting", "waiting_on": "genro",
                 "author": "me", "created": "2026-01-01", "title": "a"}]
        got = verdicts.chase(rows, "me")
        self.assertIn("genro", got)
        self.assertIn("2026-01-01", got["genro"].splitlines()[0])
        self.assertTrue(got["genro"].splitlines()[1].startswith("#1 (2026-01-01)"))

    def test_somebody_elses_pr_never_enters_a_block(self):
        """A chase list is about HIS PRs. Another author's PR waiting on its
        author is that author's queue, not something to hand out."""
        rows = [{"n": 5, "state": "waiting", "waiting_on": "dgpaci",
                 "author": "dgpaci", "created": "2026-04-04", "title": "his"},
                {"n": 6, "state": "waiting", "waiting_on": "genro",
                 "author": "dgpaci", "created": "2026-04-05", "title": "his too"}]
        self.assertEqual(verdicts.chase(rows, "me"), {})

    def test_the_person_who_may_land_becomes_the_chase(self):
        """Read from the fields and the gate, never by parsing the verdict's
        own prose back out — that coupling breaks when the wording changes."""
        row = {"n": 3, "author": "me", "draft": False, "created": "2026-02-02",
               "assignees": ["me"], "title": "c", "req": [], "reviews": [],
               "decision": "APPROVED",
               "merge": "CLEAN", "unresolved": 0, "last": None, "base": "develop"}
        gate = {"branch": "develop", "protected": True, "landers": ["lander"],
                "can_land": False, "as_admin": False,
                "conversation_resolution": False}
        rows = verdicts.decorate([row], "me", {"develop": gate})
        self.assertEqual(rows[0]["waiting_on"], "lander")
        self.assertIn("lander", verdicts.chase(rows, "me"))

    def test_a_chase_never_names_the_user_himself(self):
        row = {"n": 4, "author": "me", "draft": False, "created": "2026-03-03",
               "title": "d", "req": [], "reviews": [], "decision": None,
               "merge": "CLEAN", "unresolved": 0, "last": None, "base": "develop"}
        rows = verdicts.decorate([row], "me")
        self.assertNotIn("me", verdicts.chase(rows, "me"))


class IssueCrossCheck(unittest.TestCase):
    def test_a_branch_matches_on_the_number_not_on_a_prefix(self):
        branches = ["fix/812-empty-grid", "812-something", "feature/1812-other",
                    "fix/81-old", "wip/812_alt"]
        got = issuecheck.branches_for(812, branches)
        self.assertIn("fix/812-empty-grid", got)
        self.assertIn("812-something", got)
        self.assertIn("wip/812_alt", got)
        self.assertNotIn("feature/1812-other", got, "prefix drift")
        self.assertNotIn("fix/81-old", got)

    def test_collect_is_json_safe(self):
        """A set in here kills the cache write silently, and the whole
        cross-check then runs once per caller."""
        got = issuecheck.collect({"commented": [1], "assigned": [2]},
                                 ["fix/1-x"],
                                 [{"n": 9, "closes": [{"issue": 1, "assignees": []}]}])
        json.dumps(got)                     # must not raise
        self.assertEqual(got["open_prs"], {"1": [9]})

    def test_the_shortlist_drops_what_needs_no_model(self):
        rows = [
            {"n": 1, "assignees": [], "created": "2026-08-01",
             "cross": {"open_prs": [], "seen_by_me": False}},
            {"n": 2, "assignees": ["x"], "created": "2026-08-02",
             "cross": {"open_prs": [], "seen_by_me": False}},
            {"n": 3, "assignees": [], "created": "2026-08-03",
             "cross": {"open_prs": [7], "seen_by_me": False}},
            {"n": 4, "assignees": [], "created": "2026-08-04",
             "cross": {"open_prs": [], "seen_by_me": True}},
        ]
        self.assertEqual([r["n"] for r in issuecheck.shortlist(rows)], [1])

    def test_orphan_work_is_named_as_the_find_it_is(self):
        row = {"n": 812, "assignees": [], "title": "t"}
        issuecheck.annotate([row], {"commented": [], "assigned_to_me": [],
                                    "branches": ["feature/812-empty-grid"],
                                    "open_prs": {}})
        self.assertIn("lavoro fermo", row["cross"]["note"])

    def test_the_desk_computes_the_shortlist_and_says_so(self):
        got = fresh_desk().issues()
        self.assertTrue(got["shortlist_computed"])
        self.assertTrue(got["shortlist"]["rows"])
        self.assertTrue(all("cross" in row for row in got["rows"]))

    def test_the_cross_check_is_loaded_once_per_snapshot(self):
        desk = fresh_desk()
        calls = []
        original = desk.provider.issue_relations
        desk.provider.issue_relations = lambda *a: (calls.append(1), original(*a))[1]
        desk.snapshot()
        self.assertEqual(len(calls), 1, "the cache write is dropping the entry")


class RowsExport(unittest.TestCase):
    """What /api/rows hands the triage skill: everything the desk already
    knows, so the skill's job is only what a model can do."""

    def test_the_export_carries_the_computed_work(self):
        desk = fresh_desk()
        exported = json.loads(Path(desk.run_triage()).read_text())
        for key in ("queue", "issues", "grid", "chase", "gates", "shortlist"):
            self.assertIn(key, exported, key)
        self.assertEqual(len(exported["grid"]["blocks"]), 5)
        self.assertTrue(exported["shortlist"])
        self.assertLessEqual(len(exported["shortlist"]), 10)
        grid_rows = [row for block in exported["grid"]["blocks"]
                     for row in block["rows"]]
        self.assertTrue(all({"triage_key", "state", "todo", "autorun",
                             "waiting_on"} <= set(row)
                            for row in grid_rows))
        self.assertTrue(all("action" not in row for row in grid_rows),
                        "the command belongs to the live row, one source only")

    def test_the_shortlist_is_numbers_that_index_the_rows(self):
        """It used to be written twice in the same literal — the rows and
        then the numbers — so the payload silently carried whichever line
        came last. The numbers win: every one of them is already a full row
        under "issues", and repeating them doubled the export."""
        desk = fresh_desk()
        exported = json.loads(Path(desk.run_triage()).read_text())
        self.assertTrue(all(isinstance(n, int) for n in exported["shortlist"]))
        known = {r["n"] for r in exported["issues"]}
        self.assertTrue(set(exported["shortlist"]) <= known)


class HandOverExactlyOnce(unittest.TestCase):
    """A click hands work to the chat, which may take minutes. The lock lives
    on the SERVER: one kept in the page is lost on reload and in a second tab,
    and the user presses again because nothing visibly happened — every extra
    press being another event the chat has to work through."""

    def setUp(self):
        state = deskstate.load(REPO)
        state.pop("requests", None)
        state.pop("orders", None)
        deskstate.save(REPO, state)

    def test_a_second_press_is_refused_while_the_first_is_out(self):
        first, created = deskstate.request(REPO, "analyze:7", "analyze", 7)
        self.assertTrue(created)
        again, created = deskstate.request(REPO, "analyze:7", "analyze", 7)
        self.assertFalse(created)
        self.assertEqual(again["at"], first["at"], "a new record was minted")

    def test_a_closed_request_can_be_pressed_again(self):
        deskstate.request(REPO, "analyze:7", "analyze", 7)
        deskstate.close_request(REPO, "analyze:7", "done", "letto il diff")
        _, created = deskstate.request(REPO, "analyze:7", "analyze", 7)
        self.assertTrue(created)

    def test_a_lock_the_chat_never_closed_goes_stale_instead_of_wedging(self):
        deskstate.request(REPO, "analyze:9", "analyze", 9)
        state = deskstate.load(REPO)
        state["requests"]["analyze:9"]["epoch"] -= deskstate.REQUEST_STALE + 10
        deskstate.save(REPO, state)
        _, created = deskstate.request(REPO, "analyze:9", "analyze", 9)
        self.assertTrue(created, "a dead chat must not lock the button forever")

    def test_the_outcome_reaches_the_row(self):
        deskstate.request(REPO, "analyze:11", "analyze", 11)
        deskstate.close_request(REPO, "analyze:11", "done", "niente da rispondere")
        rows = [{"n": 11}]
        deskstate.annotate_requests(rows, deskstate.load(REPO))
        got = rows[0]["requests"]["analyze"]
        self.assertEqual(got["status"], "done")
        self.assertEqual(got["report"], "niente da rispondere")
        self.assertIn("closed_at", got)

    def test_a_failure_is_reported_as_a_failure(self):
        deskstate.request(REPO, "order:12", "order", 12)
        deskstate.close_request(REPO, "order:12", "failed", "gate non passato")
        rows = [{"n": 12}]
        deskstate.annotate_requests(rows, deskstate.load(REPO))
        self.assertEqual(rows[0]["requests"]["order"]["status"], "failed")

    def test_requests_land_on_the_right_row_only(self):
        deskstate.request(REPO, "analyze:100", "analyze", 100)
        rows = [{"n": 100}, {"n": 1000}, {"n": 10}]
        deskstate.annotate_requests(rows, deskstate.load(REPO))
        self.assertIn("analyze", rows[0]["requests"])
        self.assertEqual(rows[1]["requests"], {})
        self.assertEqual(rows[2]["requests"], {})

    def test_notify_closes_a_request_from_the_command_line(self):
        """This is how the chat reports back — the desk shows what it says."""
        deskstate.request(REPO, "analyze:13", "analyze", 13)
        out = subprocess.run(
            (sys.executable, str(ROOT / "notify.py"), "--repo", REPO,
             "--done", "analyze:13", "fatto"),
            capture_output=True, text=True, env=dict(os.environ, HOME=_HOME))
        self.assertEqual(out.returncode, 0, out.stderr)
        got = deskstate.load(REPO)["requests"]["analyze:13"]
        self.assertEqual((got["status"], got["report"]), ("done", "fatto"))


class ChatButtonsThroughTheLedger(unittest.TestCase):
    """The HTTP side of the same promise: the ledger is checked BEFORE the
    inbox, so a duplicate press never becomes a duplicate event."""

    @classmethod
    def setUpClass(cls):
        cls.desk = fresh_desk(chat=True, kind="pr")
        prdesk.Handler.desk = cls.desk
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), prdesk.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        state = deskstate.load(REPO)
        state.pop("requests", None)
        deskstate.save(REPO, state)
        inbox.truncate(REPO)

    def post(self, path, body=None):
        req = Request("http://127.0.0.1:%s%s" % (self.port, path),
                      data=json.dumps(body or {}).encode(),
                      headers={"Content-Type": "application/json",
                               "X-Git-Workflow-Token": self.desk.write_token})
        try:
            with urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def events(self):
        raw = inbox.inbox_path(REPO)
        if not raw.exists() or not raw.stat().st_size:
            return []
        return [json.loads(line) for line in raw.read_text().splitlines() if line]

    def test_one_press_one_event(self):
        status, payload = self.post("/api/pr/1145/analyze")
        self.assertEqual(status, 202)
        self.assertTrue(payload["queued"])
        self.assertEqual(len(self.events()), 1)

    def test_five_presses_still_one_event(self):
        for _ in range(5):
            self.post("/api/pr/1145/analyze")
        self.assertEqual(len(self.events()), 1, "the chat got duplicates")

    def test_the_refusal_hands_back_the_outstanding_request(self):
        self.post("/api/pr/1145/analyze")
        status, payload = self.post("/api/pr/1145/analyze")
        self.assertEqual(status, 200)
        self.assertTrue(payload["already"])
        self.assertEqual(payload["request"]["status"], "queued")

    def test_every_chat_button_is_covered(self):
        for path, body in (("/api/pr/1145/analyze", {}),
                           ("/api/pr/1145/explain", {}),
                           ("/api/pr/1145/order", {"propose": "x"}),
                           ("/api/issue/1166/analyze", {}),
                           ("/api/triage", {"flow": "pr-triage"}),
                           ("/api/run", {"flow": "pr-loop"})):
            inbox.truncate(REPO)
            state = deskstate.load(REPO)
            state.pop("requests", None)
            deskstate.save(REPO, state)
            self.post(path, body)
            self.post(path, body)
            self.assertEqual(len(self.events()), 1, path)

    def test_chosen_rows_reach_the_chat_with_the_batch_size(self):
        """Picking rows in the desk IS the answer to which ones: the loop
        must receive them, in that order, and be told how many to propose
        together."""
        inbox.truncate(REPO)
        state = deskstate.load(REPO)
        state.pop("requests", None)
        deskstate.save(REPO, state)
        self.post("/api/run", {"flow": "pr-loop", "ns": [1145, 1128], "batch": 2})
        event = self.events()[-1]
        self.assertEqual(event["ns"], [1145, 1128])
        self.assertEqual(event["batch"], 2)

    def test_the_batch_is_clamped_where_the_answer_box_ends(self):
        """The page is an input like any other: four is what one
        decision group holds."""
        inbox.truncate(REPO)
        state = deskstate.load(REPO)
        state.pop("requests", None)
        deskstate.save(REPO, state)
        self.post("/api/run", {"flow": "pr-loop", "ns": [1, 2, 3, 4, 5, 6],
                               "batch": 99})
        self.assertEqual(self.events()[-1]["batch"], prdesk.MAX_BATCH)

    def test_a_whole_queue_run_names_no_rows(self):
        inbox.truncate(REPO)
        state = deskstate.load(REPO)
        state.pop("requests", None)
        deskstate.save(REPO, state)
        self.post("/api/run", {"flow": "pr-loop"})
        event = self.events()[-1]
        self.assertEqual(event["ns"], [])
        self.assertEqual(event["batch"], 1, "one at a time is the default")

    def test_a_closed_request_lets_the_button_work_again(self):
        self.post("/api/pr/1145/analyze")
        deskstate.close_request(REPO, "analyze:1145", "done", "ok")
        inbox.truncate(REPO)
        status, payload = self.post("/api/pr/1145/analyze")
        self.assertEqual(status, 202)
        self.assertEqual(len(self.events()), 1)

    def test_outstanding_flows_reach_the_ui(self):
        self.post("/api/triage", {"flow": "pr-triage"})
        flows = self.desk.live_state()["flows"]
        self.assertEqual(flows["pr-triage"]["status"], "queued")


class SummaryFromData(unittest.TestCase):
    """What a PR is FOR: the author already wrote it. Asking a model to
    paraphrase 52 titles was the expensive way to learn it."""

    def test_rows_carry_the_author_s_own_description(self):
        rows = fresh_desk().queue()["rows"]
        with_summary = [r for r in rows if r.get("summary")]
        self.assertTrue(with_summary, "no row carried a summary")

    def test_the_summary_is_trimmed_not_the_whole_body(self):
        from providers.github import SUMMARY_CHARS, _summary
        self.assertIsNone(_summary(""))
        self.assertIsNone(_summary(None))
        self.assertEqual(_summary("  one   two\n\nthree "), "one two three")
        long = "Problem. " + ("word " * 400)
        got = _summary(long)
        self.assertLess(len(got), SUMMARY_CHARS + 20)
        self.assertTrue(got.endswith("…"))

    def test_closing_issues_carry_their_titles(self):
        rows = fresh_desk().queue()["rows"]
        closes = [c for r in rows for c in (r.get("closes") or [])]
        self.assertTrue(closes)
        self.assertTrue(any(c.get("title") for c in closes),
                        "a closed issue's title is what makes the link readable")


class WorkingMarker(unittest.TestCase):
    """pr-loop walks the queue one PR at a time; the desk should show which
    row is under the needle without the user reading the feed line by line."""

    def tearDown(self):
        deskstate.clear_working(REPO)

    def test_the_marker_names_the_row_and_what_is_happening(self):
        deskstate.set_working(REPO, 1145, "leggo il diff")
        got = deskstate.working(REPO)
        self.assertEqual(got["n"], 1145)
        self.assertEqual(got["msg"], "leggo il diff")
        self.assertIn("at", got)

    def test_it_moves_as_the_run_moves(self):
        deskstate.set_working(REPO, 1145, "primo")
        deskstate.set_working(REPO, 1128, "secondo")
        self.assertEqual(deskstate.working(REPO)["n"], 1128)

    def test_a_stale_marker_is_dropped_rather_than_left_glowing(self):
        """A highlight stuck on a row after the run died reads as work in
        progress, which is worse than no highlight."""
        deskstate.set_working(REPO, 1145, "…")
        state = deskstate.load(REPO)
        state["working"]["epoch"] -= deskstate.WORKING_STALE + 10
        deskstate.save(REPO, state)
        self.assertIsNone(deskstate.working(REPO))

    def test_it_clears_when_the_run_is_over(self):
        deskstate.set_working(REPO, 1145, "…")
        deskstate.clear_working(REPO)
        self.assertIsNone(deskstate.working(REPO))

    def test_it_reaches_the_ui_through_the_state_endpoint(self):
        deskstate.set_working(REPO, 1145, "riallineo il branch")
        got = fresh_desk().live_state()["working"]
        self.assertEqual(got["n"], 1145)

    def test_notify_sets_and_clears_it_from_the_command_line(self):
        env = dict(os.environ, HOME=_HOME)
        run = lambda *a: subprocess.run(                       # noqa: E731
            (sys.executable, str(ROOT / "notify.py"), "--repo", REPO) + a,
            capture_output=True, text=True, env=env)
        out = run("--pr", "1145", "--working", "leggo il diff")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(deskstate.working(REPO)["n"], 1145)
        out = run("--idle", "coda svuotata")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIsNone(deskstate.working(REPO))

    def test_a_batch_marks_every_row_it_is_working(self):
        """N agents in N worktrees: a marker naming one of them leaves the
        other rows reading as idle, which is the one thing the desk exists
        to prevent."""
        deskstate.set_working_batch(REPO, [1145, 1128, 1059], "3 in parallelo")
        got = deskstate.working(REPO)
        self.assertEqual(got["ns"], [1145, 1128, 1059])
        self.assertEqual(got["n"], 1145, "the first stays readable as `n`")

    def test_a_single_marker_still_reads_as_a_set_of_one(self):
        """The UI and the tests read one field, not two code paths."""
        deskstate.set_working(REPO, 1145, "leggo il diff")
        self.assertEqual(deskstate.working(REPO)["ns"], [1145])

    def test_progress_on_one_item_does_not_collapse_the_batch(self):
        """Per-PR progress must reach the desk without dropping the other
        rows back to idle — otherwise the loop cannot report as it goes."""
        deskstate.set_working_batch(REPO, [1145, 1128, 1059], "3 in parallelo")
        deskstate.set_working(REPO, 1128, "worktree pronto, giro i test")
        got = deskstate.working(REPO)
        self.assertEqual(got["ns"], [1145, 1128, 1059])
        self.assertEqual(got["items"]["1128"], "worktree pronto, giro i test")

    def test_a_number_outside_the_batch_replaces_it(self):
        """Moving on to a PR the batch never held is a new marker, not a
        fourth member of the old one."""
        deskstate.set_working_batch(REPO, [1145, 1128], "2 in parallelo")
        deskstate.set_working(REPO, 1059, "la prossima")
        self.assertEqual(deskstate.working(REPO)["ns"], [1059])

    def test_an_empty_batch_leaves_no_headless_marker(self):
        deskstate.set_working(REPO, 1145, "…")
        self.assertIsNone(deskstate.set_working_batch(REPO, []))
        self.assertIsNone(deskstate.working(REPO))

    def test_notify_marks_a_batch_from_the_command_line(self):
        out = subprocess.run(
            (sys.executable, str(ROOT / "notify.py"), "--repo", REPO,
             "--batch", "1145,#1128", "--working", "fix in parallelo"),
            capture_output=True, text=True, env=dict(os.environ, HOME=_HOME))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(deskstate.working(REPO)["ns"], [1145, 1128])

    def test_closing_a_request_drops_the_highlight_too(self):
        """The run ends by closing its request; a marker left behind would
        keep glowing on a row nobody is touching."""
        deskstate.request(REPO, "run:pr-loop", "run", None, "pr-loop")
        deskstate.set_working(REPO, 1145, "…")
        out = subprocess.run(
            (sys.executable, str(ROOT / "notify.py"), "--repo", REPO,
             "--done", "run:pr-loop", "coda svuotata"),
            capture_output=True, text=True, env=dict(os.environ, HOME=_HOME))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIsNone(deskstate.working(REPO))
