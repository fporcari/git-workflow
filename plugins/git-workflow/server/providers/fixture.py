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

    def __init__(self):
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
