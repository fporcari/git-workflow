"""GitHub provider — shells out to the authenticated `gh` CLI.

Reuses the exact GraphQL document of the pr-triage skill
(skills/pr-triage/queue.graphql) and ports its queue.jq transform to Python,
so the dashboard and the skill read the very same fields.
"""

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .base import Provider

QUERY_FILE = Path(__file__).resolve().parents[2] / "skills" / "pr-triage" / "queue.graphql"
RELATIONSHIPS = ("author", "review-requested", "reviewed-by", "assignee")


def _gh(*args, timeout=60):
    out = subprocess.run(("gh",) + args, capture_output=True, text=True, timeout=timeout)
    if out.returncode:
        raise RuntimeError("gh %s failed: %s" % (args[0], out.stderr.strip()[:400]))
    return out.stdout


class GitHubProvider(Provider):
    name = "github"

    def whoami(self):
        return _gh("api", "user", "--jq", ".login").strip()

    def _search(self, repo, rel, login):
        q = "repo:%s is:open is:pr %s:%s" % (repo, rel, login)
        raw = _gh("api", "graphql", "-F", "query=@%s" % QUERY_FILE, "-f", "q=%s" % q)
        return json.loads(raw)["data"]["search"]["nodes"]

    def queue(self, repo, me):
        with ThreadPoolExecutor(max_workers=4) as pool:
            batches = pool.map(lambda rel: self._search(repo, rel, me), RELATIONSHIPS)
        seen = {}
        for nodes in batches:
            for node in nodes:
                if node and node.get("number") not in seen:
                    seen[node["number"]] = node
        rows = [self._row(repo, node) for node in seen.values()]
        rows.sort(key=lambda r: r["created"], reverse=True)
        return rows

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
            "author": node["author"]["login"],
            "draft": node["isDraft"],
            "base": node["baseRefName"],
            "merge": node["mergeStateStatus"],
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

    def issues(self, repo):
        raw = _gh("issue", "list", "--repo", repo, "--state", "open", "--limit", "100",
                  "--json", "number,title,labels,url,author,assignees,createdAt,comments")
        rows = []
        for issue in json.loads(raw):
            rows.append({
                "n": issue["number"],
                "title": issue["title"],
                "created": issue["createdAt"][:10],
                "author": issue["author"]["login"],
                "labels": [label["name"] for label in issue["labels"]],
                "assignees": [a["login"] for a in issue["assignees"]],
                "comments": len(issue["comments"]),
                "url": issue["url"],
            })
        rows.sort(key=lambda r: r["created"], reverse=True)
        return rows
