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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import gate as gatelib

from .base import (Provider, REVIEW_STATES, brief_row, detail_row, issue_row, logins, review_row,
                   verification_result)

GQL = Path(__file__).resolve().parents[1] / "gql"


def _gh(*args, timeout=90, stdin=None):
    try:
        out = subprocess.run(("gh",) + args, capture_output=True, text=True, timeout=timeout,
                             input=stdin)
    except subprocess.TimeoutExpired:
        raise RuntimeError("gh %s timed out after %ss" % (args[0], timeout))
    except FileNotFoundError:
        raise RuntimeError("gh is not installed or not in PATH")
    if out.returncode:
        raise RuntimeError("gh %s failed: %s" % (args[0], out.stderr.strip()[:400]))
    return out.stdout


SUMMARY_CHARS = 420


def _summary(body):
    """The opening of the PR's own description, trimmed.

    This is the cheap answer to "what is this PR FOR": the author already
    wrote it. Asking a model to paraphrase 52 titles was the expensive way to
    learn something the payload could carry — `bodyText` costs nothing
    measurable on the search (within run-to-run noise), and trimming it here
    keeps the 211 KB of full descriptions off the wire.
    """
    text = " ".join((body or "").split())
    if not text:
        return None
    if len(text) <= SUMMARY_CHARS:
        return text
    cut = text[:SUMMARY_CHARS]
    stop = max(cut.rfind(". "), cut.rfind("; "))
    return (cut[:stop + 1] if stop > SUMMARY_CHARS // 2 else cut.rstrip()) + " …"


def _graphql(doc, timeout=90, **variables):
    args = ["api", "graphql", "-F", "query=@%s" % (GQL / doc)]
    for key, value in variables.items():
        args += ["-F" if isinstance(value, int) else "-f",
                 "%s=%s" % (key, value)]
    return json.loads(_gh(*args, timeout=timeout))["data"]


class GitHubProvider(Provider):
    name = "github"

    @classmethod
    def hosts(cls):
        return ["github.com"]

    def whoami(self):
        return _gh("api", "user", "--jq", ".login").strip()

    def queue(self, repo, me):
        q = "repo:%s is:open is:pr involves:%s" % (repo, me)
        search = _graphql("pr_core.graphql", q=q)["search"]
        nodes = [node for node in search["nodes"]
                 if node and node.get("state", "OPEN") == "OPEN"]
        rows = [self._row(repo, node) for node in nodes]
        rows.sort(key=lambda r: r["created"], reverse=True)
        total = search["issueCount"] - (len(search["nodes"]) - len(nodes))
        return {"rows": rows, "total": max(len(rows), total),
                "truncated": search["pageInfo"]["hasNextPage"]}

    def open_numbers(self, repo, me):
        q = "repo:%s is:open is:pr involves:%s" % (repo, me)
        nodes = _graphql("pr_membership.graphql", q=q)["search"]["nodes"]
        return [node["number"] for node in nodes
                if node and node.get("state") == "OPEN"]

    def mergestates(self, repo, me):
        """Phase two: the expensive field, for the user's own PRs only."""
        q = "repo:%s is:open is:pr author:%s" % (repo, me)
        nodes = _graphql("pr_mergestate.graphql", q=q)["search"]["nodes"]
        return {str(n["number"]): n["mergeStateStatus"] for n in nodes if n}

    def analysis_probe(self, repo, n):
        owner, name = repo.split("/", 1)
        pr = _graphql("pr_probe.graphql", timeout=10, owner=owner, name=name,
                      number=n)["repository"]["pullRequest"]
        if not pr:
            return None
        checks = (((pr.get("commits") or {}).get("nodes") or [{}])[0]
                  .get("commit") or {}).get("statusCheckRollup") or {}
        contexts = (checks.get("contexts") or {}).get("nodes") or []
        items = []
        for item in contexts:
            items.append({
                "name": item.get("name") or item.get("context"),
                "status": item.get("status") or item.get("state"),
                "conclusion": item.get("conclusion"),
            })
        threads = pr.get("reviewThreads") or {}
        return {
            "fresh": True,
            "head": pr.get("headRefOid"),
            "base_head": pr.get("baseRefOid"),
            "merge": pr.get("mergeStateStatus"),
            "decision": pr.get("reviewDecision"),
            "labels": [label["name"] for label in (pr.get("labels") or {}).get("nodes") or []],
            "requests": [
                ((node.get("requestedReviewer") or {}).get("login") or
                 (node.get("requestedReviewer") or {}).get("slug"))
                for node in (pr.get("reviewRequests") or {}).get("nodes") or []
                if node.get("requestedReviewer")],
            "reviews": [{
                "who": (item.get("author") or {}).get("login"),
                "state": item.get("state"),
                "submitted": item.get("submittedAt"),
                "commit": (item.get("commit") or {}).get("oid"),
                "verification": verification_result(item.get("bodyText")),
                "has_text": bool((item.get("bodyText") or "").strip()),
            } for item in (pr.get("reviews") or {}).get("nodes") or []],
            "threads": threads.get("totalCount", 0),
            "unresolved": sum(not item.get("isResolved")
                              for item in threads.get("nodes") or []),
            "incomplete": any((
                (pr.get("labels") or {}).get("pageInfo", {}).get("hasNextPage"),
                (pr.get("reviewRequests") or {}).get("pageInfo", {}).get("hasNextPage"),
                (pr.get("reviews") or {}).get("pageInfo", {}).get("hasPreviousPage"),
                threads.get("pageInfo", {}).get("hasNextPage"),
                (checks.get("contexts") or {}).get("pageInfo", {}).get("hasNextPage"),
            )),
            "checks": {"state": checks.get("state"), "items": items},
        }

    def _row(self, repo, node):
        spoke = []
        for c in node["comments"]["nodes"]:
            spoke.append({"t": c["createdAt"], "who": c["author"]["login"], "ch": "comment"})
        reviews = []
        for r in node["reviews"]["nodes"]:
            who = (r.get("author") or {}).get("login")
            reviews.append({"who": who, "state": r["state"],
                            "on": r["submittedAt"][:10],
                            "commit": (r.get("commit") or {}).get("oid"),
                            "verification": verification_result(r.get("bodyText")),
                            "has_text": bool((r.get("bodyText") or "").strip())})
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
            "labels": [label["name"] for label in (node.get("labels") or {}).get("nodes") or []],
            "assignees": [a["login"] for a in node["assignees"]["nodes"]],
            "draft": node["isDraft"],
            "base": node["baseRefName"],
            "base_head": node.get("baseRefOid"),
            "head": node.get("headRefOid"),
            "incomplete": any((
                (node.get("labels") or {}).get("pageInfo", {}).get("hasNextPage"),
                node["assignees"]["pageInfo"]["hasNextPage"],
                node["reviewRequests"]["pageInfo"]["hasNextPage"],
                node["reviews"]["pageInfo"]["hasPreviousPage"],
                node["reviewThreads"]["pageInfo"]["hasNextPage"],
                node["closingIssuesReferences"]["pageInfo"]["hasNextPage"],
            )),
            "merge": None,
            "decision": node["reviewDecision"],
            "req": [r["requestedReviewer"]["login"]
                    for r in node["reviewRequests"]["nodes"]
                    if r.get("requestedReviewer") and r["requestedReviewer"].get("login")],
            "reviews": reviews,
            "unresolved": unresolved,
            "threads": len(node["reviewThreads"]["nodes"]),
            "closes": [{"issue": c["number"], "title": c.get("title"),
                        "assignees": [a["login"] for a in c["assignees"]["nodes"]]}
                       for c in node["closingIssuesReferences"]["nodes"]],
            "summary": _summary(node.get("bodyText")),
            "last": spoke[-1] if spoke else None,
            "url": "https://github.com/%s/pull/%s" % (repo, node["number"]),
        }

    def merge_command(self, repo, n):
        return "gh pr merge %s --repo %s --squash --delete-branch" % (n, repo)

    def default_branch(self, repo):
        return _gh("repo", "view", repo, "--json", "defaultBranchRef",
                   "--jq", ".defaultBranchRef.name").strip() or "main"

    def gates(self, repo, me, bases):
        return gatelib.read_all(repo, me, bases)

    ISSUE_REL = ("query($q:String!){search(type:ISSUE,first:100,query:$q){"
                 "pageInfo{hasNextPage} nodes{...on Issue{number}}}}")

    def _issue_numbers(self, repo, qualifier):
        raw = _gh("api", "graphql", "-f", "query=%s" % self.ISSUE_REL,
                  "-f", "q=repo:%s is:issue is:open %s" % (repo, qualifier))
        search = json.loads(raw)["data"]["search"]
        return ([n["number"] for n in search["nodes"] if n],
                not search["pageInfo"]["hasNextPage"])

    def issue_relations(self, repo, me):
        with ThreadPoolExecutor(max_workers=2) as pool:
            seen = pool.submit(self._issue_numbers, repo, "commenter:%s" % me)
            mine = pool.submit(self._issue_numbers, repo, "assignee:%s" % me)
            commented, complete = seen.result()
            assigned, _ = mine.result()
        return {"commented": commented, "assigned": assigned, "complete": complete}

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
                "updated": issue["updatedAt"],
                "author": (issue.get("author") or {}).get("login") or "ghost",
                "labels": [label["name"] for label in issue["labels"]["nodes"]],
                "assignees": [a["login"] for a in issue["assignees"]["nodes"]],
                "comments": issue["comments"]["totalCount"],
                "url": issue["url"],
            })
        rows.sort(key=lambda r: r["created"], reverse=True)
        return {"rows": rows, "total": total, "truncated": more}

    # ---- per-item reads (gw) -------------------------------------------
    #
    # REST for the item itself: `requested_reviewers`, `labels`,
    # `mergeable_state` and the reviews' `commit_id` are all there, and the
    # same endpoints exist on Forgejo with the same names. GraphQL only for
    # what REST does not have — the linked issues.

    CLOSES_GQL = ("query($owner:String!,$name:String!,$number:Int!,$endCursor:String){"
                  "repository(owner:$owner,name:$name){pullRequest(number:$number){"
                  "closingIssuesReferences(first:100,after:$endCursor){"
                  "pageInfo{hasNextPage endCursor} nodes{number}}}}}")

    def _rest(self, endpoint, method="GET", fields=None, paginate=False, body=None):
        args = ["api", endpoint, "-X", method]
        for key, value in (fields or {}).items():
            args += ["-f", "%s=%s" % (key, value)]
        if paginate:
            args += ["--paginate", "--slurp"]
        if body is not None:
            args += ["--input", "-"]
        out = _gh(*args, **({"stdin": json.dumps(body)} if body is not None else {}))
        data = json.loads(out) if out.strip() else None
        return [item for page in data or [] for item in page] if paginate else data

    def api(self, endpoint, method="GET", fields=None):
        return self._rest(endpoint, method, fields)

    @staticmethod
    def _merge(pr):
        if pr.get("merged"):
            return "MERGED"
        state = (pr.get("mergeable_state") or "unknown").upper()
        return state if state in ("CLEAN", "DIRTY", "BLOCKED", "UNSTABLE", "BEHIND") else "UNKNOWN"

    def pulls(self, repo, state="open"):
        pulls = self._rest("repos/%s/pulls?state=%s&per_page=100&sort=created&direction=desc"
                           % (repo, state), paginate=True)
        return [brief_row(pr) for pr in pulls]

    def pr_reviews(self, repo, n):
        reviews = self._rest("repos/%s/pulls/%s/reviews?per_page=100" % (repo, n), paginate=True)
        return [review_row(r, r.get("state", "")) for r in reviews if r.get("state") in REVIEW_STATES]

    def _closes(self, repo, n):
        owner, name = repo.split("/", 1)
        raw = _gh("api", "graphql", "-f", "query=%s" % self.CLOSES_GQL,
                  "-f", "owner=%s" % owner, "-f", "name=%s" % name, "-F", "number=%s" % n,
                  "--paginate", "--slurp")
        return [{"issue": node["number"], "source": "provider"}
                for page in json.loads(raw)
                for node in page["data"]["repository"]["pullRequest"]["closingIssuesReferences"]["nodes"]]

    def pr_detail(self, repo, n):
        pr = self._rest("repos/%s/pulls/%s" % (repo, n))
        return detail_row(pr, self.pr_reviews(repo, n), self._merge(pr), self._closes(repo, n))

    def pr_diff(self, repo, n):
        return _gh("pr", "diff", str(n), "--repo", repo)

    def issue_detail(self, repo, n):
        return issue_row(self._rest("repos/%s/issues/%s" % (repo, n)),
                         self._rest("repos/%s/issues/%s/comments?per_page=100" % (repo, n), paginate=True))

    # ---- writes (gw) ---------------------------------------------------

    def issue_create(self, repo, title, body):
        issue = self._rest("repos/%s/issues" % repo, "POST", body={"title": title, "body": body})
        return {"n": issue["number"], "url": issue["html_url"]}

    def pr_create(self, repo, title, body, head, base, draft=False):
        pr = self._rest("repos/%s/pulls" % repo, "POST",
                        body={"title": title, "body": body, "head": head, "base": base, "draft": draft})
        return {"n": pr["number"], "url": pr["html_url"]}

    def add_assignees(self, repo, n, who, pull=False):
        self._rest("repos/%s/issues/%s/assignees" % (repo, n), "POST", body={"assignees": list(who)})

    def add_labels(self, repo, n, names):
        self._rest("repos/%s/issues/%s/labels" % (repo, n), "POST", body={"labels": list(names)})

    def add_reviewers(self, repo, n, who):
        self._rest("repos/%s/pulls/%s/requested_reviewers" % (repo, n), "POST",
                   body={"reviewers": list(who)})

    def comment(self, repo, n, body):
        made = self._rest("repos/%s/issues/%s/comments" % (repo, n), "POST", body={"body": body})
        return {"url": made["html_url"]}

    def label_ensure(self, repo, name, color, description):
        try:
            self._rest("repos/%s/labels/%s" % (repo, quote(name, safe="")))
        except RuntimeError:
            self._rest("repos/%s/labels" % repo, "POST",
                       body={"name": name, "color": color.lstrip("#"), "description": description})

    def collaborators(self, repo):
        return logins(self._rest("repos/%s/collaborators?per_page=100" % repo, paginate=True))
