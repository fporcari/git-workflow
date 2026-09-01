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
import issuecheck       # noqa: E402
import jobs             # noqa: E402
import lane_check       # noqa: E402
import prdesk           # noqa: E402
import safejson         # noqa: E402
import verdicts         # noqa: E402
from providers import get_provider  # noqa: E402
from providers import github as github_provider  # noqa: E402

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
               "base_head", "head", "incomplete", "merge",
               "decision", "req", "reviews", "unresolved", "threads", "closes",
               "last", "url", "todo", "state", "autorun", "action",
               "triage_key", "triage_status", "model_keys", "analysis_stale",
               "what_stale", "conflict_stale"}
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


class QueueMembership(unittest.TestCase):
    def test_github_drops_a_merged_node_from_a_stale_open_search(self):
        search = {"search": {"issueCount": 2,
                             "pageInfo": {"hasNextPage": False},
                             "nodes": [{"number": 1, "state": "OPEN"},
                                       {"number": 2, "state": "MERGED"}]}}
        provider = github_provider.GitHubProvider()
        with mock.patch.object(github_provider, "_graphql", return_value=search), \
                mock.patch.object(provider, "_row",
                                  side_effect=lambda repo, node: {
                                      "n": node["number"], "created": "2026-01-01"}):
            queue = provider.queue(REPO, "me")
            membership = provider.open_numbers(REPO, "me")
        self.assertEqual([row["n"] for row in queue["rows"]], [1])
        self.assertEqual(queue["total"], 1)
        self.assertEqual(membership, [1])

    def test_fresh_membership_filters_a_stale_detailed_queue(self):
        desk = fresh_desk()
        first = desk.queue()
        n = first["rows"][0]["n"]
        source = next(row for row in desk.provider.data["rows"] if row["n"] == n)
        source["state"] = "MERGED"
        desk._membership(True)
        queue = desk.queue()
        detailed = cache.peek(REPO, "queue")[1]
        self.assertTrue(any(row["n"] == n for row in detailed["rows"]))
        self.assertFalse(any(row["n"] == n for row in queue["rows"]))

    def test_github_analysis_probe_omits_comment_bodies_and_normalizes_checks(self):
        payload = {"repository": {"pullRequest": {
            "headRefOid": "head", "baseRefOid": "base",
            "mergeStateStatus": "BLOCKED", "reviewDecision": "REVIEW_REQUIRED",
            "reviewRequests": {"pageInfo": {"hasNextPage": False},
                               "nodes": [{"requestedReviewer": {"login": "bob"}}]},
            "reviews": {"pageInfo": {"hasPreviousPage": False}, "nodes": [{
                "author": {"login": "bob"}, "state": "DISMISSED",
                "submittedAt": "2026-08-31T10:00:00Z",
                "commit": {"oid": "reviewed"}}]},
            "reviewThreads": {"totalCount": 2,
                              "pageInfo": {"hasNextPage": False},
                              "nodes": [{"isResolved": True},
                                        {"isResolved": False}]},
            "commits": {"nodes": [{"commit": {"statusCheckRollup": {
                "state": "SUCCESS", "contexts": {
                    "pageInfo": {"hasNextPage": False}, "nodes": [{
                        "name": "tests", "status": "COMPLETED",
                        "conclusion": "SUCCESS"}]}}}}]},
        }}}
        provider = github_provider.GitHubProvider()
        with mock.patch.object(github_provider, "_graphql", return_value=payload):
            probe = provider.analysis_probe(REPO, 17)
        self.assertEqual(probe["head"], "head")
        self.assertEqual(probe["reviews"][0]["commit"], "reviewed")
        self.assertEqual(probe["checks"]["state"], "SUCCESS")
        self.assertEqual(probe["unresolved"], 1)
        self.assertNotIn("body", json.dumps(probe))

    def test_a_new_membership_forces_the_detailed_queue_forward(self):
        desk = fresh_desk()
        first = desk.queue()
        source = dict(desk.provider.data["rows"][0], n=9999, state="OPEN")
        desk.provider.data["rows"].insert(0, source)
        desk._membership(True)
        queue = desk.queue()
        self.assertIn(9999, [row["n"] for row in queue["rows"]])
        self.assertEqual(len(queue["rows"]), len(first["rows"]) + 1)


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

    def test_concurrent_forced_refreshes_collapse_into_one_load(self):
        cache.clear(REPO)
        cache.store(REPO, "forced", {"v": 0})
        calls = []

        def loader():
            calls.append(1)
            time.sleep(0.3)
            return {"v": len(calls)}

        threads = [threading.Thread(
            target=lambda: cache.get(REPO, "forced", loader, refresh=True))
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
        self.assertIn("--json", command)
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertIn("--output-schema", command)

    def test_claude_command_has_no_write_tool(self):
        command = jobs.command("claude", "prompt", jobs.READ_TOOLS, "/tmp/repo")
        self.assertIn("--json-schema", command)
        document = json.loads(command[command.index("--json-schema") + 1])
        self.assertNotIn("$schema", document)
        self.assertIn("properties", document)
        self.assertEqual(command[command.index("--output-format") + 1],
                         "stream-json")
        self.assertIn("--verbose", command)
        self.assertNotIn("Write", command[command.index("--allowedTools") + 1])
        self.assertNotIn("gh pr view", jobs.READ_TOOLS)
        self.assertNotIn("gh pr checks", jobs.READ_TOOLS)
        for tool in ("Bash(git cat-file:*)", "Bash(git show:*)",
                     "Bash(git diff:*)"):
            self.assertIn(tool, jobs.READ_TOOLS)
        for tool in ("mcp__sourcerer__kb_ask",
                     "mcp__sourcerer__code_search_code"):
            self.assertIn(tool, jobs.READ_TOOLS)
        for tool in ("kb_add_skill", "kb_update_skill", "kb_add_topic"):
            self.assertNotIn(tool, jobs.READ_TOOLS)
        self.assertNotIn("git fetch", jobs.READ_TOOLS)
        self.assertNotIn("Bash(gh api:*)", jobs.READ_TOOLS)

    def test_analysis_profiles_map_to_each_host(self):
        env = {"GIT_WORKFLOW_ANALYZE_MODEL": "fast-model",
               "GIT_WORKFLOW_ANALYZE_EFFORT": "medium"}
        with mock.patch.dict(os.environ, env, clear=False):
            claude = jobs.command("claude", "p", jobs.READ_TOOLS, "/tmp",
                                  profile="ANALYZE")
            codex = jobs.command("codex", "p", jobs.READ_TOOLS, "/tmp",
                                 profile="ANALYZE")
        self.assertEqual(claude[claude.index("--model") + 1], "fast-model")
        self.assertEqual(claude[claude.index("--effort") + 1], "medium")
        self.assertEqual(codex[codex.index("--model") + 1], "fast-model")
        self.assertIn('model_reasoning_effort="medium"', codex)

    def test_claude_stream_result_uses_the_structured_final_event(self):
        stream = "\n".join((
            "non-json diagnostic ignored",
            json.dumps({"type": "system", "subtype": "init"}),
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "thinking", "thinking": "private"}]}}),
            json.dumps({"type": "result", "structured_output": self.RESULT})))
        self.assertEqual(jobs.parse_structured("claude", stream), self.RESULT)

    def test_progress_exposes_tools_but_never_thinking_or_raw_output(self):
        thinking = json.dumps({"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "private reasoning"}]}})
        tool = json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "pytest tests/test_api.py"}}]}})
        self.assertIsNone(jobs.progress_event("claude", thinking))
        progress = jobs.progress_event("claude", tool)
        self.assertEqual(progress["stage"], "testing")
        self.assertIn("pytest", progress["detail"])
        self.assertNotIn("private reasoning", progress["detail"])

    def test_codex_jsonl_activity_is_normalized(self):
        progress = jobs.progress_event("codex", json.dumps({
            "type": "item.started", "item": {
                "type": "command_execution", "command": "gh pr view 17"}}))
        self.assertEqual(progress["stage"], "inspecting")
        self.assertIn("gh pr view 17", progress["detail"])

    def test_stream_runner_reports_activity_before_completion(self):
        lines = [json.dumps({"type": "thread.started"}),
                 json.dumps({"type": "turn.completed"})]
        script = ("import time; print(%r, flush=True); time.sleep(.05); "
                  "print(%r, flush=True)" % tuple(lines))
        seen = []
        out = jobs._execute(
            [sys.executable, "-c", script], str(ROOT), 5, "codex",
            lambda event, elapsed: seen.append(event) if event else None)
        self.assertEqual(out.returncode, 0)
        self.assertEqual([event["stage"] for event in seen],
                         ["starting", "finalizing"])

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
        deskstate.save(repo, {"prs": {"17": {"what": "già presente",
                                                "draft": "vecchia bozza"}}})
        jobs.persist(repo, self.RESULT, "analysis-v1")
        record = deskstate.load(repo)["prs"]["17"]
        self.assertEqual(record["next"], "proposta")
        self.assertEqual(record["author"], "alice")
        self.assertEqual(record["problem"], "problema")
        self.assertEqual(record["history"], "storia")
        self.assertIn("storia", record["analysis"])
        self.assertEqual(record["analysis_key"], "analysis-v1")
        self.assertEqual(record["what"], "già presente")
        self.assertNotIn("draft", record)

    def test_analysis_keeps_independent_validity_keys(self):
        repo = REPO + "-split-analysis"
        deskstate.reset(repo)
        keys = {"analysis": "all", "problem": "semantic",
                "history": "procedural", "problem_head": "abc123"}
        jobs.persist(repo, self.RESULT, keys)
        record = deskstate.load(repo)["prs"]["17"]
        self.assertEqual(record["analysis_key"], "all")
        self.assertEqual(record["problem_key"], "semantic")
        self.assertEqual(record["history_key"], "procedural")
        self.assertEqual(record["problem_head"], "abc123")

    def test_desk_hands_reusable_evidence_to_the_agent(self):
        desk = fresh_desk()
        row = next(row for row in desk.queue()["rows"] if row["n"] == 1145)
        deskstate.save(REPO, {"prs": {"1145": {
            "problem": "verified problem",
            "problem_key": row["model_keys"]["problem"],
            "problem_head": row["head"]}}})
        handler = object.__new__(prdesk.Handler)
        handler.desk = desk
        keys, context = handler._analysis_inputs(1145)
        self.assertEqual(context["cached_problem"], "verified problem")
        self.assertEqual(context["previous_problem_head"], row["head"])
        self.assertTrue(context["probe"]["fresh"])
        self.assertEqual(keys["problem_head"], row["head"])

    def test_a_new_probe_head_refreshes_the_persisted_keys(self):
        desk = fresh_desk()
        desk.queue()
        source = next(row for row in desk.provider.data["rows"]
                      if row["n"] == 1145)
        source["head"] = "head-seen-by-probe"
        handler = object.__new__(prdesk.Handler)
        handler.desk = desk
        keys, context = handler._analysis_inputs(1145)
        self.assertEqual(context["probe"]["head"], "head-seen-by-probe")
        self.assertEqual(keys["problem_head"], "head-seen-by-probe")

    def test_the_provider_read_happens_inside_the_analyze_job(self):
        """The click is answered at once; the probe and the gate fill are the
        job's first phase, not seconds held open in the HTTP handler."""
        calls = []

        def inputs():
            calls.append(True)
            return {"analysis": "k"}, {"probe": {"fresh": True}}

        with mock.patch.object(jobs, "_spawn", return_value="job") as spawn:
            jobs.analyze_pr(REPO, 17, "me", str(ROOT), inputs=inputs)
        self.assertEqual(calls, [], "the handler must not read the provider")
        prompt, finished = spawn.call_args.kwargs["prepare"]()
        self.assertEqual(len(calls), 1)
        self.assertIsNone(finished)
        self.assertIn('"probe"', prompt)

    def test_a_triage_with_nothing_due_ends_without_starting_an_agent(self):
        repo = REPO + "-triage-nowork"
        path = deskstate.runtime_path(repo, "empty-rows.json")
        path.write_text(json.dumps({"model_tasks": {}, "shortlist": []}))
        self.addCleanup(safejson.remove, path)
        with mock.patch.object(jobs, "resolve_agent", return_value="codex"), \
                mock.patch.object(jobs, "command") as command:
            job_id = jobs.triage(repo, "pr-triage", lambda: path, "me",
                                 str(ROOT))
            record = self._settled(repo, job_id)
        self.addCleanup(safejson.remove, jobs.job_path(repo, job_id))
        command.assert_not_called()
        self.assertEqual(record["status"], "done")
        self.assertIn("nessun lavoro del modello", record["result"]["report"])
        self.assertEqual(record["events"][0]["detail"], "reading the provider")

    def _settled(self, repo, job_id, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            record = jobs.get(repo, job_id) or {}
            if record.get("status") != "running":
                return record
            time.sleep(0.05)
        raise AssertionError("job %s never settled" % job_id)

    def test_job_status_is_a_runtime_json_file(self):
        repo = REPO + "-job-file"
        job_id = "abc123"
        safejson.write(
            jobs.job_path(repo, job_id),
            {"id": job_id, "kind": "analyze", "status": "running"}, indent=1)
        self.assertEqual(jobs.get(repo, job_id)["status"], "running")
        self.assertTrue(str(jobs.job_path(repo, job_id)).startswith(
            str(deskstate.RUNTIME_DIR)))

    def test_active_jobs_can_be_restored_after_a_browser_reload(self):
        repo = REPO + "-active-job"
        job_id = "live123"
        safejson.write(jobs.job_path(repo, job_id), {
            "id": job_id, "kind": "operation", "status": "running",
            "progress": {"stage": "testing", "elapsed": 12}}, indent=1)
        with jobs._lock:
            jobs._running[(repo, "pr-loop")] = job_id
        try:
            self.assertEqual(jobs.active(repo)[0]["progress"]["stage"],
                             "testing")
        finally:
            with jobs._lock:
                jobs._running.pop((repo, "pr-loop"), None)
            safejson.remove(jobs.job_path(repo, job_id))

    def test_a_dead_running_job_is_reconciled_as_orphaned(self):
        """A restart must not leave a dead PID looking live forever."""
        repo = REPO + "-dead-job"
        job_id = "dead123"
        path = jobs.job_path(repo, job_id)
        safejson.write(path, {
            "id": job_id, "kind": "analyze", "key": "pr:17:analyze",
            "status": "running", "agent": "codex", "pid": 999999,
            "pid_started": "old", "progress": {}, "events": []}, indent=1)
        self.addCleanup(safejson.remove, path)
        with mock.patch.object(jobs, "_process_identity", return_value=None):
            jobs.reconcile(repo)
        record = jobs.get(repo, job_id)
        self.assertEqual(record["status"], "orphaned")
        self.assertEqual(record["events"][-1]["stage"], "orphaned")
        self.assertIn("jobs.reconcile(repo)",
                      (ROOT / "prdesk.py").read_text())

    def test_a_verified_running_job_restores_its_duplicate_lock(self):
        """A live matching agent must survive reconciliation and block a twin."""
        repo = REPO + "-live-job"
        job_id, key = "live456", "pr-loop"
        path = jobs.job_path(repo, job_id)
        safejson.write(path, {
            "id": job_id, "kind": "operation", "key": key,
            "status": "running", "agent": "codex", "pid": 123,
            "pid_started": "same", "progress": {}, "events": []}, indent=1)

        def cleanup():
            with jobs._lock:
                jobs._running.pop((repo, key), None)
                jobs._reconciled.discard((repo, job_id))
            safejson.remove(path)

        self.addCleanup(cleanup)
        identity = {"started": "same", "command": "/usr/bin/codex exec"}
        with mock.patch.object(jobs, "_process_identity", return_value=identity):
            jobs.reconcile(repo)
            with mock.patch.object(jobs.threading, "Thread") as thread:
                duplicate = jobs._spawn(
                    "operation", key, "codex", repo, {}, "prompt", "", 1,
                    str(ROOT), None, None, None, read_only=False)
            active = jobs.active(repo)
        self.assertEqual(duplicate, job_id)
        self.assertEqual(active[0]["id"], job_id)
        thread.assert_not_called()

    def test_reconcile_prunes_only_expired_finished_jobs(self):
        """Retention removes old terminal files and preserves recent ones."""
        repo = REPO + "-job-retention"
        old = jobs.job_path(repo, "old")
        young = jobs.job_path(repo, "young")
        safejson.write(old, {"id": "old", "status": "done"}, indent=1)
        safejson.write(young, {"id": "young", "status": "error"}, indent=1)
        os.utime(old, (time.time() - 11, time.time() - 11))
        self.addCleanup(safejson.remove, old)
        self.addCleanup(safejson.remove, young)
        jobs.reconcile(repo, retention=10)
        self.assertFalse(old.exists())
        self.assertTrue(young.exists())

    def test_shutdown_terminates_a_child_and_records_aborted(self):
        """Stopping the desk must terminate its child and record the abort."""
        repo = REPO + "-shutdown-job"
        job_id, key = "stop123", "pr-loop"
        path = jobs.job_path(repo, job_id)
        safejson.write(path, {
            "id": job_id, "kind": "operation", "key": key,
            "status": "running", "progress": {}, "events": []}, indent=1)
        self.addCleanup(safejson.remove, path)
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"])
        with jobs._lock:
            jobs._running[(repo, key)] = job_id
            jobs._children[job_id] = (repo, key, process)
        try:
            self.assertEqual(jobs.shutdown(grace=0.05), 1)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        self.assertIsNotNone(process.returncode)
        record = jobs.get(repo, job_id)
        self.assertEqual(record["status"], "aborted")
        self.assertEqual(record["events"][-1]["stage"], "aborted")

    def test_job_kinds_receive_their_own_timeout_and_env_override(self):
        """Read-only and operational work must use separate overridable budgets."""
        with mock.patch.object(jobs, "_spawn", return_value="job") as spawn:
            jobs.analyze_pr(REPO, 17, "me", str(ROOT))
            analyze_timeout = spawn.call_args.args[7]
            jobs.operation(REPO, "pr-loop", {}, "me", str(ROOT))
            operation_timeout = spawn.call_args.args[7]
        self.assertEqual(analyze_timeout, jobs.ANALYZE_TIMEOUT)
        self.assertEqual(operation_timeout, jobs.OPERATION_TIMEOUT)
        script = ("import jobs; print(jobs.ANALYZE_TIMEOUT, "
                  "jobs.OPERATION_TIMEOUT)")
        env = dict(os.environ, GIT_WORKFLOW_ANALYZE_TIMEOUT="12",
                   GIT_WORKFLOW_OPERATION_TIMEOUT="34")
        override = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT, env=env,
            capture_output=True, text=True, check=True)
        self.assertEqual(override.stdout.strip(), "12 34")
        env.update(GIT_WORKFLOW_ANALYZE_TIMEOUT="invalid",
                   GIT_WORKFLOW_OPERATION_TIMEOUT="0")
        fallback = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT, env=env,
            capture_output=True, text=True, check=True)
        self.assertEqual(fallback.stdout.strip(), "900 3600")

    def test_timeout_errors_name_the_exhausted_budget(self):
        """A timed-out job must say which configured budget was exhausted."""
        repo = REPO + "-timeout-job"
        job_id, key = "timeout123", "pr-loop"
        path = jobs.job_path(repo, job_id)
        safejson.write(path, {
            "id": job_id, "kind": "operation", "key": key,
            "status": "running", "progress": {}, "events": []}, indent=1)
        self.addCleanup(safejson.remove, path)
        with jobs._lock:
            jobs._running[(repo, key)] = job_id
        with mock.patch.object(jobs, "resolve_agent", return_value="claude"), \
                mock.patch.object(jobs, "command", return_value=["claude"]), \
                mock.patch.object(
                    jobs, "_execute",
                    side_effect=subprocess.TimeoutExpired(["claude"], 11)):
            jobs._run(job_id, key, "claude", repo, "prompt", "", 11,
                      str(ROOT), None, None, None, read_only=False)
        record = jobs.get(repo, job_id)
        self.assertEqual(record["status"], "error")
        self.assertIn("GIT_WORKFLOW_OPERATION_TIMEOUT", record["error"])
        self.assertIn("11 seconds", record["error"])

    def test_both_agents_accept_the_same_explicit_schema(self):
        for agent in ("claude", "codex"):
            command = jobs.command(
                agent, "prompt", jobs.READ_TOOLS, "/tmp/repo",
                "/tmp/result.json", jobs.EXPLAIN_SCHEMA)
            expected = (str(jobs.EXPLAIN_SCHEMA) if agent == "codex"
                        else jobs.claude_schema(jobs.EXPLAIN_SCHEMA))
            self.assertIn(expected, command)

    def test_the_claude_reason_is_read_from_its_stream_not_stderr(self):
        stdout = "\n".join([
            json.dumps({"type": "system", "subtype": "init"}),
            json.dumps({"type": "assistant", "is_api_error_message": True,
                        "message": {"content": [
                            {"type": "text",
                             "text": "Failed to authenticate: OAuth session "
                                     "expired and could not be refreshed"}]}}),
            json.dumps({"type": "result", "is_error": True,
                        "terminal_reason": "api_error",
                        "result": "Failed to authenticate: OAuth session "
                                  "expired and could not be refreshed"})])
        self.assertEqual(
            jobs.stream_failure("claude", stdout),
            "Failed to authenticate: OAuth session expired and could not "
            "be refreshed")

    def test_the_codex_reason_is_read_from_its_error_item(self):
        stdout = "\n".join([
            json.dumps({"type": "thread.started"}),
            json.dumps({"type": "item.completed",
                        "item": {"type": "error", "message": "stream closed"}})])
        self.assertEqual(jobs.stream_failure("codex", stdout), "stream closed")

    def test_a_silent_stream_leaves_only_the_exit_code(self):
        self.assertEqual(jobs.stream_failure("claude", "not json\n"), "")

    def test_a_failed_job_records_why_the_agent_died(self):
        repo = REPO + "-agent-failure"
        key, job_id = "operation-failure", "failagent"
        path = jobs.job_path(repo, job_id)
        safejson.write(path, {
            "id": job_id, "kind": "operation", "key": key,
            "status": "running", "progress": {}, "events": []}, indent=1)
        self.addCleanup(safejson.remove, path)
        with jobs._lock:
            jobs._running[(repo, key)] = job_id
        out = subprocess.CompletedProcess(
            ["claude"], 1,
            json.dumps({"type": "result", "is_error": True,
                        "result": "Failed to authenticate: OAuth session "
                                  "expired and could not be refreshed"}), "")
        with mock.patch.object(jobs, "resolve_agent", return_value="claude"), \
                mock.patch.object(jobs, "command", return_value=["claude"]), \
                mock.patch.object(jobs, "_execute", return_value=out):
            jobs._run(job_id, key, "claude", repo, "prompt", "", 11,
                      str(ROOT), None, None, None, read_only=False)
        record = jobs.get(repo, job_id)
        self.assertEqual(record["status"], "error")
        self.assertIn("claude exited 1", record["error"])
        self.assertIn("Failed to authenticate", record["error"])
        self.assertIn("Failed to authenticate",
                      record["progress"]["detail"])

    def test_a_relaunched_desk_keeps_the_last_run_report(self):
        repo = REPO + "-run-reset"
        deskstate.save(repo, {
            "runs": {"pr-loop": {"status": "needs-input", "report": "due proposte",
                                 "at": "14:24:15"}},
            "feed": [{"at": "14:18:49", "msg": "avvio", "pr": None}],
            "requests": {"run:pr-loop": {"status": "done"}}})
        deskstate.reset(repo)
        state = deskstate.load(repo)
        self.assertEqual(state["runs"]["pr-loop"]["report"], "due proposte")
        self.assertNotIn("feed", state)
        self.assertNotIn("requests", state)

    def test_a_loop_report_outlives_its_job_file(self):
        repo = REPO + "-run-report"
        deskstate.save(repo, {})
        jobs.persist_operation(
            repo, {"status": "needs-input", "report": "due proposte",
                   "provider_changed": False}, None, "pr-loop")
        run = deskstate.load(repo)["runs"]["pr-loop"]
        self.assertEqual(run["status"], "needs-input")
        self.assertEqual(run["report"], "due proposte")
        self.assertRegex(run["at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")

    def test_an_order_report_is_kept_under_its_own_label(self):
        repo = REPO + "-order-report"
        deskstate.save(repo, {})
        jobs.persist_operation(
            repo, {"status": "done", "report": "merged", "provider_changed": False},
            1189, "order")
        state = deskstate.load(repo)
        self.assertEqual(state["orders"]["1189"]["report"], "merged")
        self.assertEqual(state["runs"]["order:1189"]["report"], "merged")

    def test_explanation_is_written_with_its_fingerprint(self):
        repo = REPO + "-explanation"
        deskstate.save(repo, {})
        jobs.persist_explanation(repo, {"n": 17, "what": "  una riga  "},
                                 17, "what-v1")
        self.assertEqual(deskstate.load(repo)["prs"]["17"],
                         {"what": "una riga", "what_key": "what-v1"})

    def test_pr_triage_writes_only_the_requested_artifact_keys(self):
        repo = REPO + "-triage-result"
        deskstate.save(repo, {})
        exported = {
            "model_tasks": {"17": ["analysis"], "18": ["conflict"]},
            "queue": [
                {"n": 17, "model_keys": {"analysis": "a17", "conflict": "c17"}},
                {"n": 18, "model_keys": {"analysis": "a18", "conflict": "c18"}}]}
        empty = {"author": None, "problem": None, "history": None,
                 "propose": None, "draft": None, "verified": [],
                 "not_verified": [], "conflict_kind": None, "finding": None}
        result = {"flow": "pr-triage", "report": "ok", "issues": [], "prs": [
            dict(empty, n=17, author="alice", problem="p", history="h",
                 propose="next", verified=["diff"]),
            dict(empty, n=18, conflict_kind="mechanical", finding="lock file")]}
        jobs.persist_triage(repo, result, "pr-triage", exported)
        records = deskstate.load(repo)["prs"]
        self.assertEqual(records["17"]["analysis_key"], "a17")
        self.assertEqual(records["18"]["conflict_key"], "c18")
        self.assertNotIn("analysis_key", records["18"])

    def test_triage_rejects_an_item_not_in_the_request_file(self):
        with self.assertRaisesRegex(ValueError, "wrong PR triage items"):
            jobs.persist_triage(
                REPO, {"flow": "pr-triage", "report": "", "issues": [],
                       "prs": [{"n": 99}]}, "pr-triage",
                {"model_tasks": {}, "queue": []})

    def test_operational_commands_exist_only_for_an_explicit_job(self):
        for agent in ("claude", "codex"):
            command = jobs.command(
                agent, "prompt", "", str(ROOT), "/tmp/result.json",
                jobs.OPERATION_SCHEMA, read_only=False)
            if agent == "codex":
                self.assertIn("--approve-for-me", command)
                self.assertNotIn("--sandbox", command)
            else:
                self.assertIn("auto", command)
                self.assertNotIn("--dangerously-skip-permissions", command)

    def test_operation_result_updates_the_order_and_refreshes_facts(self):
        repo = REPO + "-operation-result"
        deskstate.save(repo, {"orders": {"17": {"status": "pending"}}})
        result = {"status": "done", "report": "merged #17",
                  "provider_changed": True}
        jobs.persist_operation(repo, result, 17)
        state = deskstate.load(repo)
        self.assertEqual(state["orders"]["17"]["report"], "merged #17")
        self.assertIn("provider_refresh", state)

    def test_issue_analysis_is_persisted_by_the_server(self):
        repo = REPO + "-issue-analysis"
        deskstate.save(repo, {})
        result = {"n": 42, "type": "DEFECT", "finding": "causa verificata",
                  "size": "EASY", "phase": "SINGLE-PHASE",
                  "problem": "rotto", "cause": "guardia mancante",
                  "propose": "aggiungere guardia", "verify": "test mirato",
                  "decision": None}
        jobs.persist_issue_analysis(repo, result, 42)
        record = deskstate.load(repo)["issues"]["42"]
        self.assertEqual(record["finding"], "causa verificata")
        self.assertEqual(record["phase"], "SINGLE-PHASE")
        self.assertTrue(record["at"])


class WhereThingsLive(unittest.TestCase):
    """Temp for what the machine can recreate; home for what a model made."""

    def test_the_session_files_live_under_the_temp_dir(self):
        for path in (cache.cache_path(REPO),
                     jobs.job_path(REPO, "sample"),
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

    def get(self, path, etag=None, host=None):
        req = Request(self.url(path))
        if etag:
            req.add_header("If-None-Match", etag)
        if host:
            req.add_header("Host", host)
        try:
            with urlopen(req, timeout=30) as resp:
                return resp.status, resp.headers.get("ETag"), resp.read()
        except HTTPError as exc:          # urllib raises on 304
            return exc.code, exc.headers.get("ETag"), exc.read()

    def post(self, path, body=None, token=True, host=None):
        data = json.dumps(body or {}).encode()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Git-Workflow-Token"] = self.server.RequestHandlerClass.desk.write_token
        if host:
            headers["Host"] = host
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

    def test_foreign_hosts_are_rejected_for_reads_and_writes(self):
        """Neither HTTP method may trust a rebound foreign host."""
        get_status, _, _ = self.get("/api/meta", host="rebound.example")
        post_status, payload = self.post(
            "/api/run", {"flow": "pr-loop"}, host="rebound.example")
        self.assertEqual(get_status, 403)
        self.assertEqual(post_status, 403)
        self.assertIn("Host", payload["error"])

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

    def test_idle_agent_state_does_not_defeat_the_304(self):
        _, etag, _ = self.get("/api/state")
        time.sleep(1.05)                      # the age in seconds has moved
        status, _, _ = self.get("/api/state", etag)
        self.assertEqual(status, 304)

    def test_the_agent_is_explicitly_on_demand(self):
        _, _, body = self.get("/api/state")
        self.assertEqual(json.loads(body)["agent"]["mode"], "on-demand")

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
        self.assertIn("agent", payload)
        self.assertIn("feed", payload)

    def test_rows_export_is_what_the_triage_reads(self):
        status, payload = self.post("/api/rows")
        self.assertEqual(status, 200)
        exported = json.loads(Path(payload["path"]).read_text())
        self.assertEqual(exported["repo"], REPO)
        self.assertTrue(exported["queue"])
        self.assertTrue(exported["issues"])

    def test_ping_is_local_and_does_not_start_an_agent(self):
        with mock.patch.object(jobs, "operation") as operation:
            status, payload = self.post("/api/ping", {"token": "one"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["pong"], "one")
        operation.assert_not_called()

    def test_order_is_recorded_for_pr_run(self):
        with mock.patch.object(jobs, "operation", return_value="job-order"):
            status, payload = self.post("/api/pr/1152/order", {"propose": "merge it"})
        self.assertEqual(status, 202)
        self.assertEqual(payload["job"], "job-order")
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

    def test_a_moved_pr_is_reverdicted_not_expired(self):
        """The grid is pure engine output: a row the provider moved is
        recomputed on read (0.07 ms), never handed back as `da triagiare`.
        This is what used to make 'molti triage scadono subito'."""
        desk = fresh_desk()
        self.exported(desk)
        row = next(r for r in desk.provider.data["rows"] if r["n"] == 1145)
        row["last"] = {"t": "2026-08-27T13:00:00Z", "who": "cgabriel",
                       "ch": "comment"}
        cache.store(REPO, "queue", desk.provider.queue(REPO, desk.me))
        queue = desk.queue()
        moved = next(r for r in queue["rows"] if r["n"] == 1145)
        self.assertEqual(moved["triage_status"], "current")
        self.assertEqual(moved["todo"], "answer the review")   # live verdict
        self.assertTrue(queue["triage_complete"])
        self.assertEqual(queue["triage_counts"]["stale"], 0)
        self.assertEqual(sum(len(b["rows"]) for b in queue["grid"]["blocks"]),
                         len(queue["rows"]))

    def test_cold_gates_do_not_expire_the_triage(self):
        """A relaunch clears the cache but keeps the grid; the warm-up must
        not read as 51 righe 'da aggiornare'."""
        desk = fresh_desk()
        self.exported(desk)
        cache.clear(REPO)
        counts = desk.queue()["triage_counts"]
        self.assertEqual(counts["stale"], 0)
        self.assertEqual(counts["missing"], 0)

    def test_a_conflict_note_upgrades_the_row_without_a_press(self):
        """conflict_kind used to expire the row it explained (it is a
        VERDICT_FIELD); now the engine folds it in on the next read."""
        desk = fresh_desk()
        self.exported(desk)
        row = next(r for r in desk.queue()["rows"] if r["n"] == 1083)
        deskstate.update(REPO, lambda s: s.setdefault("prs", {}).update(
            {"1083": {"conflict_kind": "mechanical",
                       "conflict_key": row["model_keys"]["conflict"]}}))
        row = next(r for r in desk.queue()["rows"] if r["n"] == 1083)
        self.assertEqual(row["triage_status"], "current")
        self.assertEqual(row["autorun"], "A3")

    def test_a_new_pr_is_the_only_untriaged_one(self):
        desk = fresh_desk()
        self.exported(desk)
        template = dict(desk.provider.data["rows"][0], n=9999,
                        title="brand new", author="dgpaci")
        desk.provider.data["rows"].append(template)
        cache.store(REPO, "queue", desk.provider.queue(REPO, desk.me))
        cache.store(REPO, "membership", desk.provider.open_numbers(REPO, desk.me))
        queue = desk.queue()
        missing = [r["n"] for r in queue["rows"]
                   if r["triage_status"] == "missing"]
        self.assertEqual(missing, [9999])
        self.assertFalse(queue["triage_complete"])


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
        live = desk.queue()["rows"][0]
        n = live["n"]
        state = deskstate.load(REPO)
        state["prs"] = {str(n): {"what": "riscrive il dispatch dei trigger",
                                  "what_key": live["model_keys"]["what"]}}
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
        live = dirty[0]
        n = live["n"]
        deskstate.save(REPO, {"prs": {str(n): {
            "conflict_kind": "mechanical",
            "conflict_key": live["model_keys"]["conflict"]}}})
        desk.run_triage()
        row = next(r for r in desk.queue()["rows"] if r["n"] == n)
        self.assertEqual(row["autorun"], "A3")


class RelaunchKeepsTheTriage(unittest.TestCase):
    """The triage is durable: the grid re-verdicts itself on every read and
    the model's notes are dated, so a relaunch keeps them and drops only the
    session's ephemera — feed, requests, working markers."""

    def test_a_relaunch_keeps_grid_and_notes_drops_the_ephemera(self):
        desk = fresh_desk()
        desk.run_triage()
        deskstate.update(REPO, lambda s: s.update(
            prs={"1145": {"what": "una riga"}},
            feed=[{"msg": "vecchia"}],
            requests={"run:pr-loop": {"status": "pending"}}))
        deskstate.reset(REPO)
        state = deskstate.load(REPO)
        self.assertIn("grid", state)
        self.assertEqual(state["prs"]["1145"]["what"], "una riga")
        self.assertNotIn("feed", state)
        self.assertNotIn("requests", state)
        self.assertTrue(desk.queue()["triage_complete"])


class IncrementalModelTriage(unittest.TestCase):
    """The press asks only for stale model-owned artifacts."""

    def setUp(self):
        deskstate.save(REPO, {})

    def test_only_asks_and_dirty_conflicts_need_the_model(self):
        desk = fresh_desk()
        rows = json.loads(Path(desk.run_triage()).read_text())
        by_n = {str(row["n"]): row for row in rows["queue"]}
        self.assertEqual(set(rows["needs_model"]),
                         {row["n"] for row in rows["queue"]
                          if row["autorun"] == "asks"})
        self.assertEqual(rows["model_tasks"]["1083"], ["conflict"])
        notes = {}
        for n, tasks in rows["model_tasks"].items():
            row = by_n[n]
            if tasks == ["conflict"]:
                notes[n] = {"conflict_kind": "substantive",
                            "conflict_key": row["model_keys"]["conflict"]}
            else:
                notes[n] = {"analysis": "letta",
                            "analysis_key": row["model_keys"]["analysis"]}
        deskstate.update(REPO, lambda s: s.update(prs=notes))
        again = json.loads(Path(desk.run_triage()).read_text())
        self.assertEqual(again["needs_model"], [])

    def test_a_same_day_push_invalidates_an_analysis(self):
        desk = fresh_desk()
        rows = json.loads(Path(desk.run_triage()).read_text())
        row = next(r for r in rows["queue"] if r["n"] == 1145)
        deskstate.update(REPO, lambda s: s.update(prs={"1145": {
            "analysis": "letta", "analysis_key": row["model_keys"]["analysis"]}}))
        source = next(r for r in desk.provider.data["rows"] if r["n"] == 1145)
        source["head"] = "a-new-head-on-the-same-day"
        cache.clear(REPO)
        again = json.loads(Path(desk.run_triage()).read_text())
        self.assertIn(1145, again["needs_model"])

    def test_a_new_base_invalidates_a_conflict_read(self):
        desk = fresh_desk()
        rows = json.loads(Path(desk.run_triage()).read_text())
        row = next(r for r in rows["queue"] if r["n"] == 1083)
        deskstate.update(REPO, lambda s: s.update(prs={"1083": {
            "conflict_kind": "mechanical",
            "conflict_key": row["model_keys"]["conflict"]}}))
        source = next(r for r in desk.provider.data["rows"] if r["n"] == 1083)
        source["base_head"] = "base-moved"
        cache.clear(REPO)
        again = json.loads(Path(desk.run_triage()).read_text())
        self.assertEqual(again["model_tasks"]["1083"], ["conflict"])

    def test_a_review_change_keeps_the_problem_key(self):
        row = {"author": "me", "draft": False, "base": "develop",
               "base_head": "base", "head": "head", "merge": "BLOCKED",
               "decision": None, "req": [], "reviews": [], "unresolved": 0,
               "incomplete": False, "assignees": ["me"], "last": None,
               "title": "one", "summary": "body", "closes": []}
        changed = dict(row, reviews=[{"who": "x", "state": "APPROVED"}],
                       decision="APPROVED")
        before = prdesk.model_keys(row)
        after = prdesk.model_keys(changed)
        self.assertEqual(before["problem"], after["problem"])
        self.assertNotEqual(before["history"], after["history"])
        self.assertNotEqual(before["analysis"], after["analysis"])

    def test_a_title_edit_does_not_invalidate_an_analysis(self):
        desk = fresh_desk()
        rows = json.loads(Path(desk.run_triage()).read_text())
        row = next(r for r in rows["queue"] if r["n"] == 1145)
        deskstate.update(REPO, lambda s: s.update(prs={"1145": {
            "analysis": "letta", "analysis_key": row["model_keys"]["analysis"]}}))
        row = next(r for r in desk.provider.data["rows"] if r["n"] == 1145)
        row["title"] = "retitled without changing the review"
        cache.clear(REPO)
        again = json.loads(Path(desk.run_triage()).read_text())
        self.assertNotIn(1145, again["needs_model"])

    def test_a_gate_still_loading_does_not_stale_an_analysis(self):
        row = {"n": 1, "author": "me", "draft": False, "base": "develop",
               "base_head": "base", "head": "head", "merge": "BLOCKED",
               "decision": None, "req": [], "reviews": [], "unresolved": 0,
               "incomplete": False, "assignees": ["me"], "last": None,
               "title": "one", "summary": None, "closes": []}
        self.assertIsNone(prdesk.model_keys(row, gate_known=False)["analysis"])
        self.assertTrue(prdesk.model_keys(row, gate_known=True)["analysis"])


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

    def test_explanation_validity_ignores_issue_assignment(self):
        row = dict(self.ROW, closes=[{"issue": 7, "title": "bug",
                                     "assignees": ["one"]}])
        moved = dict(row, closes=[{"issue": 7, "title": "bug",
                                   "assignees": ["two"]}])
        self.assertEqual(prdesk.model_keys(row)["what"],
                         prdesk.model_keys(moved)["what"])

    def test_explanation_validity_reads_title_body_and_linked_issue(self):
        base = prdesk.model_keys(self.ROW)["what"]
        for changed in (dict(self.ROW, title="two"),
                        dict(self.ROW, summary="new body"),
                        dict(self.ROW, closes=[{"issue": 7, "title": "bug"}])):
            self.assertNotEqual(base, prdesk.model_keys(changed)["what"])


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

    def test_the_desk_computes_the_shortlist_every_read(self):
        got = fresh_desk().issues()
        self.assertTrue(got["shortlist"]["rows"])
        self.assertFalse(got["ranked"])
        self.assertTrue(all("cross" in row for row in got["rows"]))
        self.assertTrue(all("in_shortlist" in row for row in got["rows"]))

    def test_a_model_ranking_reorders_it_and_nothing_else(self):
        desk = fresh_desk()
        before = [r["n"] for r in desk.issues()["shortlist"]["rows"]]
        last = before[-1]
        deskstate.save(REPO, {"issues": {str(last): {"impact": 1,
                                                     "finding": "rompe il salvataggio"}}})
        got = desk.issues()
        after = [r["n"] for r in got["shortlist"]["rows"]]
        self.assertTrue(got["ranked"])
        self.assertEqual(after[0], last)
        self.assertEqual(sorted(after), sorted(before), "the filter is not the model's")

    def test_the_model_type_wins_over_the_label_guess(self):
        desk = fresh_desk()
        n = desk.issues()["rows"][0]["n"]
        deskstate.save(REPO, {"issues": {str(n): {"type": "REQUEST"}}})
        row = next(r for r in desk.issues()["rows"] if r["n"] == n)
        self.assertEqual(row["type"], "REQUEST")

    def test_an_analysis_the_issue_moved_past_is_marked_stale(self):
        desk = fresh_desk()
        row = desk.issues()["rows"][0]
        deskstate.save(REPO, {"issues": {str(row["n"]): {
            "finding": "vecchia", "at": "2020-01-01T10:00:00"}}})
        got = next(r for r in desk.issues()["rows"] if r["n"] == row["n"])
        self.assertTrue(got["analysis_stale"])
        deskstate.save(REPO, {"issues": {str(row["n"]): {
            "finding": "fresca", "at": row["updated"]}}})
        got = next(r for r in desk.issues()["rows"] if r["n"] == row["n"])
        self.assertFalse(got["analysis_stale"])

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

    def test_a_completed_run_requests_one_fresh_provider_read(self):
        state = deskstate.load(REPO)
        state.pop("provider_refresh", None)
        deskstate.save(REPO, state)
        out = subprocess.run(
            (sys.executable, str(ROOT / "notify.py"), "--repo", REPO,
             "--done", "run:pr-loop", "fatto"),
            capture_output=True, text=True, env=dict(os.environ, HOME=_HOME))
        self.assertEqual(out.returncode, 0, out.stderr)
        refresh = deskstate.load(REPO)["provider_refresh"]
        self.assertTrue(refresh["token"])
        self.assertEqual(fresh_desk().live_state()["provider_refresh"], refresh)

    def test_an_explicit_provider_refresh_is_available_to_every_host(self):
        state = deskstate.load(REPO)
        state.pop("provider_refresh", None)
        deskstate.save(REPO, state)
        out = subprocess.run(
            (sys.executable, str(ROOT / "notify.py"), "--repo", REPO,
             "--refresh-provider", "provider cambiato"),
            capture_output=True, text=True, env=dict(os.environ, HOME=_HOME))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("provider_refresh", deskstate.load(REPO))


class DetachedButtons(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.desk = fresh_desk(chat=False, kind="pr")
        prdesk.Handler.desk = cls.desk
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
                               "X-Git-Workflow-Token": self.desk.write_token})
        with urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read())

    def test_boot_and_snapshot_never_start_an_agent(self):
        with mock.patch.object(jobs, "analyze_pr") as analyze, \
                mock.patch.object(jobs, "triage") as triage, \
                mock.patch.object(jobs, "operation") as operation:
            self.desk.snapshot()
        analyze.assert_not_called()
        triage.assert_not_called()
        operation.assert_not_called()

    def test_analyze_and_explain_start_one_shot_jobs(self):
        with mock.patch.object(jobs, "analyze_pr", return_value="analyze-job") as analyze:
            status, payload = self.post("/api/pr/1145/analyze")
        self.assertEqual((status, payload["job"]), (202, "analyze-job"))
        self.assertEqual(analyze.call_args.args[1], 1145)
        with mock.patch.object(jobs, "explain_pr", return_value="explain-job") as explain:
            status, payload = self.post("/api/pr/1145/explain")
        self.assertEqual((status, payload["job"]), (202, "explain-job"))
        self.assertTrue(explain.call_args.args[-1])

    def test_triage_answers_with_a_job_before_reading_the_provider(self):
        """The fresh read and the grid belong to the job, not to the click."""
        with mock.patch.object(jobs, "triage", return_value="triage-job") as triage:
            status, payload = self.post("/api/triage", {"flow": "pr-triage"})
        self.assertEqual((status, payload["job"]), (202, "triage-job"))
        export = triage.call_args.args[2]
        self.assertTrue(callable(export), "the handler must not run the export")
        self.assertTrue(Path(export()).exists())

    def test_run_records_the_exact_scope_and_clamps_batch(self):
        with mock.patch.object(jobs, "operation", return_value="run-job") as operation:
            status, payload = self.post(
                "/api/run", {"flow": "pr-loop", "ns": [1145, 1128], "batch": 99})
        self.assertEqual((status, payload["job"]), (202, "run-job"))
        self.assertEqual(payload["ns"], [1145, 1128])
        self.assertEqual(payload["batch"], prdesk.MAX_BATCH)
        self.assertEqual(operation.call_args.args[2],
                         {"ns": [1145, 1128], "batch": prdesk.MAX_BATCH})

    def test_issue_analysis_is_an_explicit_read_only_job(self):
        with mock.patch.object(jobs, "analyze_issue", return_value="issue-job") as analyze:
            status, payload = self.post("/api/issue/1166/analyze")
        self.assertEqual((status, payload["job"]), (202, "issue-job"))
        self.assertEqual(analyze.call_args.args[1], 1166)


class AttachedChat(unittest.TestCase):
    """The hybrid contract: triage always on the independent one-shot agent;
    every other button routes to the attached chat while its heartbeat is
    fresh, and falls back to the one-shot job when it is not."""

    @classmethod
    def setUpClass(cls):
        cls.desk = fresh_desk(chat=False, kind="pr")
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
        for key in ("requests", "chat"):
            state.pop(key, None)
        deskstate.save(REPO, state)

    def post(self, path, body=None):
        req = Request("http://127.0.0.1:%s%s" % (self.port, path),
                      data=json.dumps(body or {}).encode(),
                      headers={"Content-Type": "application/json",
                               "X-Git-Workflow-Token": self.desk.write_token})
        with urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read())

    def _expire_heartbeat(self):
        state = deskstate.load(REPO)
        state["chat"]["epoch"] -= deskstate.CHAT_STALE + 5
        deskstate.save(REPO, state)

    def test_the_heartbeat_expires(self):
        deskstate.chat_heartbeat(REPO)
        self.assertTrue(deskstate.chat_attached(REPO))
        self._expire_heartbeat()
        self.assertIsNone(deskstate.chat_attached(REPO))

    def test_a_claimed_request_keeps_the_chat_attached_while_it_works(self):
        deskstate.chat_heartbeat(REPO)
        deskstate.request(REPO, "analyze:7", "analyze", 7, via="chat")
        record = deskstate.claim_request(REPO)
        self.assertEqual(record["key"], "analyze:7")
        self.assertEqual(record["status"], "taken")
        self._expire_heartbeat()
        self.assertTrue(deskstate.chat_attached(REPO),
                        "silence while working must not read as a dead chat")
        deskstate.close_request(REPO, "analyze:7", "done", "fatto")
        self.assertIsNone(deskstate.chat_attached(REPO))

    def test_a_taken_request_uses_the_busy_ttl_from_its_claim(self):
        deskstate.chat_heartbeat(REPO)
        deskstate.request(REPO, "analyze:8", "analyze", 8, via="chat")
        deskstate.claim_request(REPO)
        state = deskstate.load(REPO)
        state["requests"]["analyze:8"]["epoch"] -= deskstate.REQUEST_STALE + 5
        deskstate.save(REPO, state)
        record, created = deskstate.request(
            REPO, "analyze:8", "analyze", 8, via="chat")
        self.assertFalse(created)
        self.assertEqual(record["status"], "taken")

    def test_non_triage_buttons_route_to_the_attached_chat(self):
        deskstate.chat_heartbeat(REPO)
        with mock.patch.object(jobs, "analyze_pr") as analyze:
            status, payload = self.post("/api/pr/1145/analyze")
        analyze.assert_not_called()
        self.assertEqual((status, payload["via"]), (202, "chat"))
        record = deskstate.load(REPO)["requests"]["analyze:1145"]
        self.assertEqual((record["via"], record["status"]), ("chat", "queued"))
        with mock.patch.object(jobs, "operation") as operation:
            status, payload = self.post(
                "/api/run", {"flow": "pr-loop", "ns": [1145], "batch": 2})
        operation.assert_not_called()
        self.assertEqual(payload["via"], "chat")
        self.assertEqual(
            deskstate.load(REPO)["requests"]["run:pr-loop"]["payload"],
            {"flow": "pr-loop", "ns": [1145], "batch": 2})

    def test_a_second_press_reuses_the_queued_request(self):
        deskstate.chat_heartbeat(REPO)
        with mock.patch.object(jobs, "analyze_issue"):
            self.post("/api/issue/1166/analyze")
            status, payload = self.post("/api/issue/1166/analyze")
        self.assertEqual((status, payload["created"]), (202, False))

    def test_triage_never_routes_to_the_chat(self):
        deskstate.chat_heartbeat(REPO)
        with mock.patch.object(jobs, "triage", return_value="triage-job") as triage:
            status, payload = self.post("/api/triage", {"flow": "pr-triage"})
        self.assertEqual(status, 202)
        self.assertNotIn("via", payload)
        self.assertEqual(payload["job"], "triage-job")
        triage.assert_called_once()

    def test_a_stale_heartbeat_falls_back_to_the_one_shot_job(self):
        deskstate.chat_heartbeat(REPO)
        self._expire_heartbeat()
        with mock.patch.object(jobs, "analyze_pr", return_value="job-1"):
            status, payload = self.post("/api/pr/1145/analyze")
        self.assertEqual((status, payload["job"]), (202, "job-1"))

    def test_a_chat_that_died_mid_request_does_not_swallow_the_next_click(self):
        """A `taken` record nothing expires kept the chat "attached" for an
        hour: every later click was enqueued for a conversation that had
        ended, so no job ever started and the desk showed nothing."""
        deskstate.chat_heartbeat(REPO)
        deskstate.request(REPO, "analyze:7", "analyze", 7, via="chat")
        deskstate.claim_request(REPO)
        self._expire_heartbeat()
        self.assertTrue(deskstate.chat_attached(REPO), "the chip still says so")
        with mock.patch.object(jobs, "analyze_pr", return_value="job-9"):
            status, payload = self.post("/api/pr/1145/analyze")
        self.assertEqual((status, payload["job"]), (202, "job-9"))

    def test_a_click_the_chat_never_took_goes_back_to_a_one_shot_job(self):
        deskstate.chat_heartbeat(REPO)
        with mock.patch.object(jobs, "analyze_pr") as analyze:
            self.assertEqual(self.post("/api/pr/1145/analyze")[1]["via"], "chat")
        analyze.assert_not_called()
        state = deskstate.load(REPO)
        state["requests"]["analyze:1145"]["epoch"] -= (
            deskstate.CHAT_CLAIM_GRACE + 5)
        deskstate.save(REPO, state)
        self._expire_heartbeat()
        with mock.patch.object(jobs, "analyze_pr", return_value="job-10"):
            status, payload = self.post("/api/pr/1145/analyze")
        self.assertEqual((status, payload["job"]), (202, "job-10"))
        self.assertEqual(
            deskstate.load(REPO)["requests"]["analyze:1145"]["status"], "stale")

    def test_a_listening_chat_still_takes_the_click(self):
        deskstate.chat_heartbeat(REPO)
        with mock.patch.object(jobs, "analyze_pr") as analyze:
            status, payload = self.post("/api/pr/1145/analyze")
        analyze.assert_not_called()
        self.assertEqual((status, payload["via"]), (202, "chat"))

    def _cli(self, *args, **kw):
        return subprocess.run(
            (sys.executable, str(ROOT / "chatdesk.py")) + args,
            capture_output=True, text=True,
            env=dict(os.environ, HOME=_HOME), **kw)

    def test_wait_claims_the_click_and_result_publishes_it(self):
        deskstate.request(REPO, "issue-analyze:1166", "issue-analyze", 1166,
                          via="chat")
        out = self._cli("wait", "--repo", REPO, "--timeout", "1")
        self.assertEqual(out.returncode, 0, out.stderr)
        record = json.loads(out.stdout)
        self.assertEqual(record["key"], "issue-analyze:1166")
        self.assertTrue(deskstate.chat_attached(REPO),
                        "the claim itself must keep the chat attached")
        result = {"n": 1166, "type": "DEFECT", "finding": "la causa è X",
                  "size": "EASY", "phase": "SINGLE-PHASE"}
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as handle:
            json.dump(result, handle)
        out = self._cli("result", "--repo", REPO,
                        "--request", "issue-analyze:1166", handle.name)
        self.assertEqual(out.returncode, 0, out.stderr)
        state = deskstate.load(REPO)
        self.assertEqual(state["issues"]["1166"]["finding"], "la causa è X")
        got = state["requests"]["issue-analyze:1166"]
        self.assertEqual((got["status"], got["report"]),
                         ("done", "la causa è X"))

    def test_wait_reports_idle_when_nothing_is_queued(self):
        out = self._cli("wait", "--repo", REPO, "--timeout", "0")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), {"idle": True})

    def test_an_invalid_result_fails_the_request_instead_of_publishing(self):
        deskstate.request(REPO, "issue-analyze:1167", "issue-analyze", 1167,
                          via="chat")
        deskstate.claim_request(REPO)
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as handle:
            json.dump({"n": 1167, "type": "DEFECT"}, handle)
        out = self._cli("result", "--repo", REPO,
                        "--request", "issue-analyze:1167", handle.name)
        self.assertNotEqual(out.returncode, 0)
        state = deskstate.load(REPO)
        self.assertEqual(state["requests"]["issue-analyze:1167"]["status"],
                         "failed")
        self.assertNotIn("finding", (state.get("issues") or {}).get("1167", {}))

    def test_a_non_object_result_fails_the_request(self):
        deskstate.request(REPO, "issue-analyze:1168", "issue-analyze", 1168,
                          via="chat")
        deskstate.claim_request(REPO)
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as handle:
            json.dump([], handle)
        out = self._cli("result", "--repo", REPO,
                        "--request", "issue-analyze:1168", handle.name)
        self.assertNotEqual(out.returncode, 0)
        state = deskstate.load(REPO)
        self.assertEqual(state["requests"]["issue-analyze:1168"]["status"],
                         "failed")

    def test_an_operation_result_lands_in_orders_and_runs(self):
        deskstate.add_order(REPO, 1145, "merge", "", "vai")
        deskstate.request(REPO, "order:1145", "order", 1145,
                          via="chat", payload={"flow": "order", "n": 1145})
        deskstate.claim_request(REPO)
        result = {"status": "done", "report": "merged e branch cancellato",
                  "provider_changed": True}
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as handle:
            json.dump(result, handle)
        out = self._cli("result", "--repo", REPO,
                        "--request", "order:1145", handle.name)
        self.assertEqual(out.returncode, 0, out.stderr)
        state = deskstate.load(REPO)
        self.assertEqual(state["orders"]["1145"]["status"], "done")
        self.assertEqual(state["runs"]["order:1145"]["report"],
                         "merged e branch cancellato")
        self.assertIn("provider_refresh", state)

    def test_detach_hands_the_buttons_back_to_the_agents(self):
        deskstate.chat_heartbeat(REPO)
        out = self._cli("detach", "--repo", REPO)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIsNone(deskstate.chat_attached(REPO))

    def test_live_state_tells_the_page_a_chat_is_attached(self):
        deskstate.chat_heartbeat(REPO)
        self.assertTrue(self.desk.live_state()["chat"]["attached"])
        self._expire_heartbeat()
        self.assertFalse(self.desk.live_state()["chat"]["attached"])


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


