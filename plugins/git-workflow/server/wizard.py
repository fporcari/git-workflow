"""The desk's wizard — which step every row belongs to.

Computed on every read, like the verdicts: the provider facts say whose move
a PR is, the engine says what kind of move, the analysis' stance says where
it goes. Nothing here calls a model or writes state, and the browser page
and the Claude Code pane draw the same payload.

Da rivedere — somebody else's PR whose review is asked of the user:
  approve   Approvabili, prechecked: the analysis would sign it
  changes   Da respingere, prechecked, with the drafted motivation
  doubt     Dubbie, one at a time: the analysis' doubt, or why there is none
  done      Fatto: what this session sent, and whose move it is now
A row without a current analysis is `pending` until the preparation lands it;
an analysis that failed puts it among the doubts with the reason.

Mie — the user's own PRs, which are merged, fixed or answered, never approved:
  merge     Da mergiare: A1, approved, CLEAN, nothing pending (pr-loop's Lane A)
  fix       Le sistema Claude: a realign (A3) or a clear request (stance fix)
  decide    Da decidere: the reviewer asks a choice; the request, an opinion,
            three options, when the analysis gave them
  waiting   In attesa: the ball is somebody else's, with the chase to paste

Issue — the open issues nobody holds and no PR carries:
  close     Da chiudere: the analysis found the fix already merged
  claude    Le fa Claude: EASY, SINGLE-PHASE, unassigned, nothing to decide
  decide    Da decidere: everything else the analysis read
  done      Fatto

A chi tocca — per person, the PRs and issues whose next move is theirs.
"""

from datetime import date, datetime, timedelta

import verdicts

REVIEW_TODOS = ("review it", "review it (maintainer)", "re-review it")
OPEN = ("preparing", "queued", "taken", "running")
GREEN = "SUCCESS"
STAGE_WORDS = {"queued": "in coda", "starting": "parte…", "inspecting": "legge…",
               "testing": "verifica i test…", "working": "legge…",
               "waiting": "attende capacità", "finalizing": "conclude…"}


TODO_WORDS = {
    "review it": "tocca a te: sei revisore richiesto",
    "review it (maintainer)": "tocca a te: revisione da maintainer",
    "re-review it": "tocca a te: rileggila dopo le modifiche",
    "verify it": "da verificare prima del merge",
    "merge it": "da mergiare",
    "realign with the base": "da riallineare con la base",
    "inspect the conflict before realigning": "conflitto da leggere prima di riallineare",
    "answer the review": "rispondi alla review",
    "resolve the threads": "chiudi i thread aperti",
    "resolve the threads (bloccano il merge)": "chiudi i thread aperti: bloccano il merge",
    "mark ready or finish it": "segnala pronta o finiscila",
    "get a reviewer": "chiedi un revisore",
    "assign the PR to its author before merging": "assegnala al suo autore prima del merge",
    "needs a look - whose move is unclear": "da guardare: non è chiaro a chi tocca",
    "approved but BLOCKED - check the gate": "approvata ma BLOCKED: controlla il gate",
    "approved - merge state not computed": "approvata, stato di merge non letto",
    "verified - merge at your call": "verificata: il merge è tuo",
    "provider result incomplete - inspect before merging":
        "dati incompleti: controlla prima del merge",
}


def todo_text(todo):
    """The engine's to-do, in the words the desk shows."""
    todo = todo or ""
    if todo in TODO_WORDS:
        return TODO_WORDS[todo]
    for prefix, said in (("waiting on ", "aspetta "), ("answer ", "rispondi a ")):
        if todo.startswith(prefix):
            return said + todo[len(prefix):].replace("(draft)", "(bozza)").replace(
                "(changes requested)", "(modifiche richieste)")
    return todo


def _age(created):
    try:
        return max(0, (date.today() - date.fromisoformat(str(created)[:10])).days)
    except ValueError:
        return None


