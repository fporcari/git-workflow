"""gw CLI tests — stdlib unittest, fixture provider or mocked transports.

    python3 -m unittest tests.test_gw -v      (from server/)

Covers host detection (the one place a wrong default would read as "nothing
to do"), every verb on the fixture, and the two live providers against
recorded REST payloads so the same JSON shape is proven on both services.
"""

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import gw                          # noqa: E402
import prdesk                      # noqa: E402
from providers import detect       # noqa: E402
from providers import base         # noqa: E402
from providers.forgejo import ForgejoProvider  # noqa: E402
from providers.github import GitHubProvider    # noqa: E402

FIXTURE = str(ROOT / "tests" / "fixtures" / "gw.json")


def run(*argv, fixture=FIXTURE):
    out, err = io.StringIO(), io.StringIO()
    env = {"DESK_FIXTURE": fixture} if fixture else {}
    with mock.patch.dict(os.environ, env), redirect_stdout(out), redirect_stderr(err):
        code = gw.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class DetectTest(unittest.TestCase):
    def test_every_remote_form_yields_host_and_repo(self):
        for url in ("ssh://git@hub.genro.com/erpy/erpy-engine.git",
                    "git@hub.genro.com:erpy/erpy-engine.git",
                    "https://hub.genro.com/erpy/erpy-engine",
                    "HTTPS://HUB.GENRO.COM/erpy/erpy-engine.git"):
            self.assertEqual(detect.parse_remote(url), ("hub.genro.com", "erpy/erpy-engine"), url)

    def test_github_is_github_and_forgejo_needs_its_url(self):
        self.assertEqual(detect.provider_for("github.com"), "github")
        with mock.patch.dict(os.environ, {"FORGEJO_URL": "https://hub.genro.com/"}):
            self.assertEqual(detect.provider_for("hub.genro.com"), "forgejo")
        with mock.patch.dict(os.environ, {"FORGEJO_URL": ""}):
            with self.assertRaises(SystemExit) as ctx:
                detect.provider_for("hub.genro.com")
            self.assertIn("FORGEJO_URL=https://hub.genro.com", str(ctx.exception))

    def test_an_unknown_host_is_an_error_not_a_default(self):
        with self.assertRaises(SystemExit):
            detect.provider_for("gitlab.example.org")

    def test_a_host_prefixed_repo_needs_no_origin(self):
        with mock.patch.object(detect, "origin_url", side_effect=AssertionError("origin read")):
            self.assertEqual(detect.resolve("github.com/acme/widgets"), ("github", "acme/widgets", "github.com"))
            self.assertEqual(detect.resolve("acme/widgets", "fixture"), ("fixture", "acme/widgets", None))

    def test_explicit_provider_and_repo_need_no_origin(self):
        with mock.patch.object(detect, "origin_url", side_effect=AssertionError("origin read")):
            code, out, err = run("--provider", "fixture", "--repo", "acme/widgets", "whoami")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), {"login": "alice"})

    def test_desk_and_cli_preserve_explicit_forgejo_host(self):
        with mock.patch.dict(os.environ, {"FORGEJO_URL": "https://hub.example", "FORGEJO_TOKEN": "t"}), \
             mock.patch.object(detect, "origin_url", side_effect=AssertionError("origin read")), \
             mock.patch.object(ForgejoProvider, "default_branch", return_value="main"):
            p, repo = prdesk.provider_and_repo(mock.Mock(repo="hub.example/acme/widgets", provider=None))
            code, out, err = run("--repo", "hub.example/acme/widgets", "repo", "info")
        self.assertEqual((p.base, repo), ("https://hub.example", "acme/widgets"))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["host"], "hub.example")

    def test_the_forgejo_base_keeps_the_scheme_and_port_of_forgejo_url(self):
        with mock.patch.dict(os.environ, {"FORGEJO_URL": "http://localhost:3000", "FORGEJO_TOKEN": "t"}), \
             mock.patch.object(detect, "origin_url", return_value="http://localhost:3000/acme/widgets.git"):
            p, repo = prdesk.provider_and_repo(mock.Mock(repo=None, provider=None))
        self.assertEqual((p.base, repo), ("http://localhost:3000", "acme/widgets"))

    def test_a_new_provider_is_one_class_and_one_registry_entry(self):
        class GitLabProvider(base.Provider):
            name = "gitlab"

            @classmethod
            def hosts(cls):
                return ["gitlab.example"]

        with mock.patch.dict(detect.PROVIDERS, {"gitlab": GitLabProvider}), \
             mock.patch.object(detect, "origin_url", return_value="git@gitlab.example:acme/widgets.git"):
            self.assertEqual(detect.resolve(), ("gitlab", "acme/widgets", "gitlab.example"))
            p, repo = prdesk.provider_and_repo(mock.Mock(repo=None, provider=None))
            self.assertEqual(detect.resolve("acme/widgets", "gitlab"), ("gitlab", "acme/widgets", "gitlab.example"))
        self.assertEqual((type(p), p.host, repo), (GitLabProvider, "gitlab.example", "acme/widgets"))

    def test_the_cli_exits_2_on_an_unknown_host(self):
        with mock.patch.object(detect, "origin_url", return_value="https://gitlab.example.org/a/b.git"):
            code, out, err = run("whoami")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("unknown git host", err)

    def test_the_desk_no_longer_defaults_to_github(self):
        with mock.patch.object(detect, "origin_url", return_value="ssh://git@hub.genro.com/erpy/x.git"), \
             mock.patch.dict(os.environ, {"FORGEJO_URL": ""}):
            args = mock.Mock(repo=None, provider=None)
            with self.assertRaises(SystemExit):
                prdesk.provider_and_repo(args)


