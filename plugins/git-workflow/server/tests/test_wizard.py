"""The wizard: which step every row belongs to, read off the provider facts,
the engine's verdict and the analysis' stance — never off a model's copy."""

import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cache            # noqa: E402
import deskstate        # noqa: E402
import jobs             # noqa: E402
import prdesk           # noqa: E402
import wizard           # noqa: E402
from providers import get_provider  # noqa: E402

REPO = "genropy/genropy"
ME = "genro"
_SAVED = {}


def setUpModule():
    home = tempfile.mkdtemp(prefix="wizard-")
    _SAVED.update(home=os.environ.get("HOME"), state=deskstate.STATE_DIR,
                  runtime=deskstate.RUNTIME_DIR)
    os.environ["HOME"] = home
    deskstate.STATE_DIR = Path(home) / "state"
    deskstate.RUNTIME_DIR = Path(home) / "runtime"


def tearDownModule():
    if _SAVED.get("home") is not None:
        os.environ["HOME"] = _SAVED["home"]
    deskstate.STATE_DIR = _SAVED["state"]
    deskstate.RUNTIME_DIR = _SAVED["runtime"]


def fresh_desk(me=ME):
    cache.clear(REPO)
    deskstate.save(REPO, {})
    return prdesk.Desk(get_provider("fixture"), REPO, me, str(ROOT))


def analyze(desk, n, **verdict):
    rows = desk._queue_facts(complete_gates=True)[0]
    keys = next(row for row in rows if row["n"] == n)["model_keys"]
    result = {"n": n, "author": "a", "problem": "p", "history": "h",
              "propose": "x", "draft": None, "verified": [], "not_verified": [],
              "stance": "approve", "why": "w%d" % n}
    result.update(verdict)
    jobs.persist(REPO, result, dict(keys, **verdict.pop("keys", {})))


def steps(section):
    return {step["id"]: [card["n"] for card in step["rows"]]
            for step in section["steps"]}


class DaRivedere(unittest.TestCase):

    def setUp(self):
        self.desk = fresh_desk()
        self.review = self.desk.wizard()["review"]
        self.ns = sorted(card["n"] for card in self.review["pending"])

    def test_only_others_prs_whose_review_is_asked_and_all_pending_at_first(self):
        self.assertEqual(self.review["count"], 30)
        self.assertEqual(self.review["first"], "prepare")
        self.assertTrue(all(step["rows"] == [] for step in self.review["steps"]))
        authors = {card["author"] for card in self.review["pending"]}
        self.assertNotIn(ME, authors)

    def test_each_stance_lands_in_its_step_with_what_it_needs(self):
        a, c, d = self.ns[:3]
        analyze(self.desk, a)
        analyze(self.desk, c, stance="changes", draft="Please split it.")
        analyze(self.desk, d, stance="doubt", doubt="l'ordine cambia", lean="changes",
                draft="Please add a test.", hunk={"path": "a.js", "header": "@@ -1 +1 @@"})
        review = self.desk.wizard()["review"]
        self.assertEqual(steps(review)["approve"], [a])
        self.assertEqual(steps(review)["changes"], [c])
        self.assertEqual(steps(review)["doubt"], [d])
        self.assertEqual(review["first"], "approve")
        self.assertEqual(len(review["pending"]), 27)
        rows = {card["n"]: card for step in review["steps"] for card in step["rows"]}
        self.assertEqual(rows[a]["why"], "w%d" % a)
        self.assertEqual(rows[c]["draft"], "Please split it.")
        self.assertEqual(rows[d]["hunk"]["path"], "a.js")
        self.assertTrue(rows[a]["url"].endswith("/%d" % a))

    def test_a_failed_analysis_waits_among_the_doubts_with_the_reason(self):
        n = self.ns[0]
        deskstate.save(REPO, {"runs": {"pr-nightwork": {
            "status": "done", "failed": {str(n): "claude exited 3"}}}})
        review = self.desk.wizard()["review"]
        doubt = next(card for card in review["steps"][2]["rows"] if card["n"] == n)
        self.assertIn("claude exited 3", doubt["doubt"])
        self.assertNotIn(n, [card["n"] for card in review["pending"]])

    def test_needs_verification_is_never_approvable_on_tests_not_green(self):
        n = self.ns[0]
        source = next(row for row in self.desk.provider.data["rows"] if row["n"] == n)
        source.update(labels=["needs-verification"], head="h%d" % n)
        cache.clear(REPO)
        analyze(self.desk, n, keys={"checks": {"head": "h%d" % n, "state": "FAILURE"}})
        review = self.desk.wizard()["review"]
        self.assertEqual(steps(review)["approve"], [])
        held = review["steps"][2]["rows"][0]
        self.assertEqual((held["n"], held["lean"]), (n, "approve"))
        self.assertIn("needs-verification", held["doubt"])
        analyze(self.desk, n, keys={"checks": {"head": "h%d" % n, "state": "SUCCESS"}})
        self.assertEqual(steps(self.desk.wizard()["review"])["approve"], [n])

    def test_a_doubt_skipped_to_tomorrow_leaves_the_steps_for_today(self):
        n = self.ns[0]
        analyze(self.desk, n, stance="doubt", doubt="d", lean="approve")
        deskstate.update(REPO, lambda state: state["prs"][str(n)].update(
            skipped=date.today().isoformat()))
        review = self.desk.wizard()["review"]
        self.assertEqual(steps(review)["doubt"], [])
        self.assertEqual([card["n"] for card in review["skipped"]], [n])

    def test_a_pending_row_shows_what_its_job_is_doing(self):
        n = self.ns[0]
        job = {"kind": "analyze", "request": {"n": n},
               "progress": {"stage": "testing"}}
        queue = self.desk.queue()
        section = wizard.review_section(queue, deskstate.load(REPO), ME, [job])
        chip = next(card["chip"] for card in section["pending"] if card["n"] == n)
        self.assertEqual(chip, "verifica i test…")


