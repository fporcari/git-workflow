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
    gw collaborators             logins a review can be requested from
    gw api <endpoint> [-X METHOD] [-f key=value]...

    gw issue create --title T (--body-file F | --body B) [--label L]... [--assignee L]...
    gw issue edit <n> [--add-assignee L]... [--add-label L]...
    gw issue comment <n> (--body-file F | --body B)
    gw label ensure <name> [--color HEX] [--description D]
    gw pr create --title T (--body-file F | --body B) --head BRANCH [--base BRANCH]
                 [--draft] [--label L]... [--assignee L]... [--reviewer L]...
    gw pr edit <n> [--add-assignee L]... [--add-label L]... [--add-reviewer L]...
    gw pr comment <n> (--body-file F | --body B)

`@me` as a login is the authenticated user. A reviewer must be a collaborator
of the repo: the services drop a request to anybody else without an error.
`pr create` reads the PR back and fails when the body names an issue to close
that the service did not link. There is no verb that rewrites a PR body: the
description is the author's record of the change as opened.

Exit codes: 0 ok · 1 the service refused or the item does not exist ·
2 the checkout's host is not recognized (never a silent empty answer).
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from providers import PROVIDERS, detect, get_provider  # noqa: E402
from providers.base import closes_from_body, diff_paths  # noqa: E402


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
    result = p.issues(repo)
    if result.get("truncated"):
        raise RuntimeError("issue list is incomplete: the provider reached its page limit")
    _out(result["rows"])


def cmd_issue_view(p, repo, args):
    _out(p.issue_detail(repo, args.n))


def _body(args):
    if args.body_file == "-":
        return sys.stdin.read()
    if args.body_file:
        return Path(args.body_file).read_text()
    return args.body or ""


def _logins(p, names):
    return [p.whoami() if name == "@me" else name for name in names or []]


def _check_collaborators(p, repo, who):
    unknown = sorted(set(who) - set(p.collaborators(repo)))
    if unknown:
        raise RuntimeError("not collaborators of %s: %s (the service would drop the request silently)"
                           % (repo, ", ".join(unknown)))


def _apply(p, repo, n, args, pull, check=True):
    who = _logins(p, getattr(args, "assignee", None) or getattr(args, "add_assignee", None))
    if who:
        p.add_assignees(repo, n, who, pull=pull)
    labels = getattr(args, "label", None) or getattr(args, "add_label", None)
    if labels:
        p.add_labels(repo, n, labels)
    reviewers = _logins(p, getattr(args, "reviewer", None) or getattr(args, "add_reviewer", None))
    if reviewers:
        if check:
            _check_collaborators(p, repo, reviewers)
        p.add_reviewers(repo, n, reviewers)


def cmd_issue_create(p, repo, args):
    made = p.issue_create(repo, args.title, _body(args))
    _apply(p, repo, made["n"], args, pull=False)
    _out(made)


def cmd_issue_edit(p, repo, args):
    _apply(p, repo, args.n, args, pull=False)
    _out({"n": args.n})


def cmd_comment(p, repo, args):
    _out(p.comment(repo, args.n, _body(args)))


def cmd_label_ensure(p, repo, args):
    p.label_ensure(repo, args.name, args.color, args.description)
    _out({"label": args.name})


def cmd_pr_create(p, repo, args):
    body = _body(args)
    base = args.base or p.default_branch(repo)
    reviewers = _logins(p, args.reviewer)
    if reviewers:
        _check_collaborators(p, repo, reviewers)
    made = p.pr_create(repo, args.title, body, args.head, base, draft=args.draft)
    _apply(p, repo, made["n"], args, pull=True, check=False)
    detail = p.pr_detail(repo, made["n"])
    _out(dict(made, base=base, draft=args.draft, closes=detail["closes"], req=detail["req"]))
    wanted = [c["issue"] for c in closes_from_body(body)]
    if wanted and not detail["closes"]:
        raise RuntimeError("the body names #%s to close but the service linked no issue"
                           % ", #".join(str(n) for n in wanted))


def cmd_pr_edit(p, repo, args):
    _apply(p, repo, args.n, args, pull=True)
    _out({"n": args.n})