class FixtureVerbsTest(unittest.TestCase):
    R = ("--provider", "fixture", "--repo", "acme/widgets")

    def verb(self, *argv):
        code, out, err = run(*self.R, *argv)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def test_whoami_and_repo(self):
        self.assertEqual(self.verb("whoami"), {"login": "alice"})
        self.assertEqual(self.verb("repo", "default-branch"), {"default_branch": "main"})
        info = self.verb("repo", "info")
        self.assertEqual((info["repo"], info["provider"], info["default_branch"]),
                         ("acme/widgets", "fixture", "main"))

    def test_pr_list_and_mine(self):
        self.assertEqual([r["n"] for r in self.verb("pr", "list")], [7, 8])
        self.assertEqual([r["n"] for r in self.verb("pr", "list", "--mine")], [7])

    def test_pr_view_carries_the_documented_shape(self):
        pr = self.verb("pr", "view", "7")
        for key in ("n", "title", "body", "state", "draft", "author", "assignees", "labels",
                    "created", "updated", "base", "head", "merge", "decision", "req",
                    "reviews", "closes", "comments", "url"):
            self.assertIn(key, pr)
        self.assertEqual(pr["head"], {"ref": "fix/7-widget", "sha": "h7"})
        self.assertEqual(pr["closes"], [{"issue": 3, "source": "provider"}])
        self.assertEqual(self.verb("pr", "reviews", "7")[0]["commit"], "h7")

    def test_pr_diff_text_and_names(self):
        code, out, _ = run(*self.R, "pr", "diff", "7")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("diff --git a/widgets/core.py"))
        self.assertEqual(self.verb("pr", "diff", "7", "--name-only"), ["widgets/core.py", "CHANGELOG.md"])

    def test_issue_list_and_view(self):
        self.assertEqual([i["n"] for i in self.verb("issue", "list")], [3])
        issue = self.verb("issue", "view", "3")
        self.assertEqual(issue["comments"][0]["who"], "alice")

    def test_incomplete_issue_list_fails_without_partial_output(self):
        with mock.patch("providers.fixture.FixtureProvider.issues", return_value={"rows": [], "truncated": True}):
            code, out, err = run(*self.R, "issue", "list")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("incomplete", err)

    def test_api_expands_repo_and_returns_the_recorded_body(self):
        self.assertEqual(self.verb("api", "repos/{repo}/branches/main/protection"),
                         {"enforce_admins": {"enabled": True}})

    def test_a_missing_item_exits_1_with_a_message(self):
        code, out, err = run(*self.R, "pr", "view", "99")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("no PR 99", err)


# ---- recorded payloads, the shape both REST APIs share -------------------

def _user(login):
    return {"login": login, "id": 1}