class Mie(unittest.TestCase):
    """fporcari's own PRs on the fixture: none approvable, ever."""

    def setUp(self):
        self.desk = fresh_desk("fporcari")

    def mine(self):
        return self.desk.wizard()["mine"]

    def test_own_prs_are_split_by_whose_move_it_is(self):
        mine = self.mine()
        own = [row for row in self.desk.queue()["rows"] if row["author"] == "fporcari"]
        self.assertEqual(mine["count"], len(own))
        self.assertEqual({k: len(v) for k, v in steps(mine).items()},
                         {"merge": 0, "fix": 0, "decide": 8, "waiting": 28})
        self.assertEqual(mine["first"], "decide")
        self.assertIn("cgabriel", mine["chase"])
        self.assertNotIn(1145, steps(self.desk.wizard()["review"])["approve"])

    def test_a_clear_request_goes_to_claude_a_choice_comes_with_its_options(self):
        decide = steps(self.mine())["decide"]
        fix, choice = decide[:2]
        options = ["Dividila", "Rispondi a cgabriel", "Lascia com'è"]
        analyze(self.desk, fix, stance="fix")
        analyze(self.desk, choice, stance="decide", ask="Dividila in tre", options=options)
        mine = self.mine()
        self.assertEqual(steps(mine)["fix"], [fix])
        card = next(c for c in mine["steps"][2]["rows"] if c["n"] == choice)
        self.assertEqual((card["ask"], card["options"]), ("Dividila in tre", options))

    def test_an_approved_clean_pr_of_mine_is_to_merge(self):
        rows = self.desk.provider.data["rows"]
        row = next(r for r in rows if r["author"] == "fporcari" and not r["draft"]
                   and r.get("base") == "develop")
        row.update(decision="APPROVED", req=[], merge="CLEAN", assignees=["fporcari"],
                   head="hx", unresolved=0, last=None,
                   reviews=[{"who": "genro", "state": "APPROVED", "commit": "hx",
                             "has_text": False}])
        cache.clear(REPO)
        self.assertIn(row["n"], steps(self.mine())["merge"])

    def test_rows_a_loop_works_in_background_say_so(self):
        n = steps(self.mine())["decide"][0]
        deskstate.save(REPO, {"requests": {"run:pr-loop": {
            "status": "running", "payload": {"flow": "pr-loop", "ns": [n]}}}})
        card = next(c for c in self.mine()["steps"][2]["rows"] if c["n"] == n)
        self.assertEqual(card["loop"], "running")


def analyze_issue(n, **fields):
    record = {"n": n, "type": "DEFECT", "finding": "f%d" % n, "size": "EASY",
              "phase": "SINGLE-PHASE", "problem": "p", "cause": "c", "propose": "x",
              "verify": "v", "decision": None, "fixed_by": None}
    record.update(fields)
    jobs.persist_issue_analysis(REPO, record, n)


