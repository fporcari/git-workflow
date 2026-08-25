"""Verdict engine, ported from the pr-triage skill (sections 5-7).

Works on the normalized row shape every provider returns; never reads diffs.
Anything that would need a diff read is reported as `asks`, exactly as the
skill prescribes — that is the boundary, and it is where a model earns its
tokens.

Everything on THIS side of the boundary is deterministic and belongs here
rather than in a model turn: the todo/state/autorun vocabulary (§7 — 0.07 ms
for 52 PRs), the partition into the five blocks (§5 — a pure function of
those three), and the per-person chase grouping (§6). The desk used to ask
the attached chat to reproduce all of it on every refresh: ~28k tokens of
input, and a whole turn of latency, to re-derive a mapping.

Pass a `gate` (see gate.py) and the verdicts stop being conservative where an
API read settles the question — above all WHO MAY LAND. On a base whose
protection makes the merge impossible for him, an approved CLEAN PR of his
own is not something he can land, and a verdict engine that has not read the
gate says `A1 → merge it` there and is wrong. The reverse matters just as
much: a restriction that does NOT bind him (an admin where enforce_admins is
off) must keep its `A1` — whose merge it is by convention is a house rule,
not something to infer from a protection setting.
"""


def _last_who(row):
    """Who spoke last, ignoring approvals: an approval closes a conversation,
    it never opens one (same rule as the pr-triage chase.jq)."""
    last = row.get("last")
    if last and last.get("ch") == "approved":
        return None
    return last.get("who") if last else None


def _landing(gate):
    """The verdict for an otherwise-mergeable PR the user CANNOT land.

    Capability only. Whose merge it is by convention — on this team the
    author's/assignee's — is not the gate's business, and an admin who gets
    through a restriction still owns his own merge: that case keeps its A1
    and says so in a note (gate.notes), it is not handed away.
    """
    if not gate or gate.get("can_land", True):
        return None
    who = ", ".join(gate.get("landers") or []) or "nessuno"
    return ("approvata \u2014 non puoi mergiare su %s, \u00e8 riservato a %s"
            % (gate["branch"], who), "waiting", "-")


def verdict(row, me, gate=None):
    """Return (todo, state, autorun) for one normalized PR row.

    `gate` is the protection of THIS row's base branch, or None for the
    field-only reading.
    """
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
        answered = last_who in (me, None)
        if decision == "CHANGES_REQUESTED" and not answered:
            return ("answer the review", "attention", "asks")
        if unresolved and not answered:
            hard = gate and gate.get("conversation_resolution")
            return ("resolve the threads" + (" (bloccano il merge)" if hard else ""),
                    "attention", "asks")
        if decision == "APPROVED" and not req:
            if merge == "CLEAN":
                return _landing(gate) or ("merge it", "ready", "A1")
            if merge == "BLOCKED":
                if gate and unresolved and gate.get("conversation_resolution"):
                    return ("approvata ma i thread aperti bloccano il merge",
                            "attention", "asks")
                if gate and not gate.get("protected"):
                    return ("approvata \u2014 base non protetta, BLOCKED \u00e8 "
                            "altro", "decision", "asks")
                return _landing(gate) or ("approved but BLOCKED - check the gate",
                                          "decision", "asks")
            return ("approved - merge state not computed", "decision", "asks")
        if not reviews and not req:
            return ("get a reviewer", "attention", "asks")
        if answered:
            # the user spoke last (an answer, a push comment): the ball is
            # with whoever was asked, or with whoever requested the changes
            waiting_on = req[0] if req else None
            if not waiting_on and decision == "CHANGES_REQUESTED":
                for r in reviews:
                    if r["state"] == "CHANGES_REQUESTED":
                        waiting_on = r["who"]
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


