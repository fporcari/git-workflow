"""Preparation — the analyses the desk owes, paid in background, read-only.

One mechanism, two triggers: the nightwork's command line in the evening and
the desk's own boot in the morning. Both start the very jobs the desk's
buttons start, with their read-only profiles, so the results land keyed where
the desk reads them, and both take the same lock per kind and repository: an
evening run still going when the desk opens is not doubled, it is shown.

pr     the triage grid, then one pr-analyze job per PR the desk counts as
       owing an analysis (model_tasks), at most `parallel` alive at once, and
       one triage pass for the conflict readings still owed on the user's own
       PRs.
issue  the shortlist ranked by one issue-triage pass, then one issue-analyze
       job per shortlisted issue without a reusable analysis.

The run is recorded under runs.<kind>-nightwork as it goes — what is due,
what landed, what failed and why — so the desk shows rows moving into their
steps while it works, and `prepared_at` / `prepared_by` keep when work last
landed: a later run that finds nothing to do leaves "prepared last night" true.
"""

import fcntl
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import deskstate
import jobs
import notify

POLL = 2
CONFLICTS = "conflitti"
RANKING = "classifica"
KINDS = ("pr", "issue")


def label_of(kind):
    return "%s-nightwork" % kind


def bounded(repo, work, parallel, landed=None):
    """Run `work`, (label, start) pairs whose start() returns a job id, with at
    most `parallel` jobs alive; return the final job record per label, and
    hand each one to `landed` as it ends.

    A job is over when this process no longer runs it, not when its file stops
    saying `running`: a desk launched meanwhile may stamp a job it cannot
    verify as orphaned while this process is still preparing it."""
    waiting = list(work)
    alive = {}
    done = {}

    def finish(label, record):
        done[label] = record
        if landed:
            landed(label, record)
    while waiting or alive:
        while waiting and len(alive) < parallel:
            label, start = waiting.pop(0)
            try:
                alive[start()] = label
            except Exception as exc:
                finish(label, {"status": "error", "error": str(exc)[:300]})
        live = {record.get("id") for record in jobs.active(repo)}
        for job_id, label in list(alive.items()):
            if job_id not in live:
                del alive[job_id]
                finish(label, jobs.get(repo, job_id)
                       or {"status": "error", "error": "job record missing"})
        if alive:
            time.sleep(POLL)
    return done


def conflict_rows(desk, fresh=True):
    """The triage export narrowed to its conflict readings: the analyses are
    this run's own jobs, one per PR, not one long triage process."""
    path = Path(desk.run_triage("pr-triage", fresh=fresh))
    rows = json.loads(path.read_text())
    tasks = {n: ["conflict"] for n, kinds in rows["model_tasks"].items()
             if "conflict" in kinds}
    rows.update(model_tasks=tasks, needs_model=[int(n) for n in tasks])
    path.write_text(json.dumps(rows, indent=1))
    return path


def smallest_first(numbers, rows):
    """The PRs to analyze, the smallest first so the approvable ones land
    early; a PR whose size the provider did not say goes last."""
    size = {row["n"]: row.get("size") for row in rows}
    return sorted(numbers, key=lambda n: (size.get(n) is None, size.get(n) or 0, n))


def pr_work(desk, fresh=True):
    export = json.loads(Path(desk.run_triage("pr-triage", fresh=fresh)).read_text())
    tasks = export["model_tasks"]
    due = smallest_first([int(n) for n, kinds in tasks.items() if "analysis" in kinds],
                         export.get("queue") or [])
    work = [(n, lambda n=n: jobs.analyze_pr(
                desk.repo, n, desk.me, desk.cwd, desk.agent,
                lambda: desk.analysis_inputs(n)))
            for n in due]
    if any("conflict" in kinds for kinds in tasks.values()):
        work.append((CONFLICTS, lambda: jobs.triage(
            desk.repo, "pr-triage", lambda: conflict_rows(desk, fresh),
            desk.me, desk.cwd, desk.agent)))
    return work


def issue_due(desk):
    """The shortlisted issues, in the ranked order, whose analysis is missing
    or older than the issue's last activity."""
    issues = desk.issues()
    updated = {row["n"]: row.get("updated") for row in issues["rows"]}
    notes = deskstate.load(desk.repo).get("issues") or {}
    return [row["n"] for row in (issues["shortlist"] or {}).get("rows", [])
            if not deskstate.issue_analysis_reusable(
                notes.get(str(row["n"])) or {}, updated.get(row["n"]))]


def _progress(repo, kind):
    """`landed` for bounded(): each finished item goes into the run's record
    the moment it ends, so a reader sees the run advance."""
    label = label_of(kind)

    def landed(item, record):
        def mutate(state):
            run = state.setdefault("runs", {}).setdefault(label, {})
            if isinstance(item, int):
                if record.get("status") == "done":
                    run.setdefault("landed", []).append(item)
                else:
                    run.setdefault("failed", {})[str(item)] = _why(record)
            else:
                run.setdefault("passes", {})[item] = record.get("status")
        deskstate.update(repo, mutate)
    return landed


def _due(repo, kind, items):
    def mutate(state):
        state.setdefault("runs", {}).setdefault(label_of(kind), {})["due"] = list(items)
    deskstate.update(repo, mutate)


