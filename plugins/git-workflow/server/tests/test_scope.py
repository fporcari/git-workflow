"""One desk over several repositories: the scope, the merge, the threads,
the routing of every click to the repository it names, and the chat ear
that listens to all of them."""

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIXTURE = str(ROOT / "tests" / "fixtures" / "scope.json")

import cache            # noqa: E402
import chatdesk         # noqa: E402
import deskstate        # noqa: E402
import jobs             # noqa: E402
import prdesk           # noqa: E402
import scope            # noqa: E402
import threads          # noqa: E402
from providers import get_provider  # noqa: E402
from providers.base import closes_from_body, refs_from_body  # noqa: E402

ENGINE, EXT = "acme/acme-engine", "acme/acme-ext"
_SAVED = {}


def setUpModule():
    """Throwaway state for this module only: the other modules set theirs at
    import, and their command-line tests hand that HOME to subprocesses."""
    home = tempfile.mkdtemp(prefix="deskscope-")
    _SAVED.update(home=os.environ.get("HOME"), state=deskstate.STATE_DIR,
                  runtime=deskstate.RUNTIME_DIR)
    os.environ["HOME"] = home
    deskstate.STATE_DIR = Path(home) / ".local" / "state" / "git-workflow"
    deskstate.RUNTIME_DIR = Path(home) / "runtime"


def tearDownModule():
    if _SAVED.get("home") is not None:
        os.environ["HOME"] = _SAVED["home"]
    deskstate.STATE_DIR = _SAVED["state"]
    deskstate.RUNTIME_DIR = _SAVED["runtime"]


def scope_desk(clone_ext=False):
    with mock.patch.dict(os.environ, {"DESK_FIXTURE": FIXTURE}):
        provider = get_provider("fixture")
    for repo in (ENGINE, EXT):
        cache.clear(repo)
        deskstate.save(repo, {})
    desks = [prdesk.Desk(provider, ENGINE, "fp", str(ROOT), clone=True),
             prdesk.Desk(provider, EXT, "fp", str(ROOT), clone=clone_ext)]
    return prdesk.ScopeDesk("acme", desks, "fp")


class References(unittest.TestCase):
    def test_a_qualified_reference_names_its_repository(self):
        body = "Fixes #12, closes acme/acme-ext#26; see other/thing#1374, #12 and page#3"
        self.assertEqual(closes_from_body(body),
                         [{"issue": 12, "source": "body"},
                          {"issue": 26, "source": "body", "repo": "acme/acme-ext"}])
        self.assertEqual(refs_from_body(body),
                         [{"repo": None, "n": 12}, {"repo": "acme/acme-ext", "n": 26},
                          {"repo": "other/thing", "n": 1374}])

    def test_a_url_fragment_is_not_a_reference(self):
        self.assertEqual(refs_from_body("https://forge.example/a/b#4 and x/y#5z"), [])


