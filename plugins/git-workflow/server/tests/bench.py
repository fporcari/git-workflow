"""Where the desk's time goes — the measurement behind the design.

    python3 tests/bench.py [owner/repo]          # live, against the provider
    python3 tests/bench.py --http                # the served endpoints
    python3 tests/bench.py --queries owner/repo  # per-GraphQL-field costs

Run it before and after touching the provider or the cache; the numbers in
providers/github.py's docstring come from here.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cache          # noqa: E402
from providers import get_provider  # noqa: E402

CORE_FIELDS = """number title createdAt isDraft baseRefName reviewDecision author{login}
 reviewRequests(first:8){nodes{requestedReviewer{...on User{login}}}}
 reviews(last:8){nodes{author{login} state submittedAt}}
 comments(last:1){nodes{author{login} createdAt}}
 reviewThreads(first:10){nodes{isResolved comments(last:1){nodes{author{login} createdAt}}}}
 closingIssuesReferences(first:8){nodes{number assignees(first:4){nodes{login}}}}"""


def timed(label, fn):
    t0 = time.time()
    try:
        result = fn()
        note = len(result) if hasattr(result, "__len__") else result
    except Exception as exc:
        result, note = None, "ERROR %s" % str(exc)[:80]
    print("  %-34s %7.2fs  %s" % (label, time.time() - t0, note))
    return result


def bench_provider(repo, me):
    provider = get_provider("github")
    print("provider, cold (cache cleared)")
    cache.clear(repo)
    me = me or timed("whoami", provider.whoami)
    timed("queue  (1 search, involves:)", lambda: provider.queue(repo, me)["rows"])
    timed("mergestates (author:)", lambda: provider.mergestates(repo, me))
    timed("issues (1 repository query)", lambda: provider.issues(repo))

    print("\nthrough the cache")
    loader = lambda: provider.queue(repo, me)          # noqa: E731
    timed("first get (miss)", lambda: cache.get(repo, "bench", loader)[2])
    timed("second get (hit)", lambda: cache.get(repo, "bench", loader)[2])
    timed("forced refresh", lambda: cache.get(repo, "bench", loader, refresh=True)[2])


def gh_graphql(doc, **variables):
    args = ["gh", "api", "graphql", "-f", "query=%s" % doc]
    for key, value in variables.items():
        args += ["-f", "%s=%s" % (key, value)]
    out = subprocess.run(args, capture_output=True, text=True)
    if out.returncode:
        raise RuntimeError(out.stderr.strip()[:200])
    return json.loads(out.stdout)["data"]


def bench_queries(repo, me):
    """Which part of the query costs what. The answer that shaped the
    provider: the search is cheap, resolving nodes is not, and
    mergeStateStatus is the single most expensive field."""
    base = "repo:%s is:open is:pr" % repo
    search = lambda body: (          # noqa: E731
        "query($q:String!){search(type:ISSUE,first:100,query:$q){%s}}" % body)

    print("count only (no nodes resolved)")
    for rel in ("author", "involves", "assignee", "review-requested",
                "reviewed-by", "commenter", "mentions"):
        timed(rel, lambda rel=rel: gh_graphql(
            search("issueCount"), q="%s %s:%s" % (base, rel, me))
            ["search"]["issueCount"])

    print("\nnodes resolved, no mergeStateStatus")
    for rel in ("author", "involves", "reviewed-by"):
        timed(rel, lambda rel=rel: gh_graphql(
            search("nodes{...on PullRequest{%s}}" % CORE_FIELDS),
            q="%s %s:%s" % (base, rel, me))["search"]["nodes"])

    print("\nmergeStateStatus alone (nothing else)")
    for rel in ("author", "involves"):
        timed(rel, lambda rel=rel: gh_graphql(
            search("nodes{...on PullRequest{number mergeStateStatus}}"),
            q="%s %s:%s" % (base, rel, me))["search"]["nodes"])

    print("\neverything at once, one document (aliases resolve serially)")
    timed("mine+involves aliased", lambda: gh_graphql(
        "query($a:String!,$b:String!){"
        "a:search(type:ISSUE,first:100,query:$a){nodes{...on PullRequest{%s mergeStateStatus}}}"
        "b:search(type:ISSUE,first:100,query:$b){nodes{...on PullRequest{%s}}}}"
        % (CORE_FIELDS, CORE_FIELDS),
        a="%s author:%s" % (base, me), b="%s involves:%s" % (base, me)))


def http_get(url, etag=None):
    req = Request(url)
    if etag:
        req.add_header("If-None-Match", etag)
    with urlopen(req, timeout=120) as resp:
        return resp.status, resp.headers.get("ETag"), resp.read()


def bench_http(port):
    root = "http://127.0.0.1:%s" % port
    print("served endpoints (start the desk first, e.g. --provider fixture)")
    timed("GET /", lambda: len(http_get(root + "/")[2]))
    tag = [None]

    def desk():
        status, etag, body = http_get(root + "/api/desk")
        tag[0] = etag
        return "%s %d bytes" % (status, len(body))

    timed("GET /api/desk (first)", desk)
    timed("GET /api/desk (again)", desk)
    timed("GET /api/desk (If-None-Match)",
          lambda: "%s" % http_get(root + "/api/desk", tag[0])[0])
    timed("GET /api/state", lambda: http_get(root + "/api/state")[0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", nargs="?")
    parser.add_argument("--me")
    parser.add_argument("--http", action="store_true")
    parser.add_argument("--port", type=int, default=8399)
    parser.add_argument("--queries", action="store_true")
    args = parser.parse_args()

    if args.http:
        bench_http(args.port)
        return
    repo = args.repo or "genropy/genropy"
    me = args.me or os.environ.get("DESK_ME") or get_provider("github").whoami()
    if args.queries:
        bench_queries(repo, me)
    else:
        bench_provider(repo, me)


if __name__ == "__main__":
    main()