def pr_night(desk, parallel, fresh=True):
    work = pr_work(desk, fresh)
    _due(desk.repo, "pr", [label for label, _ in work if isinstance(label, int)])
    return bounded(desk.repo, work, parallel, _progress(desk.repo, "pr"))


def issue_night(desk, parallel, fresh=True):
    landed = _progress(desk.repo, "issue")
    ranked = bounded(desk.repo, [(RANKING, lambda: jobs.triage(
        desk.repo, "issue-triage",
        lambda: desk.run_triage("issue-triage", fresh=fresh),
        desk.me, desk.cwd, desk.agent))], 1, landed)
    due = issue_due(desk)
    _due(desk.repo, "issue", due)
    work = [(n, lambda n=n: jobs.analyze_issue(
                desk.repo, n, desk.me, desk.cwd, desk.agent))
            for n in due]
    return {**ranked, **bounded(desk.repo, work, parallel, landed)}


def _why(record):
    return str(record.get("error") or record.get("status") or "?")[:120]


def summary(kind, done):
    """(status, report): failed only when there was work and none of it
    landed; a run that analyzed ten and lost one did its job and says so."""
    items = {k: v for k, v in done.items() if isinstance(k, int)}
    passes = {k: v for k, v in done.items() if not isinstance(k, int)}
    ok = [k for k, v in items.items() if v.get("status") == "done"]
    noun = "PR" if kind == "pr" else "issue"
    parts = (["%d %s analizzate" % (len(ok), noun)] if items
             else ["nessuna %s da analizzare" % noun])
    for label, text in ((RANKING, "shortlist classificata"),
                        (CONFLICTS, "conflitti letti")):
        if (passes.get(label) or {}).get("status") == "done":
            parts.append(text)
    failed = (["#%s (%s)" % (k, _why(v)) for k, v in sorted(items.items())
               if v.get("status") != "done"] +
              ["%s (%s)" % (k, _why(v)) for k, v in passes.items()
               if v.get("status") != "done"])
    if failed:
        parts.append("non riuscite: " + ", ".join(failed))
    landed = ok or any(v.get("status") == "done" for v in passes.values())
    return ("failed" if done and not landed else "done"), ", ".join(parts)


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _start(repo, kind, trigger):
    def mutate(state):
        runs = state.setdefault("runs", {})
        previous = runs.get(label_of(kind)) or {}
        runs[label_of(kind)] = {
            "status": "running", "trigger": trigger, "started": _now(),
            "at": _now(), "report": "in corso", "due": [], "landed": [],
            "failed": {}, "passes": {},
            **{key: previous[key] for key in ("prepared_at", "prepared_by")
               if key in previous}}
    deskstate.update(repo, mutate)


def finish_run(repo, kind, status, report, worked=False, trigger=None):
    def mutate(state):
        run = state.setdefault("runs", {}).setdefault(label_of(kind), {})
        run.update(status=status, report=report, at=_now())
        if worked:
            run.update(prepared_at=_now(), prepared_by=trigger or run.get("trigger"))
    deskstate.update(repo, mutate)


@contextmanager
def exclusive(repo, label):
    path = deskstate.runtime_path(repo, "%s.lock" % label)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def running(repo, kind):
    """Whether a run of this kind holds its lock now, in any process."""
    with exclusive(repo, label_of(kind)) as free:
        return not free


def prepare(desk, kind, parallel=4, trigger="night"):
    """One repository's run of one kind: (status, report). `trigger` is
    `night` (the command line, which pays a fresh provider read) or `desk`
    (the boot, which reads the snapshot the boot just paid for)."""
    label = label_of(kind)
    with exclusive(desk.repo, label) as free:
        if not free:
            return "failed", "%s già in corso su questo repository" % label
        _start(desk.repo, kind, trigger)
        notify.notify(desk.repo, "%s partito" % label)
        done = {}
        try:
            done = (pr_night if kind == "pr" else issue_night)(
                desk, parallel, fresh=trigger == "night")
            status, report = summary(kind, done)
        except Exception as exc:
            status, report = "failed", "interrotto: %s" % str(exc)[:200]
        worked = any(item.get("status") == "done" for item in done.values())
        finish_run(desk.repo, kind, status, report, worked, trigger)
        notify.notify(desk.repo, "%s: %s" % (label, report))
        return status, report


def lanes(kinds, parallel):
    """How many of the `parallel` jobs each kind may hold: with both, the
    issues keep one, so they never wait for the whole PR backlog."""
    if len(kinds) < 2:
        return {kind: parallel for kind in kinds}
    return {kind: (1 if kind == "issue" else max(1, parallel - 1)) for kind in kinds}


def prepare_all(desks, kinds=KINDS, parallel=4, trigger="desk"):
    """The desk's boot: the PRs and the issues side by side, each over every
    member in turn."""
    share = lanes(kinds, parallel)

    def lane(kind):
        for desk in desks:
            try:
                prepare(desk, kind, share[kind], trigger)
            except Exception:
                continue

    threads = [threading.Thread(target=lane, args=(kind,), daemon=True) for kind in kinds]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
