"""The stances: where every row stands, read off the provider facts, the
engine's verdict and the analysis' stance — never off a model's copy."""

import json
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
import stances          # noqa: E402
from providers import get_provider  # noqa: E402

REPO = "genropy/genropy"
ME = "genro"
_SAVED = {}


def setUpModule():
    home = tempfile.mkdtemp(prefix="stances-")
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


def owed(desk):
    return json.loads(Path(desk.run_triage("pr-triage")).read_text())["model_tasks"]


def steps(section):
    return {step["id"]: [card["n"] for card in step["rows"]]
            for step in section["steps"]}


class DaRivedere(unittest.TestCase):

    def setUp(self):
        self.desk = fresh_desk()
        self.review = self.desk.stances()["review"]
        self.ns = sorted(steps(self.review)["review"])

    def test_only_others_prs_whose_review_is_asked_and_all_to_review_at_first(self):
        self.assertEqual(self.review["count"], 30)
        self.assertEqual(self.review["first"], "review")
        self.assertEqual(len(self.ns), 30)
        self.assertTrue(all(step["rows"] == [] for step in self.review["steps"]
                            if step["id"] != "review"))
        authors = {card["author"] for card in self.review["steps"][3]["rows"]}
        self.assertNotIn(ME, authors)

    def test_each_stance_lands_in_its_step_with_what_it_needs(self):
        a, c, d = self.ns[:3]
        analyze(self.desk, a)
        analyze(self.desk, c, stance="changes", draft="Please split it.")
        analyze(self.desk, d, stance="doubt", doubt="l'ordine cambia", lean="changes",
                draft="Please add a test.", hunk={"path": "a.js", "header": "@@ -1 +1 @@"})
        review = self.desk.stances()["review"]
        self.assertEqual(steps(review)["approve"], [a])
        self.assertEqual(steps(review)["changes"], [c])
        self.assertEqual(steps(review)["doubt"], [d])
        self.assertEqual(review["first"], "approve")
        self.assertEqual(len(steps(review)["review"]), 27)
        rows = {card["n"]: card for step in review["steps"] for card in step["rows"]}
        self.assertEqual(rows[a]["why"], "w%d" % a)
        self.assertEqual(rows[c]["draft"], "Please split it.")
        self.assertEqual(rows[d]["hunk"]["path"], "a.js")
        self.assertTrue(rows[a]["url"].endswith("/%d" % a))

    def test_what_is_to_do_names_who_asks_from_the_first_read(self):
        self.assertEqual(sorted((row["n"], row["step"]) for row in self.desk.todo()["rows"]),
                         [(n, "review") for n in self.ns])
        a, c = self.ns[:2]
        analyze(self.desk, a)
        analyze(self.desk, c, stance="changes", draft="Please split it.")
        rows = self.desk.todo()["rows"]
        self.assertEqual(sorted((row["n"], row["step"]) for row in rows if row["step"] != "review"),
                         sorted([(a, "approve"), (c, "changes")]))
        self.assertEqual(len(rows), 30)
        authors = {row["n"]: row["author"] for row in self.desk.queue()["rows"]}
        self.assertTrue(all(row["author"] == authors[row["n"]] for row in rows))
        self.assertTrue(all(row["repo"] == REPO for row in rows))

    def test_a_review_already_sent_is_no_longer_to_do(self):
        n = self.ns[0]
        analyze(self.desk, n)
        deskstate.update(REPO, lambda state: state.setdefault(
            "session_reviews", {}).setdefault("approve", []).append(n))
        self.assertNotIn(n, [row["n"] for row in self.desk.todo()["rows"]])
        self.assertEqual(len(self.desk.todo()["rows"]), 29)

    def test_a_failed_analysis_waits_among_the_doubts_with_the_reason(self):
        n = self.ns[0]
        deskstate.save(REPO, {"runs": {"pr-nightwork": {
            "status": "done", "failed": {str(n): "claude exited 3"}}}})
        review = self.desk.stances()["review"]
        doubt = next(card for card in review["steps"][2]["rows"] if card["n"] == n)
        self.assertIn("claude exited 3", doubt["doubt"])
        self.assertNotIn(n, steps(review)["review"])

    def test_a_giant_pr_is_read_by_folder_and_never_leaves_the_doubts(self):
        n, m = self.ns[0], self.ns[1]
        rows = {r["n"]: r for r in self.desk.provider._d(REPO)["rows"]}
        files = [{"path": "gnrpy/gnr/web/next.py", "additions": 1200, "deletions": 0},
                 {"path": "gnrpy/gnr/sql/macro.py", "additions": 900, "deletions": 0},
                 {"path": "gnrpy/tests/web/test_next.py", "additions": 800, "deletions": 0}]
        for k in (n, m):
            rows[k].update(size=2900, files=files)
        try:
            cache.clear(REPO)
            self.assertIn(str(n), owed(self.desk))
            giant = self.desk.analysis_inputs(n)[1]["giant"]
            self.assertEqual(giant["code_lines"], 2100)
            self.assertEqual([g["folder"] for g in giant["read"]], ["gnrpy/gnr/sql", "gnrpy/gnr/web"])
            self.assertEqual(giant["not_code"], {"files": 1, "lines": 800})
            self.assertNotIn("giant", self.desk.analysis_inputs(self.ns[2])[1])
            analyze(self.desk, n)
            analyze(self.desk, m, stance="changes", draft="Please split it.")
            review = self.desk.stances()["review"]
            self.assertEqual(steps(review)["approve"], [])
            self.assertEqual(steps(review)["changes"], [])
            doubts = {card["n"]: card for card in review["steps"][2]["rows"]}
            self.assertEqual((doubts[n]["lean"], doubts[m]["lean"]), ("approve", "changes"))
            self.assertIn("PR molto grande (2100 righe di codice)", doubts[n]["doubt"])
            self.assertEqual(doubts[m]["draft"], "Please split it.")
        finally:
            for k in (n, m):
                del rows[k]["size"], rows[k]["files"]
            cache.clear(REPO)

    def test_a_pr_big_only_in_tests_and_bundles_is_analyzed_like_any_other(self):
        n = self.ns[0]
        row = next(r for r in self.desk.provider._d(REPO)["rows"] if r["n"] == n)
        row["files"] = [{"path": "gnrjs/gnr_d11/js/genro_bagjs_bundle.js", "additions": 11000, "deletions": 0},
                        {"path": "gnrpy/tests/web/test_next.py", "additions": 900, "deletions": 0},
                        {"path": "gnrpy/gnr/web/next.py", "additions": 300, "deletions": 20}]
        row["size"] = 12220
        try:
            cache.clear(REPO)
            rows = self.desk._queue_facts(complete_gates=True)[0]
            self.assertEqual(next(r for r in rows if r["n"] == n)["code_size"], 320)
            self.assertIn(str(n), owed(self.desk))
            self.assertIn(n, steps(self.desk.stances()["review"])["review"])
        finally:
            del row["size"], row["files"]
            cache.clear(REPO)

    def test_needs_verification_is_never_approvable_on_tests_not_green(self):
        n = self.ns[0]
        source = next(row for row in self.desk.provider.data["rows"] if row["n"] == n)
        source.update(labels=["needs-verification"], head="h%d" % n)
        cache.clear(REPO)
        analyze(self.desk, n, keys={"checks": {"head": "h%d" % n, "state": "FAILURE"}})
        review = self.desk.stances()["review"]
        self.assertEqual(steps(review)["approve"], [])
        held = review["steps"][2]["rows"][0]
        self.assertEqual((held["n"], held["lean"]), (n, "approve"))
        self.assertIn("needs-verification", held["doubt"])
        analyze(self.desk, n, keys={"checks": {"head": "h%d" % n, "state": "SUCCESS"}})
        self.assertEqual(steps(self.desk.stances()["review"])["approve"], [n])

    def test_a_doubt_skipped_to_tomorrow_leaves_the_steps_for_today(self):
        n = self.ns[0]
        analyze(self.desk, n, stance="doubt", doubt="d", lean="approve")
        deskstate.update(REPO, lambda state: state["prs"][str(n)].update(
            skipped=date.today().isoformat()))
        review = self.desk.stances()["review"]
        self.assertEqual(steps(review)["doubt"], [])
        self.assertEqual([card["n"] for card in review["skipped"]], [n])

    def test_a_row_to_review_shows_what_its_job_is_doing(self):
        n = self.ns[0]
        job = {"kind": "analyze", "request": {"n": n},
               "progress": {"stage": "testing"}}
        queue = self.desk.queue()
        section = stances.review_section(queue, deskstate.load(REPO), ME, [job])
        cards = {card["n"]: card for card in section["steps"][3]["rows"]}
        self.assertEqual(cards[n]["chip"], "verifica i test…")
        self.assertNotIn("chip", cards[self.ns[1]])


