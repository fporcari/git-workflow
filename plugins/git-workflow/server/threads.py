"""Threads — every open issue with the PRs that close or cite it, on every
repository of the scope, grouped by who has to move.

Computed from the rows the desk already holds, never by a model: a PR's
`closes` (owner/repo#n included), an issue's `prs` and `prs_elsewhere` (the
provider's own link, which sees PRs outside the user's queue) and its
`refs`. A PR's verdict is the one the engine already gave it; a PR nobody
has triaged yet stays `untriaged` here too — fetch paints facts.

    {"groups": [{"id", "who", "label", "threads": [...]}],
     "people": {"<login>|": {"threads": n, "prs": [ref], "issues": [ref]}}}

A thread: {"key", "issue": row|None, "prs": [pr], "cites": [ref],
"cited_by": [ref], "same_title": [ref], "who", "move", "group"}; a ref is
"owner/repo#n".
"""

MINE, NOBODY, UNTRIAGED = "me", "nobody", "untriaged"
ISSUE_FIELDS = ("repo", "n", "title", "type", "author", "assignees", "labels",
                "created", "updated", "url", "comments", "excluded")
PR_FIELDS = ("repo", "n", "title", "author", "assignees", "state", "todo", "waiting_on",
             "merge", "decision", "draft", "created", "url", "triage_status")


def ref(repo, n):
    return "%s#%s" % (repo, n)


def _key(repo, n):
    return (repo or "").lower(), int(n)


def link_closes(prs, issues):
    """Fill the assignees and title of every closed issue the desk knows —
    Forgejo's queue reads `closes` off the body, which carries neither, and a
    closed issue of another repository is known only here. Fresh lists, so
    the cached rows are never touched."""
    known = {_key(i.get("repo"), i["n"]): i for i in issues}
    for pr in prs:
        linked = []
        for item in pr.get("closes") or []:
            item = dict(item)
            issue = known.get(_key(item.get("repo") or pr.get("repo"), item["issue"]))
            if issue and item.get("assignees") is None:
                item["assignees"] = list(issue.get("assignees") or [])
                item.setdefault("title", issue.get("title"))
            linked.append(item)
        pr["closes"] = linked
    return prs


def _brief(row, fields):
    return {f: row.get(f) for f in fields if f in row}


def _pr_move(pr, me):
    state = pr.get("state")
    if pr.get("triage_status") != "current" or state == "untriaged":
        return UNTRIAGED, "senza verdetto"
    if state == "waiting":
        who = pr.get("waiting_on")
        return (who or NOBODY), (pr.get("todo") or "in attesa")
    return MINE, pr.get("todo") or state


def _issue_move(issue, me):
    owners = issue.get("assignees") or []
    if me in owners:
        return MINE, "iniziala"
    if owners:
        return owners[0], "di %s" % owners[0]
    return NOBODY, "da prendere"


def build(prs, issues, me):
    issues_by_key = {_key(i.get("repo"), i["n"]): i for i in issues}
    prs_by_key = {_key(p.get("repo"), p["n"]): p for p in prs}
    attached = {key: [] for key in issues_by_key}
    claimed = set()
    for pr in prs:
        for item in pr.get("closes") or []:
            key = _key(item.get("repo") or pr.get("repo"), item["issue"])
            if key in attached and pr not in attached[key]:
                attached[key].append(pr)
                claimed.add(_key(pr.get("repo"), pr["n"]))
    for key, issue in issues_by_key.items():
        linked = [(issue.get("repo"), n) for n in issue.get("prs") or []]
        linked += [(x["repo"], x["n"]) for x in issue.get("prs_elsewhere") or []]
        for repo, n in linked:
            pr = prs_by_key.get(_key(repo, n))
            stub = pr or {"repo": repo, "n": n, "in_queue": False}
            if all(_key(p.get("repo"), p["n"]) != _key(repo, n) for p in attached[key]):
                attached[key].append(stub)
                if pr:
                    claimed.add(_key(repo, n))

    titles = {}
    for issue in issues:
        titles.setdefault(((issue.get("repo") or "").lower(),
                           (issue.get("title") or "").strip().lower()), []).append(issue)
    cited_by = {}
    for issue in issues:
        for r in issue.get("refs") or []:
            target = _key(r.get("repo") or issue.get("repo"), r["n"])
            if target in issues_by_key and target != _key(issue.get("repo"), issue["n"]):
                cited_by.setdefault(target, []).append(ref(issue.get("repo"), issue["n"]))

    threads = []
    for key, issue in issues_by_key.items():
        linked = attached[key]
        triaged = [p for p in linked if p.get("in_queue", True)]
        if triaged:
            moves = [_pr_move(p, me) for p in triaged]
            group, move = next((m for m in moves if m[0] == MINE), moves[0])
        else:
            group, move = _issue_move(issue, me)
        same = [ref(i.get("repo"), i["n"]) for i in titles[((issue.get("repo") or "").lower(),
                                                             (issue.get("title") or "").strip().lower())]
                if i is not issue]
        threads.append({
            "key": ref(issue.get("repo"), issue["n"]),
            "issue": _brief(issue, ISSUE_FIELDS),
            "prs": [_brief(p, PR_FIELDS) if p.get("in_queue", True) else dict(p) for p in linked],
            "cites": [ref(r.get("repo") or issue.get("repo"), r["n"]) for r in issue.get("refs") or []],
            "cited_by": cited_by.get(key, []),
            "same_title": same,
            "group": group, "move": move})
    for key, pr in prs_by_key.items():
        if key in claimed:
            continue
        group, move = _pr_move(pr, me)
        threads.append({"key": ref(pr.get("repo"), pr["n"]), "issue": None,
                        "prs": [_brief(pr, PR_FIELDS)], "cites": [], "cited_by": [],
                        "same_title": [], "group": group, "move": move})

    people = {}
    for thread in threads:
        mark = people.setdefault(thread["group"], {"threads": 0, "prs": [], "issues": []})
        mark["threads"] += 1
        for pr in thread["prs"]:
            if pr.get("in_queue", True):
                mark["prs"].append(ref(pr.get("repo"), pr["n"]))
        if thread["issue"] and not thread["prs"]:
            mark["issues"].append(thread["key"])

    def order(gid):
        rank = {MINE: 0, UNTRIAGED: 3, NOBODY: 2}.get(gid, 1)
        return rank, -people[gid]["threads"], gid

    groups = []
    for gid in sorted(people, key=order):
        members = [t for t in threads if t["group"] == gid]
        members.sort(key=lambda t: ((t["issue"] or t["prs"][0]).get("created") or ""), reverse=True)
        groups.append({"id": gid, "who": None if gid in (MINE, NOBODY, UNTRIAGED) else gid,
                       "threads": members})
    return {"groups": groups, "people": people, "total": len(threads)}
