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
        rows = [dict(row, merge=None) for row in self.data["rows"]]
        return {"rows": rows, "total": self.data.get("queue_total", len(rows)),
                "truncated": bool(self.data.get("queue_truncated"))}

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