def card(row, **extra):
    """The compact row both drawings work from."""
    advice = row.get("advice") or {}
    out = {"n": row["n"], "repo": row.get("repo"), "label": verdicts.label(row),
           "title": row.get("title"), "author": row.get("author"),
           "age": _age(row.get("created")), "url": row.get("url"),
           "head": row.get("head"), "why": advice.get("why"),
           "labels": row.get("labels") or []}
    out.update(extra)
    return out


def green(row, advice):
    checks = (advice or {}).get("checks") or {}
    return checks.get("state") == GREEN and checks.get("head") == row.get("head")


def held_for_verification(row, advice):
    """needs-verification never reaches the approvable step on tests that
    are not green on the very head the analysis read."""
    return verdicts.VERIFY_LABEL in (row.get("labels") or []) and not green(row, advice)


def skipped_today(note):
    return (note or {}).get("skipped") == date.today().isoformat()


def progress_of(active_jobs):
    """{n: the chip a pending row shows} from the jobs this process runs."""
    out = {}
    for job in active_jobs or []:
        n = (job.get("request") or {}).get("n")
        if job.get("kind") == "analyze" and n is not None:
            stage = (job.get("progress") or {}).get("stage")
            out[int(n)] = STAGE_WORDS.get(stage, "legge…")
    return out


def in_flight(state):
    """{n: event} of the rows a review click has handed to the chat and the
    chat has not closed yet."""
    out = {}
    for key, record in (state.get("requests") or {}).items():
        if key.startswith("review:") and record.get("status") in OPEN:
            for item in (record.get("payload") or {}).get("items") or []:
                out[int(item["n"])] = (record.get("payload") or {}).get("event")
    return out


def review_section(queue, state, me, active_jobs=()):
    rows, gates = queue["rows"], queue.get("gates") or {}
    notes = state.get("prs") or {}
    sending = in_flight(state)
    sent = {n: event for event, ns in (state.get("session_reviews") or {}).items()
            for n in ns}
    run = (state.get("runs") or {}).get("pr-nightwork") or {}
    failed = run.get("failed") or {}
    chips = progress_of(active_jobs)
    steps = {"approve": [], "changes": [], "doubt": []}
    pending, skipped, waiting_author = [], [], 0
    for row in rows:
        if row.get("author") == me:
            continue
        todo = verdicts.verdict(row, me, gates.get(row.get("base")))[0]
        if todo not in REVIEW_TODOS:
            if todo.endswith("(changes requested)"):
                waiting_author += 1
            continue
        note = notes.get(str(row["n"])) or {}
        advice = row.get("advice")
        marks = {key: value for key, value in (
            ("sending", sending.get(row["n"])), ("sent", sent.get(row["n"])),
            ("skipped_before", note.get("skipped"))) if value}
        if skipped_today(note):
            skipped.append(card(row))
            continue
        if not advice:
            reason = failed.get(str(row["n"]))
            if verdicts.too_big(row):
                steps["doubt"].append(card(row, stance=None, doubt=(
                    "PR molto grande (%d righe): Claude non la legge da solo, "
                    "Analizza se vuoi il suo parere" % row["size"]), why="da leggere a mano",
                    **marks))
            elif reason:
                steps["doubt"].append(card(row, stance=None, doubt=(
                    "analisi non riuscita: %s" % reason), why="da leggere a mano",
                    **marks))
            else:
                pending.append(card(row, chip=chips.get(row["n"], "in coda"), **marks))
            continue
        stance = advice.get("stance")
        extra = {key: advice.get(key) for key in (
            "stance", "draft", "doubt", "lean", "hunk", "checks")}
        extra.update(marks)
        if stance == "approve" and held_for_verification(row, advice):
            extra.update(stance="doubt", lean="approve", doubt=(
                "needs-verification: i test non sono verdi sull'head letto"))
            steps["doubt"].append(card(row, **extra))
        elif stance in steps:
            steps[stance].append(card(row, **extra))
        else:
            steps["doubt"].append(card(row, **dict(
                extra, stance="doubt", doubt=advice.get("doubt") or advice.get("why"))))
    for items in steps.values():
        items.sort(key=lambda c: (not c.get("skipped_before"), -c["n"]))
    done = {event: list(ns) for event, ns in (state.get("session_reviews") or {}).items()}
    done["skip"] = [c["n"] for c in skipped]
    order = ("approve", "changes", "doubt")
    first = next((step for step in order if steps[step]),
                 "prepare" if pending else "done")
    return {"count": sum(len(v) for v in steps.values()) + len(pending),
            "steps": [{"id": step, "rows": steps[step]} for step in order]
            + [{"id": "done", "rows": [], "summary": done}],
            "first": first, "pending": pending, "skipped": skipped,
            "waiting_author": waiting_author}


