"""Forgejo provider — talks to the Forgejo (Gitea-compatible) REST API v1.

Configuration by environment:
    FORGEJO_URL    e.g. https://git.example.org
    FORGEJO_TOKEN  a personal access token (read scope on repos and issues)

Field mapping notes:
- `mergeable` maps to CLEAN/DIRTY; Forgejo has no BLOCKED/UNSTABLE composite,
  the branch-protection gate stays with the reviews.
- review states arrive as APPROVED / REQUEST_CHANGES / COMMENT and are
  normalized to the GitHub vocabulary the verdict engine speaks.
- unresolved review threads are not exposed by the API; `unresolved` is
  reported as 0 and `threads` counts review comments.
"""

import json
import os
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .base import Provider

STATE_MAP = {"REQUEST_CHANGES": "CHANGES_REQUESTED"}


class ForgejoProvider(Provider):
    name = "forgejo"

    def __init__(self):
        self.base = os.environ.get("FORGEJO_URL", "").rstrip("/")
        self.token = os.environ.get("FORGEJO_TOKEN", "")
        if not self.base or not self.token:
            raise SystemExit("forgejo provider needs FORGEJO_URL and FORGEJO_TOKEN in the environment")

    def _get(self, path, **params):
        url = "%s/api/v1%s" % (self.base, path)
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Authorization": "token %s" % self.token})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())

    def whoami(self):
        return self._get("/user")["login"]

    def queue(self, repo, me):
        pulls = self._get("/repos/%s/pulls" % repo, state="open", limit=50)
        with ThreadPoolExecutor(max_workers=6) as pool:
            reviews = list(pool.map(
                lambda pr: self._get("/repos/%s/pulls/%s/reviews" % (repo, pr["number"])), pulls))
        rows = []
        for pr, revs in zip(pulls, reviews):
            row = self._row(repo, pr, revs or [])
            if self._involves(row, me):
                rows.append(row)
        rows.sort(key=lambda r: r["created"], reverse=True)
        return rows

    def _involves(self, row, me):
        return (row["author"] == me or me in row["req"]
                or any(r["who"] == me for r in row["reviews"]))

    def _row(self, repo, pr, revs):
        reviews, spoke = [], []
        for r in revs:
            state = STATE_MAP.get(r.get("state", ""), r.get("state", ""))
            if state in ("APPROVED", "CHANGES_REQUESTED", "COMMENT", "COMMENTED"):
                who = (r.get("user") or {}).get("login")
                on = (r.get("submitted_at") or "")[:10]
                reviews.append({"who": who, "state": "COMMENTED" if state.startswith("COMMENT") else state, "on": on})
                if r.get("submitted_at"):
                    spoke.append({"t": r["submitted_at"], "who": who, "ch": state.lower()})
        by_user = {}
        for r in reviews:
            by_user[r["who"]] = r["state"]
        if "CHANGES_REQUESTED" in by_user.values():
            decision = "CHANGES_REQUESTED"
        elif "APPROVED" in by_user.values():
            decision = "APPROVED"
        else:
            decision = "REVIEW_REQUIRED" if reviews else None
        spoke.sort(key=lambda s: s["t"])
        mergeable = pr.get("mergeable")
        return {
            "n": pr["number"],
            "title": pr["title"],
            "created": (pr.get("created_at") or "")[:10],
            "author": (pr.get("user") or {}).get("login"),
            "draft": bool(pr.get("draft")),
            "base": (pr.get("base") or {}).get("ref"),
            "merge": "CLEAN" if mergeable else ("DIRTY" if mergeable is False else "UNKNOWN"),
            "decision": decision,
            "req": [u["login"] for u in pr.get("requested_reviewers") or [] if u],
            "reviews": reviews,
            "unresolved": 0,
            "threads": sum(r.get("comments_count") or 0 for r in revs),
            "closes": [],
            "last": spoke[-1] if spoke else None,
            "url": pr.get("html_url") or "%s/%s/pulls/%s" % (self.base, repo, pr["number"]),
        }

    def issues(self, repo):
        issues = self._get("/repos/%s/issues" % repo, state="open", type="issues", limit=100)
        rows = []
        for issue in issues:
            rows.append({
                "n": issue["number"],
                "title": issue["title"],
                "created": (issue.get("created_at") or "")[:10],
                "author": (issue.get("user") or {}).get("login"),
                "labels": [label["name"] for label in issue.get("labels") or []],
                "assignees": [a["login"] for a in issue.get("assignees") or []],
                "comments": issue.get("comments") or 0,
                "url": issue.get("html_url") or "%s/%s/issues/%s" % (self.base, repo, issue["number"]),
            })
        rows.sort(key=lambda r: r["created"], reverse=True)
        return rows
