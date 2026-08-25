"""Field-only verdict engine, ported from the pr-triage skill (section 7).

Works on the normalized row shape every provider returns; never reads diffs.
Verdicts computed here are the honest field-level subset: anything that would
need a diff read is reported as `asks`, exactly as the skill prescribes.
"""


def _last_who(row):
    last = row.get("last")
    return last.get("who") if last else None


def verdict(row, me):
    """Return (todo, state, autorun) for one normalized PR row."""
    author = row.get("author")
    mine = author == me
    draft = row.get("draft")
    merge = row.get("merge")
    decision = row.get("decision")
    req = row.get("req") or []
    reviews = row.get("reviews") or []
    unresolved = row.get("unresolved") or 0
    last_who = _last_who(row)

    review_states = {}
    for r in reviews:
        review_states[r["who"]] = r["state"]

    if draft:
        if mine:
            return ("mark ready or finish it", "decision", "yours")
        return ("waiting on %s (draft)" % author, "waiting", "-")

    if me in req:
        return ("review it", "attention", "asks")

    if review_states.get(me) == "CHANGES_REQUESTED" and last_who not in (me, None):
        return ("re-review it", "attention", "asks")

    if mine:
        if merge == "DIRTY":
            return ("realign with the base", "attention", "A3")
        if decision == "CHANGES_REQUESTED" and last_who != me:
            return ("answer the review", "attention", "asks")
        if unresolved:
            return ("resolve the threads", "attention", "asks")
        if decision == "APPROVED" and not req and merge == "CLEAN":
            return ("merge it", "ready", "A1")
        if not reviews and not req:
            return ("get a reviewer", "attention", "asks")
        if last_who == me or last_who is None:
            waiting_on = req[0] if req else None
            if waiting_on:
                return ("waiting on %s" % waiting_on, "waiting", "-")
            return ("needs a look - whose move is unclear", "decision", "asks")
        return ("answer %s" % last_who, "attention", "asks")

    if last_who == me:
        return ("waiting on %s" % author, "waiting", "-")
    return ("waiting on %s" % author, "waiting", "-")


ISSUE_TYPES = (
    ("DEFECT", ("bug", "hotfix"), ("[bug]",)),
    ("REQUEST", ("enhancement", "feature"), ("[feature]", "[feat]")),
    ("QUESTION", ("question",), ("[question]", "how ")),
    ("DOCS", ("documentation", "docs"), ("[docs]",)),
)


def issue_type(labels, title):
    labels = [(label or "").lower() for label in labels]
    low_title = (title or "").lower()
    for name, label_set, markers in ISSUE_TYPES:
        if any(label in labels for label in label_set):
            return name
        if any(m in low_title for m in markers):
            return name
    return "UNCLASSIFIED"


def decorate(rows, me):
    for row in rows:
        todo, state, autorun = verdict(row, me)
        row["todo"] = todo
        row["state"] = state
        row["autorun"] = autorun
    return rows
