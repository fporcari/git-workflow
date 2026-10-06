"""Prepare a fixture desk the way the morning does, with the fake agent:
both kinds, through the real jobs and persistence, so the UI tests open on
a desk whose steps are filled.

    PATH=<dir with fake_claude.py as claude>:$PATH \
      python3 tests/seed_ui.py --repo desk-tests/ui --me genro
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import jobs         # noqa: E402
import prdesk       # noqa: E402
import preparation  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--me", required=True)
    args = parser.parse_args()
    preparation.POLL = 0.1
    _, desks = prdesk.build_desks([args.repo], provider="fixture", me=args.me,
                                  agent="claude")
    try:
        for kind in preparation.KINDS:
            for desk in desks:
                status, report = preparation.prepare(desk, kind, 8)
                sys.stdout.write("%s %s: %s\n" % (kind, status, report))
    finally:
        jobs.shutdown()


if __name__ == "__main__":
    main()
