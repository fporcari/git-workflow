"""One deterministic Lane A pass for pr-loop — the queue read fresh, the
verdict engine run on it, nothing published.

pr-loop used to re-derive the A1/A3 gates by hand: one `gh pr view` plus one
reviews read PER PR PER PASS, each paid as its own round trip. The engine
that answers the same question in 0.07 ms already existed (verdicts.py);
this script is its command-line door.

    python3 lane_check.py [--repo owner/repo] [--me login] [--ns 1145,1128]
                          [--provider github|forgejo|fixture] [--cached]

Reads the provider through the shared disk cache (cache.py), so a running
desk and this script pay for one search, not two. Default is a fresh read —
a loop pass exists to see what its own actions just changed; `--cached`
serves the warm entry instead (a re-read within seconds, offline work).

Read-only everywhere: it never publishes `grid` or `chase` — those are the
desk's, written on the user's explicit triage press — and it never posts to
the provider. What it CANNOT settle stays `asks`: an A2 needs the review
read, and `conflict_kind` on a DIRTY branch is written by a model that
inspected the diff (deskstate `prs.<n>`), which this script only consumes.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import verdicts  # noqa: E402
from prdesk import Desk, detect_repo  # noqa: E402
from providers import get_provider  # noqa: E402

ROW_FIELDS = ("n", "title", "author", "created", "draft", "base", "head",
              "merge", "decision", "req", "unresolved", "threads",
              "assignees", "incomplete", "conflict_kind", "gate",
              "todo", "state", "autorun", "waiting_on", "triage_key")

LANES = ("A1", "A2", "A3", "asks", "yours", "-")


def check(provider, repo, me, ns=None, refresh=True):
    desk = Desk(provider, repo, me, cwd=str(Path.cwd()))
    rows, _, states, gates = desk._queue_facts(refresh=refresh,
                                               complete_gates=True)
    verdicts.decorate(rows, me, gates)
    wanted = set(ns or [])
    if wanted:
        rows = [row for row in rows if row["n"] in wanted]
    lanes = {lane: [] for lane in LANES}
    for row in rows:
        lanes.setdefault(row.get("autorun") or "-", []).append(row["n"])
    return {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "repo": repo, "me": me,
            "mergestate_pending": not states,
            "not_in_queue": sorted(wanted - {row["n"] for row in rows}),
            "lanes": lanes,
            "fixed_point": not lanes["A1"] and not lanes["A3"],
            "rows": [{key: row.get(key) for key in ROW_FIELDS}
                     for row in rows]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="owner/repo (default: origin of the cwd)")
    parser.add_argument("--provider", default="github",
                        choices=("github", "forgejo", "fixture"))
    parser.add_argument("--me", help="login to check for (default: the authenticated user)")
    parser.add_argument("--ns", help="comma-separated working set; anything else is ignored")
    parser.add_argument("--cached", action="store_true",
                        help="serve the warm cache entry instead of re-reading the provider")
    args = parser.parse_args()

    provider = get_provider(args.provider)
    repo = args.repo or detect_repo()
    me = args.me or provider.whoami()
    ns = [int(n.strip().lstrip("#")) for n in args.ns.split(",") if n.strip()] \
        if args.ns else None
    sys.stdout.write(json.dumps(
        check(provider, repo, me, ns, refresh=not args.cached), indent=1) + "\n")


if __name__ == "__main__":
    main()