class ScopeResolution(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="clones-"))
        for name, origin in (("acme-engine", "https://forge.example/acme/acme-engine.git"),
                             ("acme-ext", "git@forge.example:acme/acme-ext.git"),
                             ("notes", None)):
            path = self.root / name
            path.mkdir()
            if origin:
                subprocess.run(("git", "init", "-q", str(path)), check=True)
                subprocess.run(("git", "-C", str(path), "remote", "add", "origin", origin),
                               check=True)

    def fake_provider(self, name, host):
        provider = mock.Mock()
        provider.scope_repos.return_value = [ENGINE, EXT, "acme/acme-docs"]
        return provider

    def test_a_folder_of_clones_is_a_scope_of_its_own(self):
        with mock.patch("scope.provider_for", return_value="forgejo"):
            name, members = scope.build(cwd=self.root, get_provider=self.fake_provider)
        self.assertEqual(name, self.root.name)
        self.assertEqual([m["repo"] for m in members], [ENGINE, EXT])
        self.assertTrue(all(m["cwd"] and m["host"] == "forge.example" for m in members))

    def test_a_folder_nobody_can_read_is_not_a_clone_and_breaks_nothing(self):
        locked = self.root / "locked"
        locked.mkdir()
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o755)
        with mock.patch("scope.provider_for", return_value="forgejo"):
            _, members = scope.build(cwd=self.root, get_provider=self.fake_provider)
        self.assertEqual([m["repo"] for m in members], [ENGINE, EXT])
        self.assertEqual(scope.clones(locked), {})

    def test_an_org_reads_its_repositories_and_finds_their_clones(self):
        with mock.patch("scope.provider_for", return_value="forgejo"):
            name, members = scope.build(orgs=["acme"], cwd=self.root,
                                        get_provider=self.fake_provider)
        self.assertEqual(name, "acme")
        found = {m["repo"]: m["cwd"] for m in members}
        self.assertEqual(found[ENGINE], str((self.root / "acme-engine").resolve()))
        self.assertIsNone(found["acme/acme-docs"], "no clone: read only")
        self.assertTrue(all(m["host"] == "forge.example" for m in members))

    def test_one_checkout_is_still_the_one_repository(self):
        with mock.patch("scope.provider_for", return_value="forgejo"), \
             mock.patch("providers.detect.provider_for", return_value="forgejo"):
            name, members = scope.build(cwd=self.root / "acme-ext",
                                        get_provider=self.fake_provider)
        self.assertEqual((name, len(members)), (EXT, 1))
        self.assertEqual(members[0]["cwd"], str((self.root / "acme-ext").resolve()))

    def test_the_fixture_serves_an_owner(self):
        with mock.patch.dict(os.environ, {"DESK_FIXTURE": FIXTURE}):
            self.assertEqual(get_provider("fixture").scope_repos("acme"), [ENGINE, EXT])

    def test_short_names_drop_the_owner_and_the_common_prefix(self):
        self.assertEqual(prdesk.repo_labels([ENGINE, EXT]),
                         {ENGINE: "engine", EXT: "ext"})
        self.assertEqual(prdesk.repo_labels(["acme/web", "other/web"]),
                         {"acme/web": "acme/web", "other/web": "other/web"})


class Merge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.desk = scope_desk()
        cls.snap = cls.desk.snapshot()

    def test_every_row_says_its_repository(self):
        rows = self.snap["queue"]["rows"] + self.snap["issues"]["rows"]
        self.assertTrue(rows)
        self.assertTrue(all(r["repo"] in (ENGINE, EXT) for r in rows))
        self.assertIn("ext #29", [r["label"] for r in self.snap["queue"]["rows"]])

    def test_the_meta_carries_the_scope_and_which_members_have_a_clone(self):
        members = {m["repo"]: m for m in self.snap["meta"]["scope"]["members"]}
        self.assertEqual(self.snap["meta"]["scope"]["name"], "acme")
        self.assertTrue(members[ENGINE]["clone"])
        self.assertFalse(members[EXT]["clone"])
        self.assertEqual(members[EXT]["label"], "ext")

    def test_a_closed_issue_gets_its_assignees_across_repositories(self):
        pr = next(r for r in self.snap["queue"]["rows"] if r["repo"] == EXT and r["n"] == 31)
        self.assertEqual(pr["closes"][0]["repo"], ENGINE)
        self.assertEqual(pr["closes"][0]["assignees"], ["fp"])

    def test_an_issue_another_repository_closes_is_not_up_for_grabs(self):
        listed = {(r["repo"], r["n"]) for r in self.snap["issues"]["rows"]}
        self.assertNotIn((ENGINE, 19), listed)
        moved = next(r for r in self.snap["issues"]["others"] if r["n"] == 19)
        self.assertEqual(moved["excluded"], "with_pr")
        self.assertIn("ext #31", moved["cross"]["note"])

    def test_counts_add_up_across_members(self):
        issues = self.snap["issues"]
        self.assertEqual(issues["total"], 7)
        self.assertEqual(len(issues["rows"]) + len(issues["others"]), 7)
        self.assertEqual(self.snap["queue"]["total"], 4)

    def test_threads_pair_every_issue_with_its_pr(self):
        found = {t["key"]: t for g in self.snap["threads"]["groups"] for t in g["threads"]}
        self.assertEqual([p["n"] for p in found["acme/acme-engine#19"]["prs"]], [31])
        self.assertEqual(found["acme/acme-engine#19"]["cited_by"], ["acme/acme-engine#23"])
        self.assertEqual(found["acme/acme-engine#66"]["same_title"], ["acme/acme-engine#65"])
        self.assertEqual(self.snap["threads"]["total"], 7)

    def test_an_untriaged_pr_keeps_its_thread_without_a_verdict(self):
        groups = {g["id"]: g for g in self.snap["threads"]["groups"]}
        self.assertIn("untriaged", groups)
        self.assertTrue(all(t["move"] == "senza verdetto" for t in groups["untriaged"]["threads"]))
        self.assertEqual(groups["me"]["threads"][0]["move"], "iniziala")
        self.assertEqual(groups["rob"]["threads"][0]["key"], "acme/acme-engine#20")

    def test_a_member_is_named_or_the_request_is_refused(self):
        self.assertIs(self.desk.member("ACME/acme-ext"), self.desk.desks[1])
        with self.assertRaises(KeyError):
            self.desk.member()
        with self.assertRaises(KeyError):
            self.desk.member("acme/elsewhere")


