"""Provider contract.

Every provider normalizes its hosting service into the same two shapes, so the
verdict engine and the UI never know which service they are talking to.

PR row (the pr-triage skill's rows.json shape). `merge` may be None on a
provider whose merge state is a separate phase — see mergestates() below:
    n, title, created (YYYY-MM-DD), author, labels [names], assignees [logins],
    draft (bool),
    base, base_head (base commit oid), head (commit oid), incomplete (bool),
    merge (CLEAN|DIRTY|BLOCKED|UNSTABLE|UNKNOWN), decision
    (APPROVED|CHANGES_REQUESTED|REVIEW_REQUIRED|None),
    req [logins], reviews [{who, state, on, commit, has_text, verification}], unresolved (int),
    threads (int),
    closes [{issue, assignees}], last {t, who, ch} | None, url

Issue row:
    n, title, created, author, labels [names], assignees [logins],
    comments (int), url

The `gw` CLI adds per-item reads, same shape on every service:

PR detail:
    n, title, body, state (open|closed|merged), draft, author, assignees,
    labels, created, updated, base {ref, sha}, head {ref, sha},
    merge (CLEAN|DIRTY|BLOCKED|UNSTABLE|BEHIND|UNKNOWN),
    decision (derived from the reviews, see decision_from),
    req [logins], reviews [{who, state, on, commit, has_text}],
    closes [{issue, source}] (source: provider|body), comments (int), url

Issue detail:
    n, title, body, state, author, assignees, labels, created, updated, url,
    comments [{who, t, body}]
"""

import re

CLOSES = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s*#(\d+)", re.I)
REVIEW_STATES = ("APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED")


def closes_from_body(body):
    """The issues a PR body says it closes — what a service without a
    linked-issues API (Forgejo) resolves at merge time from these keywords."""
    seen = []
    for number in CLOSES.findall(body or ""):
        if int(number) not in seen:
            seen.append(int(number))
    return [{"issue": n, "source": "body"} for n in seen]


def decision_from(reviews, requests):
    """One rule for every service: the latest review of each person counts,
    a standing CHANGES_REQUESTED blocks, otherwise any APPROVED approves,
    otherwise a pending request or a mere comment leaves the review required."""
    latest = {}
    for review in reviews:
        if review.get("state") in ("APPROVED", "CHANGES_REQUESTED"):
            latest[review.get("who")] = review["state"]
    if "CHANGES_REQUESTED" in latest.values():
        return "CHANGES_REQUESTED"
    if "APPROVED" in latest.values():
        return "APPROVED"
    return "REVIEW_REQUIRED" if (reviews or requests) else None


class Provider:
    name = "base"

    def whoami(self):
        raise NotImplementedError

    def queue(self, repo, me):
        """Open PRs the user is involved in.

        Returns {"rows": [...], "total": int, "truncated": bool} — the count
        is reported so a page cap never drops PRs silently.
        """
        raise NotImplementedError

    def open_numbers(self, repo, me):
        """Cheap authoritative membership for the user's open PR queue.

        Providers may override this when their detailed queue is expensive.
        The default keeps the contract correct for smaller providers.
        """
        return [row["n"] for row in self.queue(repo, me)["rows"]]

    def mergestates(self, repo, me):
        """Phase two, for providers where the merge state is expensive:
        {"<number>": "CLEAN"|"DIRTY"|...} for the user's own PRs. An empty
        dict means phase one already carried it."""
        return {}

    def analysis_probe(self, repo, n):
        """Fresh, lightweight facts used before a full PR analysis.

        Providers without a cheap detail endpoint return None; the analysis
        then follows its complete verification path.
        """
        return None

    def issues(self, repo):
        """Open issues, newest first.

        Returns {"rows": [...], "total": int, "truncated": bool} — same
        promise as queue(): a page cap is reported, never hidden.

        A row carries `updated`, the last activity on the issue: it is what
        tells a dated analysis apart from one the issue has moved past.
        """
        raise NotImplementedError

    def merge_command(self, repo, n):
        """The exact CLI command that merges the PR — handed to the user,
        never executed by the desk."""
        raise NotImplementedError

    def default_branch(self, repo):
        """The branch a PR targets unless told otherwise — the one base whose
        gate is worth reading before the queue has even arrived."""
        return "main"

    def gates(self, repo, me, bases):
        """The merge gate of each base branch (see gate.py for the shape).
        An empty dict means this service exposes no protection to read, and
        the verdicts fall back to their field-only reading."""
        return {}

    def remote_branches(self, cwd):
        """Every branch on the remote — how the desk knows somebody already
        started an issue. Repo-local, so the default is git itself."""
        import issuecheck
        return issuecheck.remote_branches(cwd)

    def issue_relations(self, repo, me):
        """Which open issues the user has already commented on, and which
        are assigned to him — two cheap searches that decide what a model
        has to read. `complete` is False when a page cap cut them short."""
        return {"commented": [], "assigned": [], "complete": True}

    # ---- per-item reads, the gw CLI's verbs ---------------------------

    def pulls(self, repo, state="open"):
        """Every PR in `state`, newest first, as brief rows:
        n, title, author, draft, base, head, created, updated, labels,
        assignees, req, url."""
        raise NotImplementedError

    def pr_detail(self, repo, n):
        """One PR, the shape documented at the top of this file."""
        raise NotImplementedError

    def pr_reviews(self, repo, n):
        """[{who, state, on, commit, has_text}], oldest first. `commit` is
        the head the review was given on: an approval on another commit is
        not an approval of this one."""
        raise NotImplementedError

    def pr_diff(self, repo, n):
        """The unified diff as text."""
        raise NotImplementedError

    def issue_detail(self, repo, n):
        """One issue with its comments, the shape documented at the top."""
        raise NotImplementedError

    def api(self, endpoint, method="GET", fields=None):
        """Raw passthrough to the service's REST API, endpoint relative to
        its API root. For what the verbs do not carry — never for writes
        the verbs exist for."""
        raise NotImplementedError


def diff_paths(diff):
    """The files a unified diff touches, in order, once each."""
    paths = []
    for line in (diff or "").splitlines():
        if line.startswith("diff --git "):
            path = line.split(" b/", 1)[-1]
            if path not in paths:
                paths.append(path)
    return paths


def verification_result(body):
    line = (body or "").strip().splitlines()
    if not line:
        return None
    for result in ("PASS", "FAIL", "BLOCKED"):
        if line[-1] == "Verification result: " + result:
            return result
    return None
