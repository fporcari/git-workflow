"""gw — one command over GitHub and Forgejo for the git-workflow skills.

The service is read from the checkout's `origin` (providers/detect.py); the
skills never name one. Every verb prints JSON in the shape documented in
providers/base.py, identical on both services, so a skill written once runs
on any repository the user works in.

    gw [--repo [host/]owner/repo] [--provider github|forgejo|fixture] <verb>

    gw whoami
    gw repo info                 host, provider, owner/repo, default branch
    gw repo default-branch
    gw pr list [--state open|closed|all] [--mine]
    gw pr view <n>
    gw pr reviews <n>
    gw pr diff <n> [--name-only]
    gw issue list
    gw issue view <n>
    gw api <endpoint> [-X METHOD] [-f key=value]...

Exit codes: 0 ok · 1 the service refused or the item does not exist ·
2 the checkout's host is not recognized (never a silent empty answer).
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from providers import detect, get_provider  # noqa: E402
from providers.base import diff_paths  # noqa: E402


def _out(data):
    sys.stdout.write(json.dumps(data, indent=1, ensure_ascii=False) + "\n")


def cmd_whoami(p, repo, args):
    _out({"login": p.whoami()})


def cmd_repo_info(p, repo, args):
    _out({"repo": repo, "provider": p.name, "host": args.host,
          "default_branch": p.default_branch(repo)})


def cmd_repo_default_branch(p, repo, args):
    _out({"default_branch": p.default_branch(repo)})


def cmd_pr_list(p, repo, args):
    rows = p.pulls(repo, state=args.state)
    if args.mine:
        me = p.whoami()
        rows = [r for r in rows if r["author"] == me]
    _out(rows)


def cmd_pr_view(p, repo, args):
    _out(p.pr_detail(repo, args.n))


def cmd_pr_reviews(p, repo, args):
    _out(p.pr_reviews(repo, args.n))


def cmd_pr_diff(p, repo, args):
    diff = p.pr_diff(repo, args.n)
    if args.name_only:
        _out(diff_paths(diff))
    else:
        sys.stdout.write(diff if diff.endswith("\n") else diff + "\n")


def cmd_issue_list(p, repo, args):
    _out(p.issues(repo)["rows"])


def cmd_issue_view(p, repo, args):
    _out(p.issue_detail(repo, args.n))


def cmd_api(p, repo, args):
    fields = dict(item.split("=", 1) for item in args.field or [])
    endpoint = args.endpoint.replace("{repo}", repo)
    _out(p.api(endpoint, method=args.method, fields=fields or None))


def build_parser():
    parser = argparse.ArgumentParser(prog="gw", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", "-R", help="[host/]owner/repo (default: the origin of the cwd)")
    parser.add_argument("--provider", choices=("github", "forgejo", "fixture"),
                        help="force the service (tests use fixture)")
    sub = parser.add_subparsers(dest="verb", required=True)

    sub.add_parser("whoami").set_defaults(run=cmd_whoami)

    repo = sub.add_parser("repo").add_subparsers(dest="sub", required=True)
    repo.add_parser("info").set_defaults(run=cmd_repo_info)
    repo.add_parser("default-branch").set_defaults(run=cmd_repo_default_branch)

    pr = sub.add_parser("pr").add_subparsers(dest="sub", required=True)
    lst = pr.add_parser("list")
    lst.add_argument("--state", default="open", choices=("open", "closed", "all"))
    lst.add_argument("--mine", action="store_true", help="only the authenticated user's")
    lst.set_defaults(run=cmd_pr_list)
    view = pr.add_parser("view")
    view.add_argument("n", type=int)
    view.set_defaults(run=cmd_pr_view)
    reviews = pr.add_parser("reviews")
    reviews.add_argument("n", type=int)
    reviews.set_defaults(run=cmd_pr_reviews)
    diff = pr.add_parser("diff")
    diff.add_argument("n", type=int)
    diff.add_argument("--name-only", action="store_true")
    diff.set_defaults(run=cmd_pr_diff)

    issue = sub.add_parser("issue").add_subparsers(dest="sub", required=True)
    issue.add_parser("list").set_defaults(run=cmd_issue_list)
    iview = issue.add_parser("view")
    iview.add_argument("n", type=int)
    iview.set_defaults(run=cmd_issue_view)

    api = sub.add_parser("api", help="raw REST call; {repo} expands to owner/repo")
    api.add_argument("endpoint")
    api.add_argument("-X", "--method", default="GET")
    api.add_argument("-f", "--field", action="append", metavar="KEY=VALUE")
    api.set_defaults(run=cmd_api)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        name, repo = detect.resolve(args.repo, args.provider)
        args.host = detect.parse_remote(detect.origin_url())[0] \
            if not args.repo or args.repo.count("/") != 2 else args.repo.split("/", 1)[0]
    except SystemExit as exc:
        sys.stderr.write("gw: %s\n" % exc)
        return 2
    try:
        provider = get_provider(name, host=args.host if name == "forgejo" else None)
        args.run(provider, repo, args)
    except (RuntimeError, SystemExit, KeyError) as exc:
        sys.stderr.write("gw: %s\n" % (exc if str(exc) else type(exc).__name__))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