class LaneCheck(unittest.TestCase):
    """One deterministic Lane A pass — pr-loop reads this instead of one
    `gh pr view` per PR per pass."""

    def setUp(self):
        deskstate.save(REPO, {})
        cache.clear(REPO)
        self.provider = get_provider("fixture")
        self.me = self.provider.whoami()

    def _check(self, **kw):
        cache.clear(REPO)
        return lane_check.check(self.provider, REPO, self.me, **kw)

    def test_every_row_lands_in_exactly_one_lane(self):
        got = self._check()
        counted = [n for ns in got["lanes"].values() for n in ns]
        self.assertEqual(sorted(counted), sorted(r["n"] for r in got["rows"]))
        self.assertEqual(got["fixed_point"],
                         not got["lanes"]["A1"] and not got["lanes"]["A3"])
        for row in got["rows"]:
            self.assertTrue(set(lane_check.ROW_FIELDS) <= set(row))

    def test_a2_is_never_the_engine_s_call(self):
        """An A2 needs the review body read — that is the model's work."""
        self.assertEqual(self._check()["lanes"]["A2"], [])

    def test_ns_narrows_and_names_the_unseen(self):
        got = self._check(ns=[1145, 99999])
        self.assertEqual([r["n"] for r in got["rows"]], [1145])
        self.assertEqual(got["not_in_queue"], [99999])

    def test_an_inspected_mechanical_conflict_becomes_a3(self):
        """The model writes conflict_kind after reading the diff; the next
        pass reads it and the DIRTY row classifies itself."""
        before = self._check()
        self.assertNotIn(1083, before["lanes"]["A3"])
        row = next(row for row in before["rows"] if row["n"] == 1083)
        deskstate.save(REPO, {"prs": {"1083": {
            "conflict_kind": "mechanical",
            "conflict_key": row["model_keys"]["conflict"]}}})
        after = self._check()
        self.assertIn(1083, after["lanes"]["A3"])
        self.assertFalse(after["fixed_point"])

    def test_a_fully_approved_own_pr_is_an_a1(self):
        row = next(r for r in self.provider.data["rows"] if r["n"] == 1113)
        row["req"] = []
        row["assignees"] = [self.me]
        self.provider.data["gates"]["develop"]["can_land"] = True
        got = self._check()
        self.assertIn(1113, got["lanes"]["A1"])

    def test_the_cli_prints_the_same_pass(self):
        cache.clear(REPO)
        out = subprocess.run(
            (sys.executable, str(ROOT / "lane_check.py"), "--provider",
             "fixture", "--repo", REPO, "--ns", "#1145,1128"),
            capture_output=True, text=True, env=dict(os.environ, HOME=_HOME))
        self.assertEqual(out.returncode, 0, out.stderr)
        got = json.loads(out.stdout)
        self.assertEqual(sorted(r["n"] for r in got["rows"]), [1128, 1145])
