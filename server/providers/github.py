"""GitHub provider — shells out to the authenticated `gh` CLI.

Two measured facts shape this file (reproduce them with tests/bench.py):

1. The search itself is cheap (`issueCount` alone answers in 0.7s). What
   costs is resolving the NODES — the nested reviews/threads/closes
   connections, per PR. So the way to be fast is to resolve each PR once.
   `involves:<me>` is a superset of author, assignee, commenter, mentions,
   review-requested and reviewed-by (verified: the union of all six is
   exactly the involves set), so ONE search replaces the four the desk used
   to run, and no PR is resolved twice.

2. `mergeStateStatus` is the single expensive field: GitHub computes a test
   merge per PR, and asking for it costs more than the whole rest of the
   query (5.4s for 35 PRs vs 4.3s for 51 PRs without it). Widening the
   parallelism does not help — eight concurrent searches on one token
   measure SLOWER than four (7.8s vs 4.9s), and aliases inside one document
   resolve serially (7.8s).

Hence the two-phase read: `queue()` returns the rows without merge state,
and `mergestates()` fills it in a second call the desk runs behind the
browser. The verdict engine only reads `merge` for the user's own PRs, so
that is the only search phase two needs.
"""

import json
import subprocess
from pathlib import Path

from .base import Provider

GQL = Path(__file__).resolve().parents[1] / "gql"


def _gh(*args, timeout=90):
    out = subprocess.run(("gh",) + args, capture_output=True, text=True, timeout=timeout)
    if out.returncode:
        raise RuntimeError("gh %s failed: %s" % (args[0], out.stderr.strip()[:400]))
    return out.stdout


def _graphql(doc, **variables):
    args = ["api", "graphql", "-F", "query=@%s" % (GQL / doc)]
    for key, value in variables.items():
        args += ["-f", "%s=%s" % (key, value)]
    return json.loads(_gh(*args))["data"]


class GitHubProvider(Provider):
    name = "github"

    def whoami(self):
        return _gh("api", "user", "--jq", ".login").strip()

    def queue(self, repo, me):
        q = "repo:%s is:open is:pr involves:%s" % (repo, me)
        search = _graphql("pr_core.graphql", q=q)["search"]
        rows = [self._row(repo, node) for node in search["nodes"] if node]
        rows.sort(key=lambda r: r["created"], reverse=True)
        return {"rows": rows, "total": search["issueCount"],
                "truncated": search["pageInfo"]["hasNextPage"]}

    def mergestates(self, repo, me):
        """Phase two: the expensive field, for the user's own PRs only."""
        q = "repo:%s is:open is:pr author:%s" % (repo, me)
        nodes = _graphql("pr_mergestate.graphql", q=q)["search"]["nodes"]
        return {str(n["number"]): n["mergeStateStatus"] for n in nodes if n}

    def _row(self, repo, node):
        spoke = []
        for c in node["comments"]["nodes"]:
            spoke.append({"t": c["createdAt"], "who": c["author"]["login"], "ch": "comment"})
        reviews = []
        for r in node["reviews"]["nodes"]:
            who = (r.get("author") or {}).get("login")
            reviews.append({"who": who, "state": r["state"], "on": r["submittedAt"][:10]})
            spoke.append({"t": r["submittedAt"], "who": who, "ch": r["state"].lower()})
        unresolved = 0
        for th in node["reviewThreads"]["nodes"]:
            if not th["isResolved"]:
                unresolved += 1
            for c in th["comments"]["nodes"]:
                spoke.append({"t": c["createdAt"], "who": c["author"]["login"], "ch": "inline"})
        spoke = sorted((s for s in spoke if s["t"]), key=lambda s: s["t"])
        return {
            "n": node["number"],
            "title": node["title"],
            "created": node["createdAt"][:10],
            "author": (node.get("author") or {}).get("login") or "ghost",
            "draft": node["isDraft"],
            "base": node["baseRefName"],
            "merge": None,
            "decision": node["reviewDecision"],
            "req": [r["requestedReviewer"]["login"]
                    for r in node["reviewRequests"]["nodes"]
                    if r.get("requestedReviewer") and r["requestedReviewer"].get("login")],
            "reviews": reviews,
            "unresolved": unresolved,
            "threads": len(node["reviewThreads"]["nodes"]),
            "closes": [{"issue": c["number"],
                        "assignees": [a["login"] for a in c["assignees"]["nodes"]]}
                       for c in node["closingIssuesReferences"]["nodes"]],
            "last": spoke[-1] if spoke else None,
            "url": "https://github.com/%s/pull/%s" % (repo, node["number"]),
        }

    def merge_command(self, repo, n):
        return "gh pr merge %s --repo %s --squash --delete-branch" % (n, repo)

    ISSUE_PAGES = 4          # 400 issues; beyond that the desk says so

    def issues(self, repo):
        owner, name = repo.split("/", 1)
        nodes, cursor, total, more = [], None, 0, False
        for _ in range(self.ISSUE_PAGES):
            args = {"o": owner, "r": name}
            if cursor:
                args["after"] = cursor
            page = _graphql("issues.graphql", **args)["repository"]["issues"]
            nodes += [n for n in page["nodes"] if n]
            total = page["totalCount"]
            more = page["pageInfo"]["hasNextPage"]
            cursor = page["pageInfo"]["endCursor"]
            if not more:
                break
        rows = []
        for issue in nodes:
            rows.append({
                "n": issue["number"],
                "title": issue["title"],
                "created": issue["createdAt"][:10],
                "author": (issue.get("author") or {}).get("login") or "ghost",
                "labels": [label["name"] for label in issue["labels"]["nodes"]],
                "assignees": [a["login"] for a in issue["assignees"]["nodes"]],
                "comments": issue["comments"]["totalCount"],
                "url": issue["url"],
            })
        rows.sort(key=lambda r: r["created"], reverse=True)
        return {"rows": rows, "total": total, "truncated": more}