PULL = {
    "number": 12, "title": "Add gw", "body": "Adds it.\n\nFixes #4",
    "state": "open", "draft": False, "merged": False, "mergeable": True,
    "mergeable_state": "blocked", "user": _user("alice"),
    "assignees": [_user("alice")], "labels": [{"name": "needs-verification"}],
    "requested_reviewers": [_user("bob")],
    "base": {"ref": "main", "sha": "b" * 40}, "head": {"ref": "codex/gw", "sha": "h" * 40},
    "created_at": "2026-09-08T10:00:00Z", "updated_at": "2026-09-08T11:00:00Z",
    "comments": 2, "review_comments": 1, "html_url": "https://x/acme/widgets/pull/12",
}
REVIEWS_GH = [
    {"user": _user("bob"), "state": "CHANGES_REQUESTED", "commit_id": "0" * 40,
     "submitted_at": "2026-09-08T10:30:00Z", "body": "no"},
    {"user": _user("bob"), "state": "APPROVED", "commit_id": "h" * 40,
     "submitted_at": "2026-09-08T10:45:00Z", "body": ""},
    {"user": _user("bob"), "state": "PENDING", "commit_id": "h" * 40, "submitted_at": None, "body": ""},
]
REVIEWS_FJ = [dict(r, state={"CHANGES_REQUESTED": "REQUEST_CHANGES"}.get(r["state"], r["state"]))
              for r in REVIEWS_GH]
ISSUE = {"number": 4, "title": "Need gw", "body": "please", "state": "open",
         "user": _user("dave"), "assignees": [], "labels": [{"name": "enhancement"}],
         "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-02T00:00:00Z",
         "html_url": "https://x/acme/widgets/issues/4"}
COMMENTS = [{"user": _user("alice"), "created_at": "2026-09-02T00:00:00Z", "body": "on it"}]
DIFF = "diff --git a/server/gw.py b/server/gw.py\n--- a/server/gw.py\n+++ b/server/gw.py\n@@ -0,0 +1 @@\n+x\n"

EXPECTED = {
    "n": 12, "title": "Add gw", "body": "Adds it.\n\nFixes #4", "state": "open",
    "draft": False, "author": "alice", "assignees": ["alice"], "labels": ["needs-verification"],
    "created": "2026-09-08", "updated": "2026-09-08T11:00:00Z",
    "base": {"ref": "main", "sha": "b" * 40}, "head": {"ref": "codex/gw", "sha": "h" * 40},
    "decision": "APPROVED", "req": ["bob"],
    "reviews": [
        {"who": "bob", "state": "CHANGES_REQUESTED", "on": "2026-09-08", "commit": "0" * 40, "has_text": True},
        {"who": "bob", "state": "APPROVED", "on": "2026-09-08", "commit": "h" * 40, "has_text": False},
    ],
    "comments": 3, "url": "https://x/acme/widgets/pull/12",
}


