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

Nothing is written to the provider. The work itself is preparation.py's,
the same function the desk runs at boot: the outcome lands under
runs.<kind>-nightwork as it goes and as one feed line, and a run of the same
kind on the same repository — this command or a desk opening — refuses to
start while another is alive.
"""

import argparse
import signal
import sys

import jobs
import prdesk
import preparation
from providers import PROVIDERS


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
            status, report = preparation.prepare(desk, args.kind, max(1, args.parallel))
            failed = failed or status == "failed"
            sys.stdout.write("%s %s-nightwork %s: %s\n"
                             % (desk.repo, args.kind, status, report))
            sys.stdout.flush()
    finally:
        jobs.shutdown()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
