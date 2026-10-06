"""Nightwork — the analyses the desk would owe in the morning, paid the
evening before, read-only.

    python3 nightwork.py --kind pr|issue [--repo owner/repo]... [--org [host/]owner]...
                         [--folder DIR] [--agent auto|claude|codex] [--parallel 4]

It starts the very jobs the desk's own buttons start, with their read-only
profiles, so the server's validators persist the results where the desk reads
them, keyed to the facts they were read from: a PR that moves before morning
shows its analysis as stale, never as current.

pr     publishes the triage grid as the triage button does, then one pr-analyze
       job per PR the desk counts as owing an analysis (model_tasks), at most
       --parallel alive at once, and one triage pass for the conflict readings
       still owed on the user's own PRs.
issue  ranks the shortlist (open issues nobody holds, cited by no PR, never
       commented by the user) with one issue-triage pass, then one
       issue-analyze job per shortlisted issue without a reusable analysis.

Nothing is written to the provider. The outcome lands under
runs.<kind>-nightwork and as one feed line; a second run of the same kind on
the same repository refuses to start while the first is alive.
"""

import argparse
import fcntl
import json
import os
import signal
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import deskstate
import jobs
import notify
import prdesk
from providers import PROVIDERS

POLL = 2
CONFLICTS = "conflitti"
RANKING = "classifica"


def bounded(repo, work, parallel):
    """Run `work`, (label, start) pairs whose start() returns a job id, with at
    most `parallel` jobs alive; return the final job record per label.

    A job is over when this process no longer runs it, not when its file stops
    saying `running`: a desk launched meanwhile may stamp a job it cannot
    verify as orphaned while this process is still preparing it."""
    waiting = list(work)
    alive = {}
    done = {}
    while waiting or alive:
        while waiting and len(alive) < parallel:
            label, start = waiting.pop(0)
            try:
                alive[start()] = label
            except Exception as exc:
                done[label] = {"status": "error", "error": str(exc)[:300]}
        live = {record.get("id") for record in jobs.active(repo)}
        for job_id, label in list(alive.items()):
            if job_id not in live:
                done[label] = (jobs.get(repo, job_id)
                               or {"status": "error", "error": "job record missing"})
                del alive[job_id]
        if alive:
            time.sleep(POLL)
    return done


def conflict_rows(desk):
    """The triage export narrowed to its conflict readings: the analyses are
    this run's own jobs, one per PR, not one long triage process."""
    path = Path(desk.run_triage("pr-triage"))
    rows = json.loads(path.read_text())
    tasks = {n: ["conflict"] for n, kinds in rows["model_tasks"].items()
             if "conflict" in kinds}
    rows.update(model_tasks=tasks, needs_model=[int(n) for n in tasks])
    path.write_text(json.dumps(rows, indent=1))
    return path


def pr_work(desk):
    tasks = json.loads(Path(desk.run_triage("pr-triage")).read_text())["model_tasks"]
    work = [(int(n), lambda n=int(n): jobs.analyze_pr(
                desk.repo, n, desk.me, desk.cwd, desk.agent,
                lambda: desk.analysis_inputs(n)))
            for n, kinds in tasks.items() if "analysis" in kinds]
    if any("conflict" in kinds for kinds in tasks.values()):
        work.append((CONFLICTS, lambda: jobs.triage(
            desk.repo, "pr-triage", lambda: conflict_rows(desk),
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


def pr_night(desk, parallel):
    return bounded(desk.repo, pr_work(desk), parallel)


def issue_night(desk, parallel):
    ranked = bounded(desk.repo, [(RANKING, lambda: jobs.triage(
        desk.repo, "issue-triage", lambda: desk.run_triage("issue-triage"),
        desk.me, desk.cwd, desk.agent))], 1)
    work = [(n, lambda n=n: jobs.analyze_issue(
                desk.repo, n, desk.me, desk.cwd, desk.agent))
            for n in issue_due(desk)]
    return {**ranked, **bounded(desk.repo, work, parallel)}


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


def record(repo, label, status, report):
    def mutate(state):
        state.setdefault("runs", {})[label] = {
            "status": status, "report": report,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
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


def night(desk, kind, parallel):
    """One repository's run: (status, report)."""
    label = "%s-nightwork" % kind
    with exclusive(desk.repo, label) as free:
        if not free:
            return "failed", "%s già in corso su questo repository" % label
        notify.notify(desk.repo, "%s partito" % label)
        try:
            done = (pr_night if kind == "pr" else issue_night)(desk, parallel)
            status, report = summary(kind, done)
        except Exception as exc:
            status, report = "failed", "interrotto: %s" % str(exc)[:200]
        record(desk.repo, label, status, report)
        notify.notify(desk.repo, "%s: %s" % (label, report))
        return status, report


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kind", required=True, choices=("pr", "issue"))
    parser.add_argument("--repo", action="append", default=[],
                        help="[host/]owner/repo, repeatable (default: the origin of the cwd)")
    parser.add_argument("--org", action="append", default=[], metavar="[HOST/]OWNER")
    parser.add_argument("--folder", metavar="DIR")
    parser.add_argument("--clones", action="append", default=[], metavar="DIR")
    parser.add_argument("--provider", choices=tuple(PROVIDERS))
    parser.add_argument("--me", help="login to work for (default: the authenticated user)")
    parser.add_argument("--agent", default="auto", choices=("auto", "claude", "codex"))
    parser.add_argument("--parallel", type=int, default=4,
                        help="jobs alive at once (default 4)")
    args = parser.parse_args()

    _, desks = prdesk.build_desks(args.repo, args.org, args.folder, args.clones,
                                  args.provider, args.me, args.kind, args.agent)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    failed = False
    try:
        for desk in desks:
            status, report = night(desk, args.kind, max(1, args.parallel))
            failed = failed or status == "failed"
            sys.stdout.write("%s %s-nightwork %s: %s\n"
                             % (desk.repo, args.kind, status, report))
            sys.stdout.flush()
    finally:
        jobs.shutdown()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