class Issues(unittest.TestCase):

    def setUp(self):
        self.desk = fresh_desk("fporcari")
        self.shortlist = [card["n"] for card in self.desk.wizard()["issue"]["pending"]]

    def test_only_the_shortlist_waits_for_the_preparation(self):
        issue = self.desk.wizard()["issue"]
        self.assertEqual(len(self.shortlist), 10)
        self.assertEqual(issue["first"], "prepare")
        self.assertEqual(issue["count"], len(self.desk.issues()["rows"]))

    def test_each_reading_lands_in_its_step(self):
        fixed, easy, long, taken = self.shortlist[:4]
        analyze_issue(fixed, fixed_by=1099)
        analyze_issue(easy)
        analyze_issue(long, size="MEDIUM", phase="WORKFLOW")
        analyze_issue(taken, decision="who owns the print templates?")
        issue = self.desk.wizard()["issue"]
        self.assertEqual(steps(issue)["close"], [fixed])
        self.assertEqual(steps(issue)["claude"], [easy])
        self.assertEqual(sorted(steps(issue)["decide"]), sorted([long, taken]))
        self.assertEqual(issue["first"], "close")
        close = issue["steps"][0]["rows"][0]
        self.assertEqual(close["body"], "Fixed by #1099, already merged.")
        self.assertNotIn(fixed, [card["n"] for card in issue["pending"]])

    def test_a_reading_the_issue_moved_past_is_not_used(self):
        n = self.shortlist[0]
        analyze_issue(n)
        deskstate.update(REPO, lambda state: state["issues"][str(n)].update(
            at="2000-01-01T00:00:00+00:00"))
        issue = self.desk.wizard()["issue"]
        self.assertEqual(steps(issue)["claude"], [])
        self.assertIn(n, [card["n"] for card in issue["pending"]])


class WhoseTurn(unittest.TestCase):

    def test_per_person_with_the_user_first_and_nobody_last(self):
        desk = fresh_desk()
        whose = desk.wizard()["whose"]
        self.assertTrue(whose[0]["me"])
        self.assertEqual(whose[0]["review"], 30)
        self.assertIsNone(whose[-1]["who"])
        self.assertGreater(whose[-1]["unassigned"], 0)
        middle = whose[1:-1]
        self.assertEqual([e["total"] for e in middle],
                         sorted((e["total"] for e in middle), reverse=True))
        fporcari = next(e for e in middle if e["who"] == "fporcari")
        self.assertTrue(fporcari["wait"] or fporcari["fix"] or fporcari["merge"])

    def test_my_prs_waiting_on_somebody_carry_the_chase(self):
        whose = fresh_desk("fporcari").wizard()["whose"]
        cgabriel = next(e for e in whose if e["who"] == "cgabriel")
        self.assertTrue(cgabriel["review"])
        self.assertTrue(cgabriel["chase"].startswith("@cgabriel"))


DIFF = """diff --git a/gnrjs/gnrbag.js b/gnrjs/gnrbag.js
--- a/gnrjs/gnrbag.js
+++ b/gnrjs/gnrbag.js
@@ -10,2 +10,2 @@ setItem
-a
+b
@@ -1204,9 +1204,11 @@ triggerDispatch
   var path = node.getFullpath();
-  this._subscribers.forEach(fire);
+  var hits = this._triggerIndex.lookup(path);
diff --git a/CHANGELOG.md b/CHANGELOG.md
@@ -1204,9 +1204,11 @@ triggerDispatch
+not this one
"""


