"""Block until the desk inbox has events, print them, exit.

Run by the chat session that launched the dashboard, in a background shell:
its exit re-invokes the session, which processes the printed events (the
review-desk skill says how), truncates the inbox, and starts a new watcher.

    python3 watch_inbox.py --repo owner/repo [--interval 2] [--timeout 0]

Exit codes: 0 with the events on stdout; 3 on timeout with nothing pending.
"""

import argparse
import sys
import time

from deskstate import STATE_DIR, heartbeat_path
from inbox import inbox_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=0,
                        help="seconds; 0 waits forever")
    args = parser.parse_args()

    path = inbox_path(args.repo)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    beat = heartbeat_path(args.repo)
    started = time.time()
    while True:
        beat.touch()
        if path.exists() and path.stat().st_size:
            sys.stdout.write(path.read_text())
            return 0
        if args.timeout and time.time() - started > args.timeout:
            sys.stderr.write("no desk events within %ss\n" % args.timeout)
            return 3
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