def cmd_collaborators(p, repo, args):
    _out(p.collaborators(repo))


def cmd_api(p, repo, args):
    fields = dict(item.split("=", 1) for item in args.field or [])
    endpoint = args.endpoint.replace("{repo}", repo)
    _out(p.api(endpoint, method=args.method, fields=fields or None))


def _body_args(parser):
    body = parser.add_mutually_exclusive_group(required=True)
    body.add_argument("--body-file", metavar="FILE", help="- reads stdin")
    body.add_argument("--body")


def build_parser():
    parser = argparse.ArgumentParser(prog="gw", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", "-R", help="[host/]owner/repo (default: the origin of the cwd)")
    parser.add_argument("--provider", choices=tuple(PROVIDERS),
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

    pcreate = pr.add_parser("create")
    pcreate.add_argument("--title", required=True)
    _body_args(pcreate)
    pcreate.add_argument("--head", required=True, help="the branch to merge")
    pcreate.add_argument("--base", help="default: the repo's default branch")
    pcreate.add_argument("--draft", action="store_true")
    pcreate.add_argument("--label", action="append", metavar="NAME")
    pcreate.add_argument("--assignee", action="append", metavar="LOGIN")
    pcreate.add_argument("--reviewer", action="append", metavar="LOGIN")
    pcreate.set_defaults(run=cmd_pr_create)
    pedit = pr.add_parser("edit", help="add assignees, labels, reviewers; never the body")
    pedit.add_argument("n", type=int)
    pedit.add_argument("--add-assignee", action="append", metavar="LOGIN")
    pedit.add_argument("--add-label", action="append", metavar="NAME")
    pedit.add_argument("--add-reviewer", action="append", metavar="LOGIN")
    pedit.set_defaults(run=cmd_pr_edit)
    pcomment = pr.add_parser("comment")
    pcomment.add_argument("n", type=int)
    _body_args(pcomment)
    pcomment.set_defaults(run=cmd_comment)

    issue = sub.add_parser("issue").add_subparsers(dest="sub", required=True)
    issue.add_parser("list").set_defaults(run=cmd_issue_list)
    iview = issue.add_parser("view")
    iview.add_argument("n", type=int)
    iview.set_defaults(run=cmd_issue_view)
    icreate = issue.add_parser("create")
    icreate.add_argument("--title", required=True)
    _body_args(icreate)
    icreate.add_argument("--label", action="append", metavar="NAME")
    icreate.add_argument("--assignee", action="append", metavar="LOGIN")
    icreate.set_defaults(run=cmd_issue_create)
    iedit = issue.add_parser("edit")
    iedit.add_argument("n", type=int)
    iedit.add_argument("--add-assignee", action="append", metavar="LOGIN")
    iedit.add_argument("--add-label", action="append", metavar="NAME")
    iedit.set_defaults(run=cmd_issue_edit)
    icomment = issue.add_parser("comment")
    icomment.add_argument("n", type=int)
    _body_args(icomment)
    icomment.set_defaults(run=cmd_comment)

    label = sub.add_parser("label").add_subparsers(dest="sub", required=True)
    ensure = label.add_parser("ensure", help="create the label unless the repo has it")
    ensure.add_argument("name")
    ensure.add_argument("--color", default="ededed", metavar="HEX")
    ensure.add_argument("--description", default="")
    ensure.set_defaults(run=cmd_label_ensure)

    sub.add_parser("collaborators").set_defaults(run=cmd_collaborators)

    api = sub.add_parser("api", help="raw REST call; {repo} expands to owner/repo")
    api.add_argument("endpoint")
    api.add_argument("-X", "--method", default="GET")
    api.add_argument("-f", "--field", action="append", metavar="KEY=VALUE")
    api.set_defaults(run=cmd_api)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        name, repo, args.host = detect.resolve(args.repo, args.provider)
    except SystemExit as exc:
        sys.stderr.write("gw: %s\n" % exc)
        return 2
    try:
        provider = get_provider(name, host=args.host)
        args.run(provider, repo, args)
    except (RuntimeError, SystemExit, KeyError) as exc:
        sys.stderr.write("gw: %s\n" % (exc if str(exc) else type(exc).__name__))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