class Threads(unittest.TestCase):
    def pr(self, n, repo, state, todo="", waiting_on=None, closes=()):
        return {"repo": repo, "n": n, "title": "t", "state": state, "todo": todo,
                "waiting_on": waiting_on, "triage_status": "current", "created": "2026-10-0%s" % n,
                "closes": [{"issue": c} for c in closes]}

    def issue(self, n, repo, assignees=()):
        return {"repo": repo, "n": n, "title": "i%s" % n, "assignees": list(assignees),
                "created": "2026-09-0%s" % n, "refs": [], "prs": [], "prs_elsewhere": []}

    def test_a_thread_belongs_to_whoever_must_move(self):
        got = threads.build(
            [self.pr(1, ENGINE, "attention", "review it", closes=[5]),
             self.pr(2, ENGINE, "waiting", "waiting on rob", "rob", closes=[6]),
             self.pr(3, ENGINE, "waiting", "waiting on rob", "rob")],
            [self.issue(5, ENGINE), self.issue(6, ENGINE), self.issue(7, ENGINE, ["fp"])],
            "fp")
        groups = {g["id"]: [t["key"] for t in g["threads"]] for g in got["groups"]}
        self.assertEqual(groups["me"], ["acme/acme-engine#7", "acme/acme-engine#5"],
                         "newest first")
        self.assertEqual(sorted(groups["rob"]), ["acme/acme-engine#3", "acme/acme-engine#6"])
        self.assertEqual([g["id"] for g in got["groups"]][0], "me")
        self.assertEqual(got["people"]["rob"]["prs"], ["acme/acme-engine#2", "acme/acme-engine#3"])

    def test_the_cached_rows_are_never_mutated(self):
        closes = [{"issue": 5, "assignees": None}]
        pr = {"repo": ENGINE, "n": 1, "closes": closes}
        threads.link_closes([pr], [self.issue(5, ENGINE, ["rob"])])
        self.assertIsNone(closes[0]["assignees"])
        self.assertEqual(pr["closes"][0]["assignees"], ["rob"])


class Http(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        prdesk.Handler.desk = scope_desk()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), prdesk.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def post(self, path, body=None):
        req = Request("http://127.0.0.1:%s%s" % (self.port, path),
                      data=json.dumps(body or {}).encode(),
                      headers={"Content-Type": "application/json",
                               "X-Git-Workflow-Token": prdesk.Handler.desk.write_token})
        try:
            with urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def get(self, path):
        with urlopen("http://127.0.0.1:%s%s" % (self.port, path), timeout=30) as resp:
            return json.loads(resp.read())

    def test_the_snapshot_is_the_whole_scope(self):
        snap = self.get("/api/desk")
        self.assertEqual(snap["meta"]["repo"], "acme")
        self.assertEqual({r["repo"] for r in snap["queue"]["rows"]}, {ENGINE, EXT})
        self.assertIn("groups", snap["threads"])

    def test_a_click_without_its_repository_is_refused(self):
        status, body = self.post("/api/pr/79/analyze")
        self.assertEqual(status, 400)
        self.assertIn("repo", body["error"])

    def test_a_click_goes_to_the_member_it_names(self):
        with mock.patch.object(jobs, "analyze_issue", return_value="job-1") as started:
            status, body = self.post("/api/issue/28/analyze", {"repo": EXT})
        self.assertEqual((status, body["job"]), (202, "job-1"))
        self.assertEqual(started.call_args.args[:2], (EXT, 28))

    def test_a_write_on_a_repository_without_a_clone_is_refused(self):
        with mock.patch.object(jobs, "operation") as started:
            status, body = self.post("/api/pr/29/order", {"repo": EXT, "propose": "merge"})
        self.assertEqual(status, 409)
        self.assertIn("clone", body["error"])
        started.assert_not_called()
        self.assertNotIn("orders", deskstate.load(EXT))

    def test_a_run_across_repositories_is_one_loop_per_repository(self):
        with mock.patch.object(prdesk.ScopeDesk, "member", wraps=prdesk.Handler.desk.member), \
             mock.patch.object(jobs, "operation", side_effect=["job-a", "job-b"]) as started:
            prdesk.Handler.desk.desks[1].clone = True
            try:
                status, body = self.post("/api/run", {"flow": "pr-loop", "items": [
                    {"repo": ENGINE, "n": 79}, {"repo": EXT, "n": 29}, {"repo": ENGINE, "n": 45}]})
            finally:
                prdesk.Handler.desk.desks[1].clone = False
        self.assertEqual(status, 202)
        self.assertEqual([(r["repo"], r["ns"]) for r in body["runs"]],
                         [(ENGINE, [79, 45]), (EXT, [29])])
        self.assertEqual([c.args[0] for c in started.call_args_list], [ENGINE, EXT])

    def test_a_triage_press_triages_every_member(self):
        with mock.patch.object(jobs, "triage", side_effect=["t1", "t2"]) as started:
            status, body = self.post("/api/triage", {"flow": "pr-triage"})
        self.assertEqual((status, body["jobs"]), (202, ["t1", "t2"]))
        self.assertEqual([c.args[0] for c in started.call_args_list], [ENGINE, EXT])

    def test_a_job_is_found_in_whichever_member_ran_it(self):
        with mock.patch.object(jobs, "get", side_effect=lambda repo, job: {"id": job, "repo": repo}
                               if repo == EXT else None):
            self.assertEqual(self.get("/api/job/abc")["repo"], EXT)