def decorate(rows, me, gates=None):
    """Annotate every row. `gates` maps a base branch to its gate dict."""
    for row in rows:
        gate = (gates or {}).get(row.get("base"))
        todo, state, autorun = verdict(row, me, gate)
        row["todo"] = todo
        row["state"] = state
        row["autorun"] = autorun
        row["waiting_on"] = waiting_on(row, me, gate) if state == "waiting" else None
    return rows


# ---------------------------------------------------------------------------
# §5 — the five blocks. A pure function of (autorun, state, todo); the model
# used to be asked to reproduce this partition on every refresh.

BLOCK_TITLES = ("Da mergiare subito", "Azione banale", "Review da fare",
                "Solo tue", "In attesa di altri")
REVIEW_TODOS = ("review it", "re-review it")


def block_of(row):
    if row.get("autorun") == "A1":
        return "Da mergiare subito"
    if row.get("autorun") in ("A2", "A3"):
        return "Azione banale"
    if row.get("todo") in REVIEW_TODOS:
        return "Review da fare"
    if row.get("state") == "waiting":
        return "In attesa di altri"
    # a decision, or an `attention` that is nobody else's move: his call
    return "Solo tue"


def blocks(rows):
    """The §5 output, computed. Same shape the skill exports, so the desk
    renders one thing whether the grid came from here or from a model."""
    grouped = {title: [] for title in BLOCK_TITLES}
    for row in rows:
        grouped[block_of(row)].append({
            "n": row["n"], "date": row.get("created"), "author": row.get("author"),
            "what": row.get("title"), "todo": row.get("todo"),
            "autorun": row.get("autorun"), "base": row.get("base"),
        })
    return [{"title": title, "rows": grouped[title]} for title in BLOCK_TITLES]


# ---------------------------------------------------------------------------
# §6 — the chase blocks, per person, oldest first, ready to paste.

def waiting_on(row, me, gate=None):
    """Who owes the next move on a waiting row — from the FIELDS, never by
    re-reading the verdict's prose. Parsing the sentence back out worked
    until the wording changed, which is exactly the kind of coupling that
    breaks silently."""
    if row.get("draft"):
        return None if row.get("author") == me else row.get("author")
    if row.get("author") != me:
        return row.get("author")
    # the user's own PR: whoever was asked, or whoever requested the changes,
    # or — when the gate says so — whoever may actually land it
    if row.get("req"):
        return row["req"][0]
    if row.get("decision") == "CHANGES_REQUESTED":
        for review in row.get("reviews") or []:
            if review.get("state") == "CHANGES_REQUESTED":
                return review.get("who")
    if gate and gate.get("landers") and not gate.get("can_land"):
        return gate["landers"][0]
    return None


def chase(rows):
    per = {}
    for row in rows:
        if row.get("state") != "waiting":
            continue
        who = row.get("waiting_on")
        if who:
            per.setdefault(who, []).append(row)
    out = {}
    for who, items in sorted(per.items(), key=lambda kv: -len(kv[1])):
        items.sort(key=lambda r: r.get("created") or "")
        lines = ["#%s (%s) %s" % (r["n"], r.get("created"), r.get("title"))
                 for r in items]
        out[who] = ("@%s \u2014 %s PR ferme su di te, la pi\u00f9 vecchia dal %s:\n%s"
                    % (who, len(items), items[0].get("created"), "\n".join(lines)))
    return out


def fallback_chase(rows):
    """The name the desk and the skills already import."""
    return chase(rows)


def handoff(row, repo, merge_command):
    """The prepared action for one row: the exact command for an A1, a
    ready-to-paste /pr-loop prompt for everything else that is the user's
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
            "text": "/pr-loop — solo la PR #%s di %s: %s. Contesto dal desk: %s."
                    % (n, repo, todo, context)}


def issue_handoff(row, repo):
    if row["assignees"]:
        return None
    return {"kind": "prompt", "label": "Copia prompt per Claude",
            "text": "/issue-triage — nella selezione prendi la #%s di %s (%s, %s): "
                    "analizzala e proponimi la mossa."
                    % (row["n"], repo, row["type"], row["title"])}
