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

The skills (pr-triage, pr-loop, issue-triage) write what only a model can
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

import os
import tempfile
import time
from pathlib import Path

import safejson

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
    return safejson.read(state_path(repo))


def reset(repo):
    """Archive the previous session's state so the desk starts empty:
    stale analyses and feed lines read as fresh data otherwise. The old
    file survives as .prev next to it. The inbox is emptied only when its
    events are stale: the two desks start back to back and each enqueues
    its own startup triage, which must survive the sibling's reset."""
    path = state_path(repo)
    safejson.archive(path, path.with_suffix(".json.prev"))
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
    safejson.write(state_path(repo), state, indent=1)


def update(repo, mutate):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return safejson.update(state_path(repo), mutate, indent=1)


REQUEST_STALE = 1800
WORKING_STALE = 900          # a run that stopped saying anything


def _mark(ns, msg, items=None):
    return {"n": ns[0], "ns": list(ns), "items": items or {}, "msg": msg,
            "epoch": time.time(), "at": time.strftime("%H:%M:%S")}


def set_working(repo, n, msg=""):
    """Mark the PR (or issue) the chat is on RIGHT NOW.

    pr-loop walks the queue and the desk is the radar: the user wants to see
    which rows are under the needle without reading the feed line by line.

    While a batch is live, marking one of ITS numbers refines that item and
    leaves the set standing — otherwise every per-item progress line would
    collapse a batch of four back to a single glowing row.
    """
    n = int(n)
    def mutate(state):
        mark = state.get("working") or {}
        if n in (mark.get("ns") or []):
            mark.setdefault("items", {})[str(n)] = msg
            mark["epoch"] = time.time()
            mark["at"] = time.strftime("%H:%M:%S")
        else:
            mark = _mark([n], msg, {str(n): msg})
        state["working"] = mark
        return mark
    return update(repo, mutate)


def set_working_batch(repo, ns, msg=""):
    """Mark the several rows a batch is working in parallel.

    One number would leave the other rows reading as idle while an agent is
    in a worktree on each of them — a desk that shows less than it knows is
    the one failure mode it exists to prevent. An empty batch is no batch:
    it clears the marker rather than leaving a headless one behind.
    """
    ns = [int(n) for n in ns]
    if not ns:
        clear_working(repo)
        return None
    def mutate(state):
        state["working"] = _mark(ns, msg)
        return state["working"]
    return update(repo, mutate)


def clear_working(repo):
    update(repo, lambda state: state.pop("working", None))


def working(repo, state=None):
    """The live marker, or None. A marker nobody updated for a quarter of an
    hour is dropped: a highlight stuck on a row after the run died is worse
    than no highlight, because it reads as work in progress.

    Always carries BOTH `n` and `ns`, whichever shape was written: the UI and
    the tests read one field, not two code paths."""
    state = state if state is not None else load(repo)
    mark = state.get("working")
    if not mark:
        return None
    if time.time() - mark.get("epoch", 0) > WORKING_STALE:
        return None
    mark = dict(mark)
    mark.setdefault("ns", [mark["n"]] if mark.get("n") is not None else [])
    mark.setdefault("items", {})
    return mark


def request(repo, key, kind, n=None, label=""):
    """Record a button press, and refuse a second one while the first is out.

    The ledger lives on the SERVER, not in the page: a click enqueues work
    for the chat, and the chat may take minutes. A lock kept in the browser
    is lost on reload, in a second tab, and by the user who presses again
    because nothing visibly happened — and every extra press is another
    event the chat has to work through.

    Returns (record, created). created is False when one was already out.
    """
    def mutate(state):
        ledger = state.setdefault("requests", {})
        existing = ledger.get(key)
        if existing and existing.get("status") == "queued":
            age = time.time() - existing.get("epoch", 0)
            if age < REQUEST_STALE:
                return existing, False
            existing["status"] = "stale"
        record = {"kind": kind, "n": n, "label": label, "status": "queued",
                  "at": time.strftime("%H:%M:%S"), "epoch": time.time()}
        ledger[key] = record
        return record, True
    return update(repo, mutate)


def close_request(repo, key, status="done", report=""):
    """The chat says how it went — this is what the desk shows in place of
    the lock."""
    def mutate(state):
        ledger = state.setdefault("requests", {})
        record = ledger.get(key) or {"kind": key.split(":")[0], "status": "queued"}
        record.update(status=status, report=report,
                      closed_at=time.strftime("%H:%M:%S"))
        ledger[key] = record
        return record
    return update(repo, mutate)


def request_key(kind, n):
    return "%s:%s" % (kind, n)


def annotate_requests(rows, state):
    """Attach every request outstanding or recently closed, keyed by kind, so
    the UI can lock the button it belongs to and show its outcome."""
    ledger = state.get("requests") or {}
    per_row = {}
    for key, record in ledger.items():
        kind, _, number = key.rpartition(":")
        if number.isdigit():
            per_row.setdefault(int(number), {})[kind] = record
    for row in rows:
        row["requests"] = per_row.get(row["n"], {})
    return rows


def add_order(repo, n, propose, draft, instruction):
    """Record the user's go-ahead on one PR. pr-loop reads pending orders as
    pre-authorized work: the click in the desk was the approval."""
    def mutate(state):
        orders = state.setdefault("orders", {})
        orders[str(n)] = {"propose": propose, "draft": draft,
                          "instruction": instruction, "status": "pending"}
        return orders[str(n)]
    return update(repo, mutate)


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
