"""Append a progress notification to the desk feed.

Used by the chat session while it works, so the dashboard shows what is
happening step by step:

    python3 notify.py --repo owner/repo [--pr N] "message"
"""

import argparse
import time

import deskstate

FEED_MAX = 100


def notify(repo, msg, pr=None):
    state = deskstate.load(repo)
    feed = state.setdefault("feed", [])
    feed.append({"at": time.strftime("%H:%M:%S"), "msg": msg, "pr": pr})
    del feed[:-FEED_MAX]
    deskstate.save(repo, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", type=int)
    parser.add_argument("msg")
    args = parser.parse_args()
    notify(args.repo, args.msg, args.pr)


if __name__ == "__main__":
    main()
