"""Provider contract.

Every provider normalizes its hosting service into the same two shapes, so the
verdict engine and the UI never know which service they are talking to.

PR row (the pr-triage skill's rows.json shape). `merge` may be None on a
provider whose merge state is a separate phase — see mergestates() below:
    n, title, created (YYYY-MM-DD), author, assignees [logins], draft (bool),
    base, base_head (base commit oid), head (commit oid), incomplete (bool),
    merge (CLEAN|DIRTY|BLOCKED|UNSTABLE|UNKNOWN), decision
    (APPROVED|CHANGES_REQUESTED|REVIEW_REQUIRED|None),
    req [logins], reviews [{who, state, on}], unresolved (int), threads (int),
    closes [{issue, assignees}], last {t, who, ch} | None, url

Issue row:
    n, title, created, author, labels [names], assignees [logins],
    comments (int), url
"""


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