class GitHubShapeTest(unittest.TestCase):
    def gh(self, *args, **kw):
        joined = " ".join(args)
        if joined.startswith("api graphql"):
            return json.dumps([{"data": {"repository": {"pullRequest": {
                "closingIssuesReferences": {"nodes": [{"number": 4}]}}}}}])
        table = {
            "api repos/acme/widgets/pulls/12": PULL,
            "api repos/acme/widgets/pulls/12/reviews?per_page=100": REVIEWS_GH,
            "api repos/acme/widgets/pulls?state=open&per_page=100&sort=created&direction=desc": [PULL],
            "api repos/acme/widgets/issues/4": ISSUE,
            "api repos/acme/widgets/issues/4/comments?per_page=100": COMMENTS,
        }
        if joined == "pr diff 12 --repo acme/widgets":
            return DIFF
        data = table[" ".join(args[:2])]
        return json.dumps([data] if "--paginate" in args else data)

    def test_lists_and_reviews_include_later_pages(self):
        def gh(*args):
            if args[1].endswith("/12"):
                return json.dumps(PULL)
            if args[1] == "graphql":
                return self.gh(*args)
            self.assertIn("--paginate", args)
            self.assertIn("--slurp", args)
            if "/reviews?" in args[1]:
                return json.dumps([[REVIEWS_GH[0]], [REVIEWS_GH[1]]])
            return json.dumps([[dict(PULL, user=_user("other"))], [PULL]])

        with mock.patch("providers.github._gh", side_effect=gh), \
             mock.patch.object(GitHubProvider, "whoami", return_value="alice"):
            pr = GitHubProvider().pr_detail("acme/widgets", 12)
            code, out, err = run("--repo", "github.com/acme/widgets", "pr", "list", "--mine")
        self.assertEqual(pr["decision"], "APPROVED")
        self.assertEqual(len(pr["reviews"]), 2)
        self.assertEqual(code, 0, err)
        self.assertEqual([row["author"] for row in json.loads(out)], ["alice"])

    def test_issue_comments_include_later_pages(self):
        def gh(*args):
            if "/comments?" in args[1]:
                self.assertIn("--paginate", args)
                self.assertIn("--slurp", args)
                return json.dumps([COMMENTS, [dict(COMMENTS[0], body="later")]])
            return json.dumps(ISSUE)

        with mock.patch("providers.github._gh", side_effect=gh):
            issue = GitHubProvider().issue_detail("acme/widgets", 4)
        self.assertEqual([c["body"] for c in issue["comments"]], ["on it", "later"])

    def test_linked_issues_include_later_pages(self):
        pages = [{"data": {"repository": {"pullRequest": {
            "closingIssuesReferences": {"nodes": [{"number": n}]}}}}} for n in (4, 5)]
        with mock.patch("providers.github._gh", return_value=json.dumps(pages)) as gh:
            closes = GitHubProvider()._closes("acme/widgets", 12)
        self.assertEqual([c["issue"] for c in closes], [4, 5])
        self.assertIn("--paginate", gh.call_args.args)
        self.assertIn("--slurp", gh.call_args.args)
        self.assertIn("after:$endCursor", GitHubProvider.CLOSES_GQL)
        self.assertIn("pageInfo{hasNextPage endCursor}", GitHubProvider.CLOSES_GQL)

    def test_get_fields_never_switch_to_post(self):
        with mock.patch("providers.github._gh", return_value="[]") as gh:
            GitHubProvider().api("repos/acme/widgets/issues", method="GET", fields={"state": "open"})
        gh.assert_called_once_with("api", "repos/acme/widgets/issues", "-X", "GET", "-f", "state=open")

    def test_pr_detail_shape(self):
        with mock.patch("providers.github._gh", side_effect=self.gh):
            pr = GitHubProvider().pr_detail("acme/widgets", 12)
        self.assertEqual(pr, dict(EXPECTED, merge="BLOCKED",
                                  closes=[{"issue": 4, "source": "provider"}]))

    def test_pulls_issue_and_diff(self):
        with mock.patch("providers.github._gh", side_effect=self.gh):
            p = GitHubProvider()
            self.assertEqual(p.pulls("acme/widgets")[0]["req"], ["bob"])
            issue = p.issue_detail("acme/widgets", 4)
            self.assertEqual(base.diff_paths(p.pr_diff("acme/widgets", 12)), ["server/gw.py"])
        self.assertEqual((issue["author"], issue["labels"], issue["comments"][0]["who"]),
                         ("dave", ["enhancement"], "alice"))