class Zoom(unittest.TestCase):

    def test_the_hunk_is_the_one_the_analysis_pointed_at(self):
        hunk = wizard.cut_hunk(DIFF, "gnrjs/gnrbag.js", "@@ -1204,9 +1204,11 @@")
        self.assertEqual(hunk["header"], "@@ -1204,9 +1204,11 @@ triggerDispatch")
        self.assertEqual(hunk["lines"], ["   var path = node.getFullpath();",
                                         "-  this._subscribers.forEach(fire);",
                                         "+  var hits = this._triggerIndex.lookup(path);"])
        self.assertIsNone(wizard.cut_hunk(DIFF, "gnrjs/other.js", "@@ -1204,9 +1204,11 @@"))

    def test_the_whole_situation_of_one_pr(self):
        desk = fresh_desk()
        n = sorted(desk.review_targets())[0]
        source = next(row for row in desk.provider.data["rows"] if row["n"] == n)
        source["head"] = "h%d" % n
        desk.provider.data["diffs"] = {str(n): DIFF}
        cache.clear(REPO)
        analyze(desk, n, stance="doubt", doubt="l'ordine cambia", lean="changes",
                draft="Please add a test.", verified=["i test passano"],
                not_verified=["le pagine dei clienti"],
                hunk={"path": "gnrjs/gnrbag.js", "header": "@@ -1204,9 +1204,11 @@"},
                keys={"checks": {"head": "h%d" % n, "state": "SUCCESS"}})
        zoom = desk.zoom(n)
        self.assertEqual(zoom["card"]["stance"], "doubt")
        self.assertEqual((zoom["verified"], zoom["not_verified"]),
                         (["i test passano"], ["le pagine dei clienti"]))
        self.assertEqual(len(zoom["hunk"]["lines"]), 3)
        self.assertEqual(zoom["state"]["tests"], "SUCCESS")
        self.assertEqual(zoom["timeline"][-1], {"on": "oggi", "now": True,
                                                "text": "tocca a te: sei revisore richiesto"})
        self.assertEqual(zoom["timeline"][0]["text"], "%s apre la PR" % source["author"])
        with self.assertRaises(KeyError):
            desk.zoom(1)

    def test_a_diff_the_provider_cannot_give_is_said_not_hidden(self):
        desk = fresh_desk()
        n = sorted(desk.review_targets())[0]
        source = next(row for row in desk.provider.data["rows"] if row["n"] == n)
        source["head"] = "h%d" % n
        cache.clear(REPO)
        analyze(desk, n, stance="doubt", doubt="d", lean="approve",
                hunk={"path": "a.js", "header": "@@ -1 +1 @@"})
        self.assertIn("no diff", desk.zoom(n)["hunk"]["error"])


class Preparation(unittest.TestCase):
    NOW = datetime(2026, 10, 6, 9, 12)

    def info(self, run, running=None, kind="pr"):
        return wizard.prepare_info({"runs": {"%s-nightwork" % kind: run}}, kind,
                                   running, self.NOW)

    def test_a_run_going_says_how_far(self):
        info = self.info({"status": "running", "due": [1, 2, 3, 4],
                          "landed": [1, 2], "failed": {"3": "x"}}, True)
        self.assertEqual(info["phrase"], "preparo la review · 3 di 4 lette")

    def test_last_night_is_said_so(self):
        info = self.info({"status": "done", "prepared_by": "night",
                          "prepared_at": "2026-10-05T23:10:00"})
        self.assertEqual(info["phrase"], "preparata stanotte alle 23:10")
        info = self.info({"status": "done", "prepared_by": "night",
                          "prepared_at": "2026-10-06T02:10:00"})
        self.assertEqual(info["phrase"], "preparata stanotte alle 02:10")
        evening = wizard.prepare_info({"runs": {"pr-nightwork": {
            "status": "done", "prepared_by": "night",
            "prepared_at": "2026-10-06T20:55:00"}}}, "pr", None, datetime(2026, 10, 6, 21, 30))
        self.assertEqual(evening["phrase"], "preparata stasera alle 20:55")
        info = self.info({"status": "done", "prepared_by": "desk",
                          "prepared_at": "2026-10-06T09:09:00"})
        self.assertEqual(info["phrase"], "preparata alle 09:09")
        old = (self.NOW - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S")
        info = self.info({"status": "done", "prepared_by": "night", "prepared_at": old})
        self.assertTrue(info["phrase"].startswith("preparata il 03/10"))

    def test_a_record_running_with_nobody_on_the_lock_is_a_dead_run(self):
        info = self.info({"status": "running", "due": [1], "prepared_by": "night",
                          "prepared_at": "2026-10-05T23:10:00"}, running=False)
        self.assertEqual(info["status"], "interrupted")
        self.assertTrue(info["phrase"].startswith("preparazione interrotta"))

    def test_failures_are_counted_and_nothing_is_never(self):
        info = self.info({"status": "done", "prepared_by": "desk",
                          "prepared_at": "2026-10-06T09:00:00", "failed": {"7": "x"}},
                         kind="issue")
        self.assertEqual(info["phrase"], "preparata alle 09:00 · 1 non riuscite")
        self.assertEqual(wizard.prepare_info({}, "pr")["status"], "never")


if __name__ == "__main__":
    unittest.main()