def analyze_issue(n, **fields):
    record = {"n": n, "type": "DEFECT", "finding": "f%d" % n, "size": "EASY",
              "phase": "SINGLE-PHASE", "problem": "p", "cause": "c", "propose": "x",
              "verify": "v", "decision": None, "fixed_by": None}
    record.update(fields)
    jobs.persist_issue_analysis(REPO, record, n)


class Issues(unittest.TestCase):

    def setUp(self):
        self.desk = fresh_desk("fporcari")
        self.shortlist = [card["n"] for card in self.desk.stances()["issue"]["pending"]]

    def test_only_the_shortlist_waits_for_a_reading(self):
        issue = self.desk.stances()["issue"]
        self.assertEqual(len(self.shortlist), 10)
        self.assertEqual(issue["first"], "done")
        self.assertEqual(issue["count"], len(self.desk.issues()["rows"]))

    def test_each_reading_lands_in_its_step(self):
        fixed, easy, long, taken = self.shortlist[:4]
        analyze_issue(fixed, fixed_by=1099)
        analyze_issue(easy)
        analyze_issue(long, size="MEDIUM", phase="WORKFLOW")
        analyze_issue(taken, decision="who owns the print templates?")
        issue = self.desk.stances()["issue"]
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
        issue = self.desk.stances()["issue"]
        self.assertEqual(steps(issue)["claude"], [])
        self.assertIn(n, [card["n"] for card in issue["pending"]])


class Preparation(unittest.TestCase):
    NOW = datetime(2026, 10, 6, 9, 12)

    def info(self, run, running=None, kind="pr"):
        return stances.prepare_info({"runs": {"%s-nightwork" % kind: run}}, kind,
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
        evening = stances.prepare_info({"runs": {"pr-nightwork": {
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
        self.assertEqual(stances.prepare_info({}, "pr")["status"], "never")

    def test_a_failed_run_says_why_instead_of_nothing_to_do(self):
        info = self.info({"status": "failed", "report": "interrotto: gh api failed"})
        self.assertEqual(info["phrase"], "preparazione non riuscita · interrotto: gh api failed")


if __name__ == "__main__":
    unittest.main()
