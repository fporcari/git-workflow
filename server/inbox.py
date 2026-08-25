"""Desk inbox — how the dashboard's buttons reach the chat that launched it.

In chat mode the server never spawns headless runs: a button click appends
one JSON line here, and the launching Claude session — parked on
watch_inbox.py in a background shell — wakes up, reads the events, acts on
them with its full context (CLAUDE.md, skills, permissions), writes the
outcome to the desk state file, and truncates the inbox.
"""

import json
import time

from deskstate import STATE_DIR


def inbox_path(repo):
    return STATE_DIR / ("%s__inbox.jsonl" % repo.replace("/", "__"))


def push(repo, event):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    event = dict(event, at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    with inbox_path(repo).open("a") as f:
        f.write(json.dumps(event) + "\n")
    return event
