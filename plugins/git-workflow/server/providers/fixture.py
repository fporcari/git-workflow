"""Fixture provider — replays a recorded payload, zero network.

Exists for the test environment: the desk's UI and its HTTP layer can be
exercised, benchmarked and regression-tested at full speed without touching
GitHub. Capture a fresh payload with `python3 tests/capture.py owner/repo`.

    python3 prdesk.py --provider fixture --repo genropy/genropy
"""

import json
import os
import time
from pathlib import Path

from .base import Provider

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


class FixtureProvider(Provider):
    name = "fixture"

    def __init__(self, host=None):
        super().__init__(host)
        path = os.environ.get("DESK_FIXTURE")
        self.path = Path(path) if path else (FIXTURE_DIR / "genropy.json")
        self.data = json.loads(self.path.read_text())
        # DESK_FIXTURE_LATENCY fakes the provider's real cost, so a benchmark
        # can measure the caching layer without waiting on GitHub.
        self.latency = float(os.environ.get("DESK_FIXTURE_LATENCY") or 0)

    def _sleep(self):
        if self.latency:
            time.sleep(self.latency)

    def whoami(self):
        return self.data.get("me", "fixture-user")

    def queue(self, repo, me):
        self._sleep()
        rows = []
        for source in self.data["rows"]:
            row = dict(source, merge=None)
            row.setdefault("assignees", [row["author"]])
            row.setdefault("labels", [])
            row.setdefault("base_head", None)
            row.setdefault("head", None)
            row.setdefault("incomplete", False)
            rows.append(row)
        return {"rows": rows, "total": self.data.get("queue_total", len(rows)),
                "truncated": bool(self.data.get("queue_truncated"))}

    def open_numbers(self, repo, me):
        self._sleep()
        return [row["n"] for row in self.data["rows"]
                if row.get("state", "OPEN") == "OPEN"]

    def mergestates(self, repo, me):
        self._sleep()
        return {str(row["n"]): row.get("merge") or "UNKNOWN"
                for row in self.data["rows"] if row.get("author") == self.whoami()}

    def analysis_probe(self, repo, n):
        row = next((row for row in self.data["rows"] if row["n"] == n), None)
        if not row:
            return None
        return {
            "fresh": True, "head": row.get("head"),
            "base_head": row.get("base_head"), "merge": row.get("merge"),
            "decision": row.get("decision"), "requests": row.get("req") or [],
            "reviews": row.get("reviews") or [],
            "threads": row.get("threads", 0),
            "unresolved": row.get("unresolved", 0),
            "incomplete": row.get("incomplete", False),
            "checks": {"state": row.get("checks_state"), "items": []},
        }

    def issues(self, repo):
        self._sleep()
        rows = [dict(row) for row in self.data["issues"]]
        return {"rows": rows, "total": self.data.get("issues_total", len(rows)),
                "truncated": bool(self.data.get("issues_truncated"))}

    def merge_command(self, repo, n):
        return "gh pr merge %s --repo %s --squash --delete-branch" % (n, repo)

    def default_branch(self, repo):
        return self.data.get("default_branch") or "main"

    def gates(self, repo, me, bases):
        self._sleep()
        recorded = self.data.get("gates") or {}
        return {b: recorded[b] for b in bases if b in recorded}

    def remote_branches(self, cwd):
        return self.data.get("branches") or []

    def issue_relations(self, repo, me):
        self._sleep()
        return self.data.get("issue_relations") or {
            "commented": [], "assigned": [], "complete": True}

    # ---- per-item reads (gw): recorded under pr_details / issue_details /
    # diffs / api, keyed by number or endpoint; a PR without a recorded
    # detail is synthesized from its queue row so every fixture serves gw.

    def pulls(self, repo, state="open"):
        rows = [row for row in self.data["rows"] if row.get("state", "OPEN") == state.upper()]
        return [{"n": r["n"], "title": r["title"], "author": r["author"],
                 "draft": r.get("draft", False), "base": r.get("base"), "head": r.get("head_ref"),
                 "created": r["created"], "updated": r.get("updated"),
                 "labels": r.get("labels") or [], "assignees": r.get("assignees") or [r["author"]],
                 "req": r.get("req") or [], "url": r.get("url")} for r in rows]

    def pr_detail(self, repo, n):
        recorded = (self.data.get("pr_details") or {}).get(str(n))
        if recorded:
            return recorded
        row = next((row for row in self.data["rows"] if row["n"] == n), None)
        if not row:
            raise RuntimeError("fixture has no PR %s" % n)
        reviews = row.get("reviews") or []
        return {"n": n, "title": row["title"], "body": row.get("summary") or "",
                "state": "open", "draft": row.get("draft", False), "author": row["author"],
                "assignees": row.get("assignees") or [row["author"]],
                "labels": row.get("labels") or [], "created": row["created"], "updated": None,
                "base": {"ref": row.get("base"), "sha": row.get("base_head")},
                "head": {"ref": row.get("head_ref"), "sha": row.get("head")},
                "merge": row.get("merge") or "UNKNOWN",
                "decision": row.get("decision"), "req": row.get("req") or [],
                "reviews": reviews,
                "closes": [{"issue": c["issue"], "source": "provider"} for c in row.get("closes") or []],
                "comments": row.get("threads", 0), "url": row.get("url")}

    def pr_reviews(self, repo, n):
        return self.pr_detail(repo, n)["reviews"]

    def pr_diff(self, repo, n):
        diffs = self.data.get("diffs") or {}
        if str(n) not in diffs:
            raise RuntimeError("fixture has no diff for PR %s" % n)
        return diffs[str(n)]

    def issue_detail(self, repo, n):
        recorded = (self.data.get("issue_details") or {}).get(str(n))
        if recorded:
            return recorded
        row = next((row for row in self.data["issues"] if row["n"] == n), None)
        if not row:
            raise RuntimeError("fixture has no issue %s" % n)
        return {"n": n, "title": row["title"], "body": "", "state": "open",
                "author": row["author"], "assignees": row.get("assignees") or [],
                "labels": row.get("labels") or [], "created": row["created"],
                "updated": row.get("updated"), "url": row.get("url"), "comments": []}

    def api(self, endpoint, method="GET", fields=None):
        recorded = self.data.get("api") or {}
        key = "%s %s" % (method, endpoint.lstrip("/"))
        if key not in recorded:
            raise RuntimeError("fixture has no recorded call %r" % key)
        return recorded[key]
