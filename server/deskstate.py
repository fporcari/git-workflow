"""Desk state — the file through which the skills talk to the dashboard.

TWO DIRECTORIES, and the split is what each thing costs to lose.

RUNTIME (a private dir under the OS temp dir, wiped by the OS): the provider
cache, the button inbox, the watcher heartbeat, the rows export. Every one of
them is either session-scoped by design or re-readable in seconds, so none of
it belongs in the user's home, and a stale one left behind by a dead session
is noise the OS should collect.

STATE (~/.local/state/git-workflow/<owner>__<repo>.json): the analyses, the
drafts, the orders, the verified grid — what a MODEL produced by reading
diffs. That is expensive to lose and worth keeping across a relaunch, which
is what --keep-state is for.

The skills (pr-triage, pr-run, issue-triage) write what only a model can
produce: the diff-level analysis, the review drafts, the chase blocks, the
issue findings. The server merges it into the rows it serves, so the desk's
Analysis/Draft tabs show the skills' actual work instead of placeholders.

Path: ~/.local/state/git-workflow/<owner>__<repo>.json

Schema (all keys optional):
{
  "generated": "2026-08-25T12:00:00",
  "session":   "PR triage · genropy · 2026-08-25",
  "prs":    {"1152": {"analysis": "...", "draft": "...", "next": "..."}},
  "issues": {"1156": {"type": "DEFECT", "finding": "...", "size": "EASY",
                      "phase": "SINGLE-PHASE"}},
  "chase":  {"genro": "@genro — 7 PR ferme dal ...:\n#1027 #1044 ..."}
}
"""

import json
import os
import tempfile
import time
from pathlib import Path

STATE_DIR = Path.home() / ".local" / "state" / "git-workflow"
# per-user, so two accounts on one machine never share a queue
RUNTIME_DIR = Path(tempfile.gettempdir()) / ("git-workflow-%s" % os.getuid())


def runtime_dir():
    """The temp dir for everything session-scoped. 0700: the inbox carries
    the user's repo names and the cache his queue."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    return RUNTIME_DIR


def runtime_path(repo, suffix):
    return runtime_dir() / ("%s__%s" % (repo.replace("/", "__"), suffix))


def state_path(repo):
    return STATE_DIR / ("%s.json" % repo.replace("/", "__"))


def heartbeat_path(repo):
    return runtime_path(repo, "watcher.alive")


def watcher_age(repo):
    """Seconds since the inbox watcher last polled, or None if never."""
    path = heartbeat_path(repo)
    if not path.exists():
        return None
    return time.time() - path.stat().st_mtime


def load(repo):
    path = state_path(repo)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def reset(repo):
    """Archive the previous session's state so the desk starts empty:
    stale analyses and feed lines read as fresh data otherwise. The old
    file survives as .prev next to it. The inbox is emptied only when its
    events are stale: the two desks start back to back and each enqueues
    its own startup triage, which must survive the sibling's reset."""
    path = state_path(repo)
    if path.exists():
        path.replace(path.with_suffix(".json.prev"))
    inbox = runtime_path(repo, "inbox.jsonl")
    if inbox.exists() and inbox.stat().st_size and time.time() - inbox.stat().st_mtime > 60:
        inbox.write_text("")


LEGACY_SUFFIXES = ("__cache.json", "__inbox.jsonl", "__watcher.alive",
                   "__rows.json")


def sweep_legacy():
    """Remove the session files an older layout left in the user's home.

    They used to sit next to the state file; they belong under the OS temp
    dir. Left behind they are dead weight that reads like live state — a
    stale cache in ~/.local/state is exactly the thing somebody debugs for
    twenty minutes. Returns how many it removed.
    """
    if not STATE_DIR.exists():
        return 0
    gone = 0
    for path in STATE_DIR.iterdir():
        if path.is_file() and path.name.endswith(LEGACY_SUFFIXES):
            try:
                path.unlink()
                gone += 1
            except OSError:
                pass
    return gone


def save(repo, state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_path(repo).write_text(json.dumps(state, indent=1))


def add_order(repo, n, propose, draft, instruction):
    """Record the user's go-ahead on one PR. /pr-run reads pending orders as
    pre-authorized work: the click in the desk was the approval."""
    state = load(repo)
    orders = state.setdefault("orders", {})
    orders[str(n)] = {"propose": propose, "draft": draft,
                      "instruction": instruction, "status": "pending"}
    save(repo, state)
    return orders[str(n)]


def annotate_prs(rows, state):
    notes = state.get("prs") or {}
    for row in rows:
        row["skill"] = notes.get(str(row["n"]))
    return rows


def annotate_issues(rows, state):
    notes = state.get("issues") or {}
    for row in rows:
        row["skill"] = notes.get(str(row["n"]))
    return rows