REVIEW_WORDS = {"APPROVED": "approva", "CHANGES_REQUESTED": "chiede modifiche",
                "COMMENTED": "commenta", "DISMISSED": "review ritirata"}
HUNK_LINES = 40


def cut_hunk(diff, path, header):
    """The one hunk an analysis pointed at, out of the PR's whole diff: the
    lines of `path` from the `@@` line that starts like `header` to the next
    hunk or file. None when the diff no longer has it."""
    lines = (diff or "").splitlines()
    marker = header.split("@@")[1].strip() if header.count("@@") >= 2 else header
    in_file = False
    for i, line in enumerate(lines):
        if line.startswith("diff --git "):
            in_file = line.endswith(" b/%s" % path)
            continue
        if in_file and line.startswith("@@") and marker in line:
            body = []
            for follow in lines[i + 1:]:
                if follow.startswith(("@@", "diff --git ")):
                    break
                body.append(follow)
            return {"path": path, "header": line, "lines": body[:HUNK_LINES],
                    "cut": len(body) > HUNK_LINES}
    return None


def timeline(row, me, todo):
    """The PR's story as dated lines, oldest first, ending on whose move it is."""
    events = [(row.get("created") or "", "%s apre la PR" % row.get("author"))]
    for review in row.get("reviews") or []:
        events.append((review.get("on") or "", "%s %s" % (
            review.get("who"), REVIEW_WORDS.get(review.get("state"), review.get("state")))))
    last = row.get("last") or {}
    if last.get("ch") == "comment":
        events.append(((last.get("t") or "")[:10], "%s commenta" % last.get("who")))
    events.sort(key=lambda event: event[0])
    out = [{"on": on, "text": text} for on, text in events]
    out.append({"on": "oggi", "text": todo_text(todo), "now": True})
    return out


def zoom(row, note, me, gate=None, hunk=None):
    """The whole situation of one PR, for the zoom: what the analysis read,
    what it checked and what not, the story, the state, the linked issues."""
    advice = row.get("advice") or {}
    todo, _, _ = verdicts.verdict(row, me, gate)
    checks = advice.get("checks") or {}
    merge = row.get("merge")
    return {"card": card(row, **{key: advice.get(key) for key in (
                "stance", "draft", "doubt", "lean", "hunk", "ask", "options")}),
            "problem": note.get("problem"), "history": note.get("history"),
            "next": note.get("next"), "verified": note.get("verified") or [],
            "not_verified": note.get("not_verified") or [],
            "stale": bool(row.get("analysis_stale")),
            "timeline": timeline(row, me, todo),
            "state": {"tests": checks.get("state") if checks.get("head") == row.get("head")
                      else None,
                      "merge": merge,
                      "conflicts": (row.get("conflict_kind") or "da leggere")
                      if merge == "DIRTY" else "nessuno",
                      "reviewers": sorted(set(row.get("req") or []) | {
                          review.get("who") for review in row.get("reviews") or []
                          if review.get("who")})},
            "closes": [item.get("issue") for item in row.get("closes") or []],
            "hunk": hunk}


def _clock(stamp):
    try:
        return datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None


