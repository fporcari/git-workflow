"""Provider contract.

Every provider normalizes its hosting service into the same two shapes, so the
verdict engine and the UI never know which service they are talking to.

PR row (the pr-triage skill's rows.json shape). `merge` may be None on a
provider whose merge state is a separate phase — see mergestates() below:
    n, title, created (YYYY-MM-DD), author, draft (bool), base,
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

    def mergestates(self, repo, me):
        """Phase two, for providers where the merge state is expensive:
        {"<number>": "CLEAN"|"DIRTY"|...} for the user's own PRs. An empty
        dict means phase one already carried it."""
        return {}

    def issues(self, repo):
        """Open issues, newest first, as normalized rows."""
        raise NotImplementedError

    def merge_command(self, repo, n):
        """The exact CLI command that merges the PR — handed to the user,
        never executed by the desk."""
        raise NotImplementedError
