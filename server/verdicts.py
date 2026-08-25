"""Field-only verdict engine, ported from the pr-triage skill (section 7).

Works on the normalized row shape every provider returns; never reads diffs.
Verdicts computed here are the honest field-level subset: anything that would
need a diff read is reported as `asks`, exactly as the skill prescribes.
"""


def _last_who(row):
    """Who spoke last, ignoring approvals: an approval closes a conversation,
    it never opens one (same rule as the pr-triage chase.jq)."""
    last = row.get("last")
    if last and last.get("ch") == "approved":
        return None
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
        if decision == "APPROVED" and not req:
            if merge == "CLEAN":
                return ("merge it", "ready", "A1")
            if merge == "BLOCKED":
                return ("approved but BLOCKED - check the gate", "decision", "asks")
            return ("approved - merge state not computed", "decision", "asks")
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


def handoff(row, repo, merge_command):
    """The prepared action for one row: the exact command for an A1, a
    ready-to-paste /pr-run prompt for everything else that is the user's
    move. The desk never executes; it hands over."""
    n, todo = row["n"], row["todo"]
    if row["autorun"] == "A1":
        return {"kind": "command", "label": "Copia comando merge",
                "text": merge_command}
    if row["state"] == "waiting":
        who = row["req"][0] if row["req"] else row["author"]
        return {"kind": "chase", "label": "Copia sollecito",
                "text": "@%s — PR #%s (%s) aperta dal %s, tocca a te."
                        % (who, n, row["title"], row["created"])}
    context = ("titolo: %s · autore: %s · review: %s · merge: %s · thread aperti: %s/%s"
               % (row["title"], row["author"], row["decision"] or "nessuna",
                  row["merge"], row["unresolved"], row["threads"]))
    return {"kind": "prompt", "label": "Copia prompt per Claude",
            "text": "/pr-run — solo la PR #%s di %s: %s. Contesto dal desk: %s."
                    % (n, repo, todo, context)}


def issue_handoff(row, repo):
    if row["assignees"]:
        return None
    return {"kind": "prompt", "label": "Copia prompt per Claude",
            "text": "/issue-triage — nella selezione prendi la #%s di %s (%s, %s): "
                    "analizzala e proponimi la mossa."
                    % (row["n"], repo, row["type"], row["title"])}


def fallback_chase(rows):
    """Group the waiting rows per person — the raw, field-only version of
    pr-triage's block 4. The verified blocks come from the skill's export."""
    per = {}
    for row in rows:
        todo = row.get("todo", "")
        if row.get("state") == "waiting" and todo.startswith("waiting on "):
            who = todo.split("waiting on ", 1)[1].split(" ")[0]
            per.setdefault(who, []).append("#%s" % row["n"])
    return {who: "@%s — %s PR ferme: %s" % (who, len(ns), " ".join(ns))
            for who, ns in sorted(per.items(), key=lambda kv: -len(kv[1]))}
