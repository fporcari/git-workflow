"""Nightwork: the jobs the desk would start in the morning, started the
evening before — exactly those, the PR side and the issue side apart, a few
at a time, a failure costing only its own item."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cache            # noqa: E402
import deskstate        # noqa: E402
import jobs             # noqa: E402
import nightwork        # noqa: E402
import prdesk           # noqa: E402
from providers import get_provider  # noqa: E402

REPO = "genropy/genropy"
_SAVED = {}


def setUpModule():
    home = tempfile.mkdtemp(prefix="nightwork-")
    _SAVED.update(home=os.environ.get("HOME"), state=deskstate.STATE_DIR,
                  runtime=deskstate.RUNTIME_DIR, poll=nightwork.POLL)
    os.environ["HOME"] = home
    deskstate.STATE_DIR = Path(home) / ".local" / "state" / "git-workflow"
    deskstate.RUNTIME_DIR = Path(home) / "runtime"
    nightwork.POLL = 0


def tearDownModule():
    if _SAVED.get("home") is not None:
        os.environ["HOME"] = _SAVED["home"]
    deskstate.STATE_DIR = _SAVED["state"]
    deskstate.RUNTIME_DIR = _SAVED["runtime"]
    nightwork.POLL = _SAVED["poll"]


def fresh_desk():
    cache.clear(REPO)
    deskstate.save(REPO, {})
    provider = get_provider("fixture")
    return prdesk.Desk(provider, REPO, provider.whoami(), str(ROOT))


class FakeJobs:
    """The jobs module as nightwork sees it: every job stays alive for two
    polls, so the parallel cap is observable, then ends done or, for a label
    in `fail`, in error."""

    def __init__(self, fail=()):
        self.records = {}
        self.labels = {}
        self.calls = []
        self.fail = set(fail)
        self.peak = 0
        self.exported = None
        self.inputs = None

    def _start(self, label):
        job_id = "job-%d" % len(self.records)
        self.records[job_id] = {"id": job_id, "status": "running", "polls": 2}
        self.labels[job_id] = label
        self.calls.append(label)
        self.peak = max(self.peak, sum(r["status"] == "running"
                                       for r in self.records.values()))
        return job_id

    def analyze_pr(self, repo, n, me, cwd, agent="auto", inputs=None):
        self.inputs = inputs
        return self._start(("pr", n))

    def analyze_issue(self, repo, n, me, cwd, agent="auto"):
        return self._start(("issue", n))

    def triage(self, repo, flow, export, me, cwd, agent="auto"):
        self.exported = json.loads(Path(export()).read_text())
        return self._start(("triage", flow))

    def active(self, repo):
        out = []
        for job_id, record in self.records.items():
            if record["status"] != "running":
                continue
            record["polls"] -= 1
            if record["polls"] > 0:
                out.append(record)
            elif self.labels[job_id] in self.fail:
                record.update(status="error", error="boom")
            else:
                record["status"] = "done"
        return out

    def get(self, repo, job_id):
        return self.records.get(job_id)

    def patch(self):
        return mock.patch.multiple(
            nightwork.jobs, analyze_pr=self.analyze_pr,
            analyze_issue=self.analyze_issue, triage=self.triage,
            active=self.active, get=self.get)


def owed(desk):
    return json.loads(Path(desk.run_triage("pr-triage")).read_text())["model_tasks"]


class PrNight(unittest.TestCase):

    def test_it_analyzes_exactly_what_the_desk_counts_as_owed(self):
        desk = fresh_desk()
        tasks = owed(desk)
        expected = {int(n) for n, kinds in tasks.items() if "analysis" in kinds}
        self.assertTrue(expected, "the fixture needs PRs owing an analysis")
        fake = FakeJobs()
        with fake.patch():
            nightwork.pr_night(desk, 4)
        self.assertEqual({n for kind, n in fake.calls if kind == "pr"}, expected)
        self.assertFalse([c for c in fake.calls if c[0] == "issue"])
        self.assertNotIn(("triage", "issue-triage"), fake.calls)

    def test_the_analysis_job_reads_the_same_inputs_as_the_button(self):
        desk = fresh_desk()
        fake = FakeJobs()
        with fake.patch():
            nightwork.pr_night(desk, 4)
        last = [n for kind, n in fake.calls if kind == "pr"][-1]
        keys, context = fake.inputs()
        self.assertEqual(context["row"]["n"], last)
        self.assertIn("problem_head", keys)

    def test_the_conflict_pass_owes_only_conflict_readings(self):
        desk = fresh_desk()
        conflicts = {n for n, kinds in owed(desk).items() if "conflict" in kinds}
        self.assertTrue(conflicts, "the fixture needs a DIRTY PR of the user's")
        fake = FakeJobs()
        with fake.patch():
            nightwork.pr_night(desk, 4)
        self.assertIn(("triage", "pr-triage"), fake.calls)
        self.assertEqual(set(fake.exported["model_tasks"]), conflicts)
        self.assertTrue(all(kinds == ["conflict"]
                            for kinds in fake.exported["model_tasks"].values()))
        self.assertEqual(sorted(fake.exported["needs_model"]),
                         sorted(int(n) for n in conflicts))

    def test_a_current_analysis_is_not_bought_twice(self):
        desk = fresh_desk()
        tasks = owed(desk)
        n = next(int(n) for n, kinds in tasks.items() if "analysis" in kinds)
        rows = desk._queue_facts(complete_gates=True)[0]
        key = next(r for r in rows if r["n"] == n)["model_keys"]["analysis"]
        deskstate.save(REPO, {"prs": {str(n): {"analysis": "done", "analysis_key": key}}})
        fake = FakeJobs()
        with fake.patch():
            nightwork.pr_night(desk, 4)
        self.assertNotIn(("pr", n), fake.calls)

    def test_no_more_than_parallel_jobs_are_alive(self):
        desk = fresh_desk()
        fake = FakeJobs()
        with fake.patch():
            nightwork.pr_night(desk, 2)
        self.assertGreater(len(fake.calls), 2)
        self.assertEqual(fake.peak, 2)

    def test_a_failure_costs_only_its_own_item(self):
        desk = fresh_desk()
        tasks = owed(desk)
        due = [int(n) for n, kinds in tasks.items() if "analysis" in kinds]
        fake = FakeJobs(fail={("pr", due[0])})
        with fake.patch():
            done = nightwork.pr_night(desk, 4)
        self.assertEqual({n for kind, n in fake.calls if kind == "pr"}, set(due))
        status, report = nightwork.summary("pr", done)
        self.assertEqual(status, "done")
        self.assertIn("%d PR analizzate" % (len(due) - 1), report)
        self.assertIn("#%s (boom)" % due[0], report)


class IssueNight(unittest.TestCase):

    def test_it_ranks_first_then_analyzes_the_shortlist_never_a_pr(self):
        desk = fresh_desk()
        shortlist = [r["n"] for r in desk.issues()["shortlist"]["rows"]]
        self.assertTrue(shortlist, "the fixture needs a shortlist")
        fake = FakeJobs()
        with fake.patch():
            nightwork.issue_night(desk, 4)
        self.assertEqual(fake.calls[0], ("triage", "issue-triage"))
        self.assertEqual([n for kind, n in fake.calls if kind == "issue"], shortlist)
        self.assertFalse([c for c in fake.calls if c[0] == "pr"])
        self.assertNotIn(("triage", "pr-triage"), fake.calls)

    def test_a_reusable_issue_analysis_is_skipped(self):
        desk = fresh_desk()
        first = desk.issues()["shortlist"]["rows"][0]["n"]
        deskstate.save(REPO, {"issues": {str(first): {
            "type": "DEFECT", "finding": "f", "size": "EASY",
            "phase": "SINGLE-PHASE", "problem": "p", "cause": "c",
            "propose": "x", "verify": "v", "decision": None,
            "at": "2999-01-01T00:00:00+00:00"}}})
        fake = FakeJobs()
        with fake.patch():
            nightwork.issue_night(desk, 4)
        self.assertNotIn(("issue", first), fake.calls)

    def test_a_failed_ranking_still_analyzes(self):
        desk = fresh_desk()
        fake = FakeJobs(fail={("triage", "issue-triage")})
        with fake.patch():
            done = nightwork.issue_night(desk, 4)
        self.assertTrue([c for c in fake.calls if c[0] == "issue"])
        status, report = nightwork.summary("issue", done)
        self.assertEqual(status, "done")
        self.assertIn("classifica (boom)", report)


class WithTheFakeAgent(unittest.TestCase):
    """The real jobs, the real persistence, a `claude` that answers at once:
    what the night prepares is the verdict the wizard is built on."""

    def setUp(self):
        self.bin = Path(tempfile.mkdtemp(prefix="fake-claude-"))
        agent = self.bin / "claude"
        shutil.copy(ROOT / "tests" / "fixtures" / "fake_claude.py", agent)
        agent.chmod(0o755)
        self.log = self.bin / "calls.log"
        self.env = mock.patch.dict(os.environ, {
            "PATH": "%s%s%s" % (self.bin, os.pathsep, os.environ.get("PATH", "")),
            "FAKE_LOG": str(self.log)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        jobs.shutdown()
        shutil.rmtree(self.bin, ignore_errors=True)

    def calls(self):
        return self.log.read_text().split("\n") if self.log.exists() else []

    def test_the_verdict_is_persisted_and_a_second_run_reuses_it(self):
        desk = fresh_desk()
        desk.agent = "claude"
        due = sorted(int(n) for n, kinds in owed(desk).items() if "analysis" in kinds)
        nightwork.pr_night(desk, 4)
        notes = deskstate.load(REPO)["prs"]
        for n in due:
            note = notes[str(n)]
            self.assertEqual(note["stance"], ("approve", "changes", "doubt")[n % 3])
            self.assertEqual(note["why"], "perché %d" % n)
            if note["stance"] == "approve":
                self.assertNotIn("draft", note)
            else:
                self.assertTrue(note["draft"].startswith("Please"))
            if note["stance"] == "doubt":
                self.assertEqual(note["lean"], "changes")
                self.assertEqual(note["hunk"]["path"], "gnrjs/gnrbag.js")
            else:
                self.assertNotIn("hunk", note)
        served = {row["n"]: row for row in desk.queue()["rows"]}
        self.assertEqual(served[due[0]]["advice"]["stance"], notes[str(due[0])]["stance"])
        analyzed = [line for line in self.calls() if line.startswith("pr ")]
        self.assertEqual(sorted(int(line.split()[1]) for line in analyzed), due)

        nightwork.pr_night(desk, 4)
        again = [line for line in self.calls() if line.startswith("pr ")]
        self.assertEqual(again, analyzed, "a current verdict is never bought twice")

    def test_a_failed_analysis_costs_only_its_pr(self):
        desk = fresh_desk()
        desk.agent = "claude"
        due = sorted(int(n) for n, kinds in owed(desk).items() if "analysis" in kinds)
        with mock.patch.dict(os.environ, {"FAKE_FAIL": str(due[0])}):
            done = nightwork.pr_night(desk, 4)
        self.assertEqual(done[due[0]]["status"], "error")
        notes = deskstate.load(REPO)["prs"]
        self.assertNotIn("stance", notes.get(str(due[0])) or {})
        self.assertTrue(all("stance" in notes[str(n)] for n in due[1:]))


class Outcome(unittest.TestCase):

    def test_nothing_to_do_is_not_a_failure(self):
        self.assertEqual(nightwork.summary("pr", {}),
                         ("done", "nessuna PR da analizzare"))

    def test_work_that_all_failed_is_a_failure(self):
        status, report = nightwork.summary(
            "issue", {7: {"status": "error", "error": "timeout"}})
        self.assertEqual(status, "failed")
        self.assertIn("#7 (timeout)", report)

    def test_the_run_is_recorded_where_the_desk_reads_it(self):
        desk = fresh_desk()
        fake = FakeJobs()
        with fake.patch():
            status, report = nightwork.night(desk, "pr", 4)
        state = deskstate.load(REPO)
        run = state["runs"]["pr-nightwork"]
        self.assertEqual((run["status"], run["report"]), (status, report))
        self.assertNotIn("issue-nightwork", state["runs"])
        self.assertTrue(any(line["msg"] == "pr-nightwork: %s" % report
                            for line in state["feed"]))

    def test_a_second_run_of_the_same_kind_refuses_to_start(self):
        desk = fresh_desk()
        fake = FakeJobs()
        with nightwork.exclusive(REPO, "pr-nightwork") as free, fake.patch():
            self.assertTrue(free)
            status, report = nightwork.night(desk, "pr", 4)
            self.assertEqual(fake.calls, [])
            other = nightwork.night(desk, "issue", 4)
        self.assertEqual(status, "failed")
        self.assertIn("già in corso", report)
        self.assertEqual(other[0], "done", "the other kind is not held")

    def test_a_run_that_breaks_is_recorded_as_failed(self):
        desk = fresh_desk()
        with mock.patch.object(nightwork, "pr_night", side_effect=RuntimeError("gh down")):
            status, report = nightwork.night(desk, "pr", 4)
        self.assertEqual(status, "failed")
        self.assertIn("gh down", deskstate.load(REPO)["runs"]["pr-nightwork"]["report"])


if __name__ == "__main__":
    unittest.main()