def prepare_info(state, kind, running=None, now=None):
    """What the preparation of one kind did, or is doing, in one sentence.
    `running` is whether its lock is held right now: a record that says
    running with nobody holding the lock is a run that died."""
    run = (state.get("runs") or {}).get("%s-nightwork" % kind) or {}
    now = now or datetime.now()
    status = run.get("status") or "never"
    if status == "running" and running is False:
        status = "interrupted"
    due = run.get("due") or []
    landed = run.get("landed") or []
    failed = run.get("failed") or {}
    noun = "review" if kind == "pr" else "issue"
    if status == "running":
        phrase = ("preparo %s · %d di %d lette" % (
            "la review" if kind == "pr" else "le issue",
            len(landed) + len(failed), len(due)) if due else
            "preparo %s · leggo GitHub" % ("la review" if kind == "pr" else "le issue"))
    elif status == "never":
        phrase = "%s mai preparata" % noun
    else:
        when = _clock(run.get("prepared_at"))
        night = run.get("prepared_by") == "night"
        if when and night and when.date() == now.date() and when.hour >= 12:
            phrase = "preparata stasera alle %s" % when.strftime("%H:%M")
        elif when and night and now - when < timedelta(hours=18):
            phrase = "preparata stanotte alle %s" % when.strftime("%H:%M")
        elif when and when.date() == now.date():
            phrase = "preparata alle %s" % when.strftime("%H:%M")
        elif when:
            phrase = "preparata il %s alle %s" % (when.strftime("%d/%m"), when.strftime("%H:%M"))
        else:
            phrase = "niente da preparare"
        if status == "interrupted":
            phrase = "preparazione interrotta · %s" % phrase
        elif failed:
            phrase += " · %d non riuscite" % len(failed)
    return {"status": status, "trigger": run.get("trigger"), "due": due,
            "landed": landed, "failed": failed, "phrase": phrase,
            "report": run.get("report"), "at": run.get("at"),
            "prepared_at": run.get("prepared_at"), "prepared_by": run.get("prepared_by")}


def in_background(state, flow):
    """{n: status} of the rows an open loop request of `flow` carries."""
    record = (state.get("requests") or {}).get("run:%s" % flow) or {}
    if record.get("status") not in OPEN + ("needs-input",):
        return {}
    return {int(n): record["status"] for n in (record.get("payload") or {}).get("ns") or []}


def mine_section(queue, state, me):
    rows, gates = queue["rows"], queue.get("gates") or {}
    working = in_background(state, "pr-loop")
    steps = {"merge": [], "fix": [], "decide": [], "waiting": []}
    waiting_rows = []
    for row in rows:
        if row.get("author") != me:
            continue
        gate = gates.get(row.get("base"))
        todo, verdict_state, autorun = verdicts.verdict(row, me, gate)
        advice = row.get("advice") or {}
        extra = {"todo": todo_text(todo), "loop": working.get(row["n"])}
        if autorun == "A1":
            steps["merge"].append(card(row, **extra))
        elif autorun == "A3" or advice.get("stance") == "fix":
            steps["fix"].append(card(row, draft=advice.get("draft"), **extra))
        elif verdict_state == "waiting":
            who = verdicts.waiting_on(row, me, gate)
            steps["waiting"].append(card(row, who=who, **extra))
            waiting_rows.append(dict(row, state="waiting", waiting_on=who))
        else:
            decided = advice.get("stance") == "decide"
            steps["decide"].append(card(row, ask=advice.get("ask") if decided else None,
                                        options=advice.get("options") if decided else None,
                                        draft=advice.get("draft"), **extra))
    for items in steps.values():
        items.sort(key=lambda c: -c["n"])
    order = ("merge", "fix", "decide", "waiting")
    return {"count": sum(len(v) for v in steps.values()),
            "steps": [{"id": step, "rows": steps[step]} for step in order],
            "first": next((step for step in order if steps[step]), "waiting"),
            "chase": verdicts.chase(waiting_rows, me)}


def _issue_card(row, **extra):
    record = row.get("skill") or {}
    out = {"n": row["n"], "repo": row.get("repo"), "label": verdicts.label(row),
           "title": row.get("title"), "author": row.get("author"),
           "age": _age(row.get("created")), "url": row.get("url"),
           "type": row.get("type"), "why": record.get("finding"),
           "size": record.get("size"), "phase": record.get("phase"),
           "assignees": row.get("assignees") or []}
    out.update(extra)
    return out


