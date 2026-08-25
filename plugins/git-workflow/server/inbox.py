"""Desk inbox — how the dashboard's buttons reach the chat that launched it.

In chat mode the server never spawns headless runs: a button click appends
one JSON line here, and the launching agent session — parked on
watch_inbox.py in a background shell — wakes up, reads the events, acts on
them with its full context (CLAUDE.md, skills, permissions), writes the
outcome to the desk state file, and truncates the inbox.
"""

import argparse
import json
import time

import deskstate


def inbox_path(repo):
    return deskstate.runtime_path(repo, "inbox.jsonl")


def truncate(repo):
    """Called by the chat session once it has processed the events. Kept here
    so nothing has to hardcode the path — it lives under the OS temp dir."""
    path = inbox_path(repo)
    if path.exists():
        path.write_text("")
    return path


def push(repo, event):
    event = dict(event, at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    with inbox_path(repo).open("a") as f:
        f.write(json.dumps(event) + "\n")
    return event


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--truncate", action="store_true",
                        help="empty the inbox: what the chat session does after "
                             "processing the events it was handed")
    parser.add_argument("--path", action="store_true", help="print the path")
    args = parser.parse_args()
    if args.truncate:
        print(truncate(args.repo))
    elif args.path:
        print(inbox_path(args.repo))
    else:
        parser.error("nothing to do: pass --truncate or --path")


if __name__ == "__main__":
    main()