class ForgejoShapeTest(unittest.TestCase):
    def request(self, path, method="GET", params=None, fields=None, accept=None):
        table = {
            "/repos/acme/widgets/pulls/12": PULL,
            "/repos/acme/widgets/pulls/12/reviews": REVIEWS_FJ,
            "/repos/acme/widgets/pulls": [PULL],
            "/repos/acme/widgets/issues/4": ISSUE,
            "/repos/acme/widgets/issues/4/comments": COMMENTS,
            "/repos/acme/widgets": {"default_branch": "main"},
            "/user": _user("alice"),
        }
        if path.endswith(".diff"):
            return DIFF
        if params and params.get("page", 1) > 1:
            return "[]"
        return json.dumps(table[path])

    def provider(self):
        with mock.patch.dict(os.environ, {"FORGEJO_TOKEN": "t"}):
            return ForgejoProvider(host="hub.example")

    def test_pr_detail_has_the_same_shape_as_github(self):
        p = self.provider()
        with mock.patch.object(ForgejoProvider, "_request", side_effect=self.request):
            pr = p.pr_detail("acme/widgets", 12)
        self.assertEqual(pr, dict(EXPECTED, merge="CLEAN",
                                  closes=[{"issue": 4, "source": "body"}]))

    def test_the_rest_of_the_verbs(self):
        p = self.provider()
        with mock.patch.object(ForgejoProvider, "_request", side_effect=self.request):
            self.assertEqual(p.whoami(), "alice")
            self.assertEqual(p.default_branch("acme/widgets"), "main")
            self.assertEqual(p.pulls("acme/widgets")[0]["n"], 12)
            self.assertEqual(p.issue_detail("acme/widgets", 4)["comments"][0]["body"], "on it")
            self.assertEqual(base.diff_paths(p.pr_diff("acme/widgets", 12)), ["server/gw.py"])

    def test_lists_and_reviews_include_later_pages(self):
        def request(path, **kwargs):
            params = kwargs.get("params") or {}
            page = params.get("page", 1)
            if page > 2:
                return "[]"
            if path.endswith("/reviews"):
                return json.dumps([REVIEWS_FJ[page - 1]])
            if path.endswith("/pulls"):
                return json.dumps([dict(PULL, user=_user("other" if page == 1 else "alice"))])
            return self.request(path, **kwargs)

        p = self.provider()
        with mock.patch.object(ForgejoProvider, "_request", side_effect=request), \
             mock.patch("providers.forgejo.PAGE", 1), \
             mock.patch("gw.get_provider", return_value=p):
            pr = p.pr_detail("acme/widgets", 12)
            code, out, err = run("--provider", "forgejo", "--repo", "hub.example/acme/widgets",
                                 "pr", "list", "--mine")
        self.assertEqual(pr["decision"], "APPROVED")
        self.assertEqual(len(pr["reviews"]), 2)
        self.assertEqual(code, 0, err)
        self.assertEqual([row["author"] for row in json.loads(out)], ["alice"])

    def test_get_fields_are_query_parameters(self):
        p = self.provider()
        with mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = b"[]"
            p.api("repos/acme/widgets/issues?limit=10", fields={"state": "open"})
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.full_url, "https://hub.example/api/v1/repos/acme/widgets/issues?limit=10&state=open")
        self.assertIsNone(request.data)

    def test_a_dismissed_approval_is_not_an_approval(self):
        p = self.provider()
        dismissed = [dict(REVIEWS_FJ[0], state="APPROVED", dismissed=True)]
        with mock.patch.object(ForgejoProvider, "_get_all", return_value=dismissed), \
             mock.patch.object(ForgejoProvider, "_get", return_value=PULL):
            detail = p.pr_detail("acme/widgets", 12)
        self.assertEqual([r["state"] for r in detail["reviews"]], ["DISMISSED"])
        self.assertEqual(detail["decision"], "REVIEW_REQUIRED")
        self.assertEqual(p._row("acme/widgets", PULL, dismissed)["decision"], None)

    def test_issue_comments_are_read_in_one_call(self):
        p = self.provider()
        calls = []

        def request(path, method="GET", params=None, fields=None, accept=None):
            calls.append((path, params))
            return self.request(path, method, params, fields, accept)

        with mock.patch.object(ForgejoProvider, "_request", side_effect=request):
            issue = p.issue_detail("acme/widgets", 4)
        self.assertEqual(len(issue["comments"]), 1)
        self.assertEqual([c for c in calls if c[0].endswith("/comments")],
                         [("/repos/acme/widgets/issues/4/comments", {})])

    def test_a_short_page_ends_the_pagination(self):
        p = self.provider()
        with mock.patch.object(ForgejoProvider, "_get", return_value=[PULL]) as get:
            self.assertEqual(len(p._get_all("/repos/acme/widgets/pulls")), 1)
        get.assert_called_once()

    def test_the_provider_addresses_the_origin_host(self):
        self.assertEqual(self.provider().base, "https://hub.example")
        with mock.patch.dict(os.environ, {"FORGEJO_TOKEN": ""}):
            with self.assertRaises(SystemExit):
                ForgejoProvider(host="hub.example")


if __name__ == "__main__":
    unittest.main()
