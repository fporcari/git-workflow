"""The issue cross-check, computed — not re-derived by a model.

issue-triage Step 1 is four questions, and every one of them is data:

    has anyone already worked this?      a remote branch matching (^|/)<n>-
    is there already a PR on it?         closingIssuesReferences of the queue
    have I already looked at it?         the provider's `commenter:<me>` search
    is it mine to work?                  assignees

Doing it here instead of in a model turn is what turns ~14k tokens of issue
rows into a shortlist of ten the model actually has to read. What stays with
the model is the part that needs judgement and cannot be looked up: ranking
by impact (it means reading the body), and deciding whether a numbered
branch's content is already on the base — `git cherry` is not evidence after
a squash.

The branch match is on the NUMBER, never on a prefix: prefixes drift.
"""

import re
import subprocess


def remote_branches(cwd):
    out = subprocess.run(("git", "ls-remote", "--heads", "origin"),
                         capture_output=True, text=True, cwd=cwd, timeout=60)
    if out.returncode:
        return []
    return [line.split("refs/heads/", 1)[1]
            for line in out.stdout.splitlines() if "refs/heads/" in line]


def branches_for(number, branches):
    """Branches that name this issue: the number followed by a separator,
    at the start of the ref or right after a `/`."""
    pattern = re.compile(r"(?:^|/)%d[-_/]" % number)
    return [b for b in branches if pattern.search(b)]


def collect(relations, branches, queue_rows):
    """Everything the cross-check can know without a model.

    Everything returned is JSON-safe: this goes through the disk cache, and
    a set in here silently kills the write and makes the whole thing run
    twice — once per caller — paying for the branch listing each time.
    """
    open_prs = {}
    for row in queue_rows:
        for closes in row.get("closes") or []:
            open_prs.setdefault(str(closes["issue"]), []).append(row["n"])
    return {"commented": sorted(relations.get("commented") or []),
            "commented_complete": bool(relations.get("complete", True)),
            "assigned_to_me": sorted(relations.get("assigned") or []),
            "branches": list(branches or []),
            "open_prs": open_prs}


def annotate(issue_rows, check):
    """Attach the cross-check to every issue row, and say what it means."""
    commented = set(check["commented"])
    assigned = set(check["assigned_to_me"])
    for row in issue_rows:
        n = row["n"]
        refs = branches_for(n, check["branches"])
        prs = check["open_prs"].get(str(n)) or []
        row["cross"] = {
            "branches": refs,
            "open_prs": prs,
            "seen_by_me": n in commented,
            "mine": n in assigned,
        }
        row["cross"]["note"] = _note(row, refs, prs)
    return issue_rows


def _note(row, refs, prs):
    if prs:
        return "PR aperta: %s" % " ".join("#%s" % p for p in prs)
    if refs and not row["assignees"]:
        return ("branch %s ma nessuna PR e nessun assignee \u2014 lavoro fermo, "
                "da verificare se il contenuto \u00e8 gi\u00e0 sulla base"
                % " ".join(refs))
    if refs:
        return "branch %s, in mano a %s" % (" ".join(refs),
                                            ", ".join(row["assignees"]))
    if row["assignees"]:
        return "in mano a %s" % ", ".join(row["assignees"])
    return "nessun branch, nessuna PR, nessun assignee"


def shortlist(issue_rows, limit=10):
    """The batch worth a model's attention: never looked at, nobody on it,
    no PR. Newest first until a triage has ranked them — the ranking by
    impact needs the bodies read, so it is the model's, and it arrives as
    `impact` on the rows this filter chose."""
    fresh = [r for r in issue_rows
             if not r["assignees"]
             and not r["cross"]["open_prs"]
             and not r["cross"]["seen_by_me"]]
    fresh.sort(key=lambda r: r.get("created") or "", reverse=True)
    fresh = fresh[:limit]
    if any(r.get("impact") for r in fresh):
        # unranked rows go last, in date order, rather than jumping to the top
        fresh.sort(key=lambda r: r.get("impact") or 1e6)
    return fresh


def shortlist_export(issue_rows, limit=10):
    """The issue-desk shortlist. Computed on every read — a filter is not a
    verdict, and a model copy of it was one more thing to keep in sync."""
    return {"computed": True,
            "rows": [{"n": r["n"], "date": r.get("created"), "author": r.get("author"),
                      "type": r.get("type"), "title": r.get("title"),
                      "impact": r.get("impact"),
                      "assignee": ", ".join(r["assignees"]) or None,
                      "note": r["cross"]["note"]}
                     for r in shortlist(issue_rows, limit)]}