def issue_analyzed(row):
    record = row.get("skill") or {}
    return bool(record.get("at") and record.get("size") and record.get("phase")
                and not row.get("analysis_stale"))


def closing_note(fixed_by):
    return "Fixed by #%s, already merged." % fixed_by


def issue_section(issues, state):
    working = in_background(state, "issue-loop")
    closed = set((state.get("session_closed") or {}).get("close") or [])
    sending = {}
    record = (state.get("requests") or {}).get("close:issues") or {}
    if record.get("status") in OPEN:
        sending = {int(item["n"]): True for item in (record.get("payload") or {}).get("items") or []}
    steps = {"close": [], "claude": [], "decide": []}
    pending = []
    for row in issues["rows"]:
        record = row.get("skill") or {}
        marks = {key: value for key, value in (
            ("loop", working.get(row["n"])), ("sending", sending.get(row["n"])),
            ("sent", row["n"] in closed)) if value}
        if issue_analyzed(row):
            if record.get("fixed_by"):
                steps["close"].append(_issue_card(
                    row, fixed_by=record["fixed_by"], body=closing_note(record["fixed_by"]),
                    **marks))
            elif (record["size"] == "EASY" and record["phase"] == "SINGLE-PHASE"
                    and not row.get("assignees") and not record.get("decision")):
                steps["claude"].append(_issue_card(row, **marks))
            else:
                steps["decide"].append(_issue_card(
                    row, decision=record.get("decision"), propose=record.get("propose"),
                    **marks))
        elif row.get("in_shortlist"):
            pending.append(_issue_card(row, **marks))
    taken_easy = sum(1 for row in issues.get("others") or []
                     if row.get("excluded") == "taken" and issue_analyzed(row)
                     and (row.get("skill") or {}).get("size") == "EASY")
    order = ("close", "claude", "decide")
    return {"count": len(issues["rows"]),
            "steps": [{"id": step, "rows": steps[step]} for step in order]
            + [{"id": "done", "rows": [],
                "summary": {"close": sorted(closed)}}],
            "first": next((step for step in order if steps[step]),
                          "prepare" if pending else "done"),
            "pending": pending, "taken_easy": taken_easy,
            "unassigned": sum(1 for row in issues["rows"] if not row.get("assignees"))}


CHASE_PARTS = (("merge", "da mergiare"), ("fix", "da correggere"),
               ("review", "review ferme"), ("wait", "in attesa"), ("issues", "issue senza PR"))


def chase_text(who, entry):
    lines = ["%s: %s" % (label, " ".join("#%s" % n for n in entry[key]))
             for key, label in CHASE_PARTS if entry.get(key)]
    return ("@%s — tocca a te:\n%s" % (who, "\n".join(lines))) if lines else None


def whose_section(queue, issues, me, review, mine, issue):
    """Per person, the moves that are theirs: the user's own first, then
    whoever holds the most, nobody last."""
    rows, gates = queue["rows"], queue.get("gates") or {}
    people = {}

    def person(who):
        return people.setdefault(who, {"who": who, "merge": [], "fix": [],
                                       "review": [], "wait": [], "issues": []})
    for row in rows:
        if row.get("author") == me:
            continue
        gate = gates.get(row.get("base"))
        todo, verdict_state, _ = verdicts.verdict(row, me, gate)
        if verdict_state != "waiting":
            continue
        who = verdicts.waiting_on(row, me, gate) or row.get("author")
        bucket = ("merge" if row.get("decision") == "APPROVED" else
                  "fix" if row.get("decision") == "CHANGES_REQUESTED" else "wait")
        person(who)[bucket].append(row["n"])
    for item in (mine["steps"][3]["rows"] if mine else []):
        if item.get("who"):
            person(item["who"])["review"].append(item["n"])
    for row in (issues or {}).get("others") or []:
        if row.get("excluded") == "taken":
            for who in row.get("assignees") or []:
                person(who)["issues"].append(row["n"])
    out = []
    for who, entry in people.items():
        if who == me:
            continue
        entry["chase"] = (mine or {}).get("chase", {}).get(who) or chase_text(who, entry)
        entry["total"] = sum(len(entry[key]) for key in ("merge", "fix", "review", "wait", "issues"))
        if entry["total"]:
            out.append(entry)
    out.sort(key=lambda e: -e["total"])
    me_entry = {"who": me, "me": True,
                "review": (review or {}).get("count", 0),
                "mine": sum(len(step["rows"]) for step in (mine or {}).get("steps", [])
                            if step["id"] in ("merge", "fix", "decide")),
                "issues_mine": sum(1 for row in (issues or {}).get("rows") or []
                                   if me in (row.get("assignees") or [])),
                "for_claude": len(issue["steps"][1]["rows"]) if issue else 0}
    nobody = {"who": None, "unassigned": (issue or {}).get("unassigned", 0)}
    return [me_entry] + out + [nobody]


