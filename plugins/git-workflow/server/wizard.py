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
"""

from datetime import date, datetime, timedelta

import verdicts

REVIEW_TODOS = ("review it", "review it (maintainer)", "re-review it")
GREEN = "SUCCESS"
STAGE_WORDS = {"queued": "in coda", "starting": "parte…", "inspecting": "legge…",
               "testing": "verifica i test…", "working": "legge…",
               "waiting": "attende capacità", "finalizing": "conclude…"}


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


def review_section(queue, state, me, active_jobs=()):
    rows, gates = queue["rows"], queue.get("gates") or {}
    notes = state.get("prs") or {}
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
        if skipped_today(note):
            skipped.append(card(row))
            continue
        if not advice:
            reason = failed.get(str(row["n"]))
            if reason:
                steps["doubt"].append(card(row, stance=None, doubt=(
                    "analisi non riuscita: %s" % reason), why="da leggere a mano"))
            else:
                pending.append(card(row, chip=chips.get(row["n"], "in coda")))
            continue
        stance = advice.get("stance")
        extra = {key: advice.get(key) for key in (
            "stance", "draft", "doubt", "lean", "hunk", "checks")}
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
        items.sort(key=lambda c: -c["n"])
    done = (state.get("session_reviews") or {})
    order = ("approve", "changes", "doubt")
    first = next((step for step in order if steps[step]),
                 "prepare" if pending else "done")
    return {"count": sum(len(v) for v in steps.values()) + len(pending),
            "steps": [{"id": step, "rows": steps[step]} for step in order]
            + [{"id": "done", "rows": [], "summary": done}],
            "first": first, "pending": pending, "skipped": skipped,
            "waiting_author": waiting_author}


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
        if when and run.get("prepared_by") == "night" and now - when < timedelta(hours=18):
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


def build(queue, state, me, active_jobs=(), running=None):
    """`running` maps a kind to whether its preparation holds its lock."""
    running = running or {}
    return {"review": review_section(queue, state, me, active_jobs),
            "prepare": {kind: prepare_info(state, kind, running.get(kind))
                        for kind in ("pr", "issue")}}


def merge(parts):
    """Several members' wizards as one: rows concatenated per step, the
    preparation counted across them."""
    if len(parts) == 1:
        return parts[0]
    review = {"count": 0, "steps": [], "pending": [], "skipped": [], "waiting_author": 0}
    by_step = {}
    for part in parts:
        section = part["review"]
        review["count"] += section["count"]
        review["waiting_author"] += section["waiting_author"]
        review["pending"] += section["pending"]
        review["skipped"] += section["skipped"]
        for step in section["steps"]:
            merged = by_step.setdefault(step["id"], {"id": step["id"], "rows": []})
            merged["rows"] += step["rows"]
            if "summary" in step:
                merged.setdefault("summary", {}).update(step["summary"])
    review["steps"] = list(by_step.values())
    review["first"] = next((s["id"] for s in review["steps"] if s["rows"]), "done")
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
    return {"review": review, "prepare": prepare}