class ScopeChat(unittest.TestCase):
    def setUp(self):
        for repo in (ENGINE, EXT):
            deskstate.save(repo, {})

    def test_a_scope_is_registered_by_name(self):
        deskstate.register_scope("acme", "pr", 8399,
                                 [{"repo": ENGINE, "cwd": "/src/engine", "clone": True},
                                  {"repo": EXT, "cwd": None, "clone": False}])
        self.assertEqual(deskstate.scope_repos("acme"), [ENGINE, EXT])
        self.assertEqual(deskstate.scope_members("acme")[0]["cwd"], "/src/engine")
        with self.assertRaises(ValueError):
            deskstate.scope_repos("never-opened")

    def test_the_ear_claims_in_every_member_and_names_the_repository(self):
        deskstate.chat_heartbeat(EXT, "chat-1", "pr")
        deskstate.request(EXT, "analyze:29", "analyze", 29, "pr-analyze #29", via="chat")
        record = chatdesk.wait([{"repo": ENGINE, "cwd": "/src/engine"},
                                {"repo": EXT, "cwd": "/src/ext"}], 1, "chat-1", "pr")
        self.assertEqual((record["key"], record["repo"], record["cwd"]),
                         ("analyze:29", EXT, "/src/ext"))
        self.assertEqual(chatdesk.command_for(record), "/pr-analyze 29 --repo %s" % EXT)
        self.assertTrue(deskstate.chat_listening(ENGINE, desk="pr"), "heartbeat in every member")

    def test_one_repository_keeps_its_bare_command(self):
        deskstate.chat_heartbeat(ENGINE, "chat-2", "pr")
        deskstate.request(ENGINE, "analyze:79", "analyze", 79, "x", via="chat")
        record = chatdesk.wait(ENGINE, 1, "chat-2", "pr")
        self.assertEqual(chatdesk.command_for(record), "/pr-analyze 79")

    def test_closing_a_scope_stops_its_one_server_once(self):
        for repo in (ENGINE, EXT):
            deskstate.update(repo, lambda state: state.update(
                desks={"pr": {"pid": 424242, "port": 8399}}))
        out = io.StringIO()
        with mock.patch.object(deskstate, "_alive", return_value=True), \
             mock.patch.object(chatdesk.os, "kill") as killed:
            chatdesk.close([ENGINE, EXT], "chat-3", "pr", out=out, grace=0)
        self.assertEqual(killed.call_count, 1)
        self.assertIn("desk chiuso · %s + %s" % (ENGINE, EXT), out.getvalue())

    def test_a_scope_will_not_open_over_a_desk_already_serving_a_member(self):
        deskstate.update(ENGINE, lambda state: state.update(
            desks={"pr": {"pid": 424242, "port": 8401}}))
        with mock.patch.object(deskstate, "_alive", return_value=True):
            self.assertEqual(prdesk.held_elsewhere([ENGINE, EXT], "pr"), [(ENGINE, 8401)])


if __name__ == "__main__":
    unittest.main()
