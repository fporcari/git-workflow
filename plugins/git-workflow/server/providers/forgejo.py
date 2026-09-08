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
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from .base import Provider, REVIEW_STATES, closes_from_body, decision_from, verification_result

STATE_MAP = {"REQUEST_CHANGES": "CHANGES_REQUESTED", "COMMENT": "COMMENTED"}
PAGE = 50


def _review_state(review):
    if review.get("dismissed"):
        return "DISMISSED"
    state = review.get("state", "")
    return STATE_MAP.get(state, state)


class ForgejoProvider(Provider):
    name = "forgejo"

    @classmethod
    def hosts(cls):
        host = (urlparse(os.environ.get("FORGEJO_URL", "")).hostname or "").lower()
        return [host] if host else []

    def __init__(self, host=None):
        super().__init__(host)
        url = os.environ.get("FORGEJO_URL", "")
        if host and host not in self.hosts():
            url = "https://%s" % host
        self.base = url.rstrip("/")
        self.token = os.environ.get("FORGEJO_TOKEN", "")
        if not self.base or not self.token:
            raise SystemExit("forgejo provider needs FORGEJO_URL and FORGEJO_TOKEN in the environment")

    def _request(self, path, method="GET", params=None, fields=None, accept="application/json"):
        url = "%s/api/v1%s" % (self.base, path)
        if method == "GET" and fields:
            params = dict(params or {}, **fields)
            fields = None
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        data = json.dumps(fields).encode() if fields else None
        headers = {"Authorization": "token %s" % self.token, "Accept": accept}
        if data:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:400]
            raise RuntimeError("forgejo %s %s failed: HTTP %s %s" % (method, path, exc.code, detail))

    def _get(self, path, **params):
        return json.loads(self._request(path, params=params) or "null")

    def _get_all(self, path, **params):
        rows, page = [], 1
        while True:
            batch = self._get(path, **dict(params, page=page, limit=PAGE)) or []
            rows.extend(batch)
            if len(batch) < PAGE:
                return rows
            page += 1

    def _get_text(self, path):
        return self._request(path, accept="text/plain")

    def whoami(self):
        return self._get("/user")["login"]

    def default_branch(self, repo):
        return self._get("/repos/%s" % repo).get("default_branch") or "main"

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
        return {"rows": rows, "total": len(rows), "truncated": len(pulls) >= 50}

    def open_numbers(self, repo, me):
        pulls = self._get("/repos/%s/pulls" % repo, state="open", limit=50)
        return [pr["number"] for pr in pulls]

    def _involves(self, row, me):
        return (row["author"] == me or me in row["req"]
                or any(r["who"] == me for r in row["reviews"]))

    def _row(self, repo, pr, revs):
        reviews, spoke = [], []
        for r in revs:
            state = _review_state(r)
            if state in ("APPROVED", "CHANGES_REQUESTED", "COMMENTED"):
                who = (r.get("user") or {}).get("login")
                on = (r.get("submitted_at") or "")[:10]
                reviews.append({"who": who, "state": state,
                                "on": on, "commit": r.get("commit_id"),
                                "verification": verification_result(r.get("body")),
                                "has_text": bool((r.get("body") or "").strip())})
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
            "labels": [label["name"] for label in pr.get("labels") or []],
            "assignees": [a["login"] for a in pr.get("assignees") or []],
            "draft": bool(pr.get("draft")),
            "base": (pr.get("base") or {}).get("ref"),
            "base_head": (pr.get("base") or {}).get("sha"),
            "head": (pr.get("head") or {}).get("sha"),
            "incomplete": False,
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

    def merge_command(self, repo, n):
        return ("curl -X POST -H 'Authorization: token $FORGEJO_TOKEN' "
                "%s/api/v1/repos/%s/pulls/%s/merge -d '{\"Do\":\"squash\",\"delete_branch_after_merge\":true}'"
                % (self.base, repo, n))

    def issues(self, repo):
        issues = self._get("/repos/%s/issues" % repo, state="open", type="issues", limit=100)
        rows = []
        for issue in issues:
            rows.append({
                "n": issue["number"],
                "title": issue["title"],
                "created": (issue.get("created_at") or "")[:10],
                "updated": issue.get("updated_at") or "",
                "author": (issue.get("user") or {}).get("login"),
                "labels": [label["name"] for label in issue.get("labels") or []],
                "assignees": [a["login"] for a in issue.get("assignees") or []],
                "comments": issue.get("comments") or 0,
                "url": issue.get("html_url") or "%s/%s/issues/%s" % (self.base, repo, issue["number"]),
            })
        rows.sort(key=lambda r: r["created"], reverse=True)
        return {"rows": rows, "total": len(rows), "truncated": len(issues) >= 100}

    # ---- per-item reads (gw) -------------------------------------------
    #
    # Forgejo's REST v1 mirrors GitHub's on pulls and issues: same endpoint
    # names, same field names for what matters here. What it lacks is the
    # linked-issue list, resolved at merge time from the body's keywords —
    # so `closes` is read from the body and says so.

    def api(self, endpoint, method="GET", fields=None):
        body = self._request("/" + endpoint.lstrip("/"), method=method, fields=fields)
        return json.loads(body) if body.strip() else None

    @staticmethod
    def _login(user):
        return (user or {}).get("login")

    @staticmethod
    def _merge(pr):
        if pr.get("merged"):
            return "MERGED"
        mergeable = pr.get("mergeable")
        return "CLEAN" if mergeable else ("DIRTY" if mergeable is False else "UNKNOWN")

    def _brief(self, pr):
        return {
            "n": pr["number"], "title": pr["title"],
            "author": self._login(pr.get("user")), "draft": bool(pr.get("draft")),
            "base": (pr.get("base") or {}).get("ref"),
            "head": (pr.get("head") or {}).get("ref"),
            "created": (pr.get("created_at") or "")[:10],
            "updated": pr.get("updated_at"),
            "labels": [label["name"] for label in pr.get("labels") or []],
            "assignees": [a["login"] for a in pr.get("assignees") or []],
            "req": [u["login"] for u in pr.get("requested_reviewers") or [] if u],
            "url": pr.get("html_url"),
        }

    def pulls(self, repo, state="open"):
        pulls = self._get_all("/repos/%s/pulls" % repo, state=state,
                          sort="recentupdate") or []
        pulls.sort(key=lambda pr: pr.get("created_at") or "", reverse=True)
        return [self._brief(pr) for pr in pulls]

    def pr_reviews(self, repo, n):
        out = []
        for review in self._get_all("/repos/%s/pulls/%s/reviews" % (repo, n)) or []:
            state = _review_state(review)
            if state not in REVIEW_STATES:
                continue
            out.append({"who": self._login(review.get("user")), "state": state,
                        "on": (review.get("submitted_at") or "")[:10],
                        "commit": review.get("commit_id"),
                        "has_text": bool((review.get("body") or "").strip())})
        out.sort(key=lambda r: r["on"])
        return out

    def pr_detail(self, repo, n):
        pr = self._get("/repos/%s/pulls/%s" % (repo, n))
        reviews = self.pr_reviews(repo, n)
        req = [u["login"] for u in pr.get("requested_reviewers") or [] if u]
        state = "merged" if pr.get("merged") else pr.get("state")
        return dict(self._brief(pr), **{
            "body": pr.get("body") or "", "state": state,
            "base": {"ref": pr["base"]["ref"], "sha": pr["base"]["sha"]},
            "head": {"ref": pr["head"]["ref"], "sha": pr["head"]["sha"]},
            "merge": self._merge(pr),
            "decision": decision_from(reviews, req),
            "reviews": reviews,
            "closes": closes_from_body(pr.get("body")),
            "comments": (pr.get("comments") or 0) + (pr.get("review_comments") or 0),
        })

    def pr_diff(self, repo, n):
        return self._get_text("/repos/%s/pulls/%s.diff" % (repo, n))

    def issue_detail(self, repo, n):
        issue = self._get("/repos/%s/issues/%s" % (repo, n))
        comments = self._get("/repos/%s/issues/%s/comments" % (repo, n)) or []
        return {
            "n": issue["number"], "title": issue["title"], "body": issue.get("body") or "",
            "state": issue.get("state"), "author": self._login(issue.get("user")),
            "assignees": [a["login"] for a in issue.get("assignees") or []],
            "labels": [label["name"] for label in issue.get("labels") or []],
            "created": issue.get("created_at"), "updated": issue.get("updated_at"),
            "url": issue.get("html_url"),
            "comments": [{"who": self._login(c.get("user")), "t": c.get("created_at"),
                          "body": c.get("body") or ""} for c in comments],
        }
