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