def build(queue, state, me, active_jobs=(), running=None, issues=None):
    """`running` maps a kind to whether its preparation holds its lock."""
    running = running or {}
    review = review_section(queue, state, me, active_jobs)
    mine = mine_section(queue, state, me)
    issue = issue_section(issues, state) if issues is not None else None
    return {"review": review, "mine": mine, "issue": issue,
            "whose": whose_section(queue, issues, me, review, mine, issue),
            "prepare": {kind: prepare_info(state, kind, running.get(kind))
                        for kind in ("pr", "issue")}}


def _merge_section(sections, last):
    """Sections of several members as one: counts summed, lists joined,
    rows concatenated per step, the first step recomputed."""
    sections = [s for s in sections if s]
    if not sections:
        return None
    out = {}
    by_step = {}
    for section in sections:
        for key, value in section.items():
            if key == "steps":
                for step in value:
                    merged = by_step.setdefault(step["id"], {"id": step["id"], "rows": []})
                    merged["rows"] += step["rows"]
                    for event, ns in (step.get("summary") or {}).items():
                        merged.setdefault("summary", {}).setdefault(event, []).extend(ns)
            elif isinstance(value, bool) or value is None:
                continue
            elif isinstance(value, int):
                out[key] = out.get(key, 0) + value
            elif isinstance(value, list):
                out[key] = out.get(key, []) + value
            elif isinstance(value, dict):
                out.setdefault(key, {}).update(value)
    out["steps"] = list(by_step.values())
    pending = out.get("pending")
    out["first"] = next((s["id"] for s in out["steps"] if s["rows"] and s["id"] != last),
                        "prepare" if pending else last)
    return out


def merge(parts):
    """Several members' wizards as one: rows concatenated per step, the
    preparation counted across them."""
    if len(parts) == 1:
        return parts[0]
    review = _merge_section([p["review"] for p in parts], "done")
    mine = _merge_section([p["mine"] for p in parts], "waiting")
    issue = _merge_section([p["issue"] for p in parts], "done")
    whose = {}
    for part in parts:
        for entry in part["whose"]:
            key = (entry.get("me"), entry["who"])
            if key not in whose:
                whose[key] = dict(entry)
                continue
            for field, value in entry.items():
                if isinstance(value, list):
                    whose[key][field] = whose[key][field] + value
                elif isinstance(value, int) and not isinstance(value, bool):
                    whose[key][field] = whose[key].get(field, 0) + value
    prepare = {}
    for kind in ("pr", "issue"):
        infos = [part["prepare"][kind] for part in parts]
        lead = next((i for i in infos if i["status"] == "running"), infos[0])
        merged = dict(lead, due=[n for i in infos for n in i["due"]],
                      landed=[n for i in infos for n in i["landed"]],
                      failed={k: v for i in infos for k, v in i["failed"].items()})
        if merged["status"] == "running" and merged["due"]:
            merged["phrase"] = "preparo %s · %d di %d lette" % (
                "la review" if kind == "pr" else "le issue",
                len(merged["landed"]) + len(merged["failed"]), len(merged["due"]))
        prepare[kind] = merged
    return {"review": review, "mine": mine, "issue": issue,
            "whose": list(whose.values()), "prepare": prepare}
