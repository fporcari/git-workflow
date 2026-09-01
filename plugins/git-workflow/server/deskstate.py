"""Desk state — the file through which the skills talk to the dashboard.

TWO DIRECTORIES, and the split is what each thing costs to lose.

RUNTIME (a private dir under the OS temp dir, wiped by the OS): the provider
cache, the rows export, and one request/result JSON per explicit one-shot job.
Every one of them is either session-scoped by design or re-readable in
seconds, so none belongs in the user's home.

STATE (~/.local/state/git-workflow/<owner>__<repo>.json): the analyses, the
drafts, the orders, and the grid published by an explicit triage. That work is
expensive to lose and worth keeping across a relaunch, which is what
--keep-state is for.

The grid and the chase blocks are written by the DESK, on the explicit
triage run. The skills (pr-triage, pr-loop, issue-triage) add per PR what
only a model can produce: the one-line `what`, the diff-level analysis, the
review draft, `conflict_kind`, the issue findings. The server merges those
into the rows it serves, so the desk shows the skills' actual work instead of
placeholders.

Path: ~/.local/state/git-workflow/<owner>__<repo>.json

Schema (all keys optional):
{
  "generated": "2026-08-25T12:00:00",
  "session":   "PR triage · genropy · 2026-08-25",
  "prs":    {"1152": {"what": "...", "what_key": "...",
                      "analysis": "...", "analysis_key": "...", "draft": "...",
                      "next": "...", "conflict_kind": "mechanical",
                      "conflict_key": "..."}},
  "issues": {"1156": {"type": "DEFECT", "finding": "...", "size": "EASY",
                      "phase": "SINGLE-PHASE"}},
  "grid":   {"generated": "...", "blocks": [{"id": "...", "rows": []}]},
  "chase":  {"genro": "@genro — 7 PR ferme dal ...:\n#1027 #1044 ..."}
}
"""

import os
import tempfile
import time
from pathlib import Path

import safejson


def _env_timeout(name, default):
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


# the budget a click gets as a one-shot job, and therefore how long the same
# click may stay silent in an attached chat before that chat is presumed dead
ANALYZE_TIMEOUT = _env_timeout("GIT_WORKFLOW_ANALYZE_TIMEOUT", 900)
OPERATION_TIMEOUT = _env_timeout("GIT_WORKFLOW_OPERATION_TIMEOUT", 3600)
READ_ONLY_KINDS = ("analyze", "explain", "issue-analyze")

STATE_DIR = Path.home() / ".local" / "state" / "git-workflow"
# per-user, so two accounts on one machine never share a queue
RUNTIME_DIR = Path(tempfile.gettempdir()) / ("git-workflow-%s" % os.getuid())


def runtime_dir():
    """The private temp dir for session-scoped provider and job data."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    return RUNTIME_DIR


def runtime_path(repo, suffix):
    return runtime_dir() / ("%s__%s" % (repo.replace("/", "__"), suffix))


def state_path(repo):
    return STATE_DIR / ("%s.json" % repo.replace("/", "__"))


def load(repo):
    return safejson.read(state_path(repo))


DURABLE = ("grid", "chase", "prs", "issues", "runs")


def reset(repo):
    """Archive the previous session's ephemera so the desk starts clean:
    old feed lines, requests and working markers read as fresh data
    otherwise. The old file survives as .prev next to it.

    The TRIAGE is durable and stays: the grid re-verdicts itself on every
    read (the engine recomputes; a relaunch cannot make it lie) and the
    model's per-PR/per-issue notes are dated, so what has expired shows as
    expired instead of being thrown away with the session. So is the last
    report of each run: a relaunched desk is the commonest way to come back
    to a loop that ended in needs-input. `--keep-state`
    keeps everything, ephemera included."""
    path = state_path(repo)
    kept = {key: value for key, value in safejson.read(path).items()
            if key in DURABLE}
    safejson.archive(path, path.with_suffix(".json.prev"))
    if kept:
        save(repo, kept)


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
CHAT_STALE = 45              # heartbeat older than this = no chat attached
CHAT_CLAIM_GRACE = 20        # a live wait loop claims within one heartbeat


def busy_ttl(kind):
    """How long a claimed request keeps the chat "attached" and its button
    locked: the same budget the click would have had as a one-shot job. A
    chat silent for longer than the job it replaces is presumed dead, and
    the record goes stale so the user can press again."""
    return ANALYZE_TIMEOUT if kind in READ_ONLY_KINDS else OPERATION_TIMEOUT


def expired(record, now=None):
    """True when an open record has outlived its budget: a queued click
    after REQUEST_STALE, a taken one after the busy TTL of its kind."""
    status = record.get("status")
    now = now or time.time()
    if status == "taken":
        return now - record.get("taken_epoch", record.get("epoch", 0)) > busy_ttl(
            record.get("kind"))
    if status in ("queued", "preparing"):
        return now - record.get("epoch", 0) > REQUEST_STALE
    return False


def effective(record):
    """The record as the desk should show it: an expired lock reads as
    `stale`, which the page already renders as "no outcome, press again"."""
    if expired(record):
        return dict(record, status="stale")
    return record


def chat_heartbeat(repo, session=""):
    """The attached chat says it is alive and waiting for desk requests."""
    def mutate(state):
        mark = state.setdefault("chat", {})
        mark.update(session=session or mark.get("session", ""),
                    epoch=time.time(), at=time.strftime("%H:%M:%S"))
        return dict(mark)
    return update(repo, mutate)


def chat_detach(repo):
    update(repo, lambda state: state.pop("chat", None))


def chat_attached(repo, state=None):
    """The chat mark, or None. Alive on a fresh heartbeat, and also while a
    claimed request is still open: the chat stops heartbeating the moment it
    starts working, and treating that silence as a dead chat would route the
    next click to a one-shot agent behind the user's back."""
    state = state if state is not None else load(repo)
    mark = state.get("chat")
    if not mark:
        return None
    if time.time() - mark.get("epoch", 0) <= CHAT_STALE:
        return dict(mark)
    busy = mark.get("busy") or {}
    record = (state.get("requests") or {}).get(busy.get("key") or "")
    if record and record.get("status") == "taken" and not expired(record):
        return dict(mark)
    return None


def chat_listening(repo, state=None):
    """The chat that is heartbeating RIGHT NOW, or None.

    `chat_attached` deliberately also covers the minutes a claimed request
    takes: right for the chip, wrong for routing. A chat that died mid-request
    keeps a `taken` record nobody expires, and every later click would be
    enqueued for a conversation that will never read it — the desk goes silent
    with no job to show. Only a fresh heartbeat may take a NEW click.
    """
    state = state if state is not None else load(repo)
    mark = state.get("chat") or {}
    if time.time() - mark.get("epoch", 0) <= CHAT_STALE:
        return dict(mark)
    return None


def reclaim_request(repo, key):
    """Give back a click the chat never took, so a one-shot agent can run it."""
    def mutate(state):
        record = (state.get("requests") or {}).get(key)
        if (record and record.get("status") == "queued"
                and time.time() - record.get("epoch", 0) > CHAT_CLAIM_GRACE):
            record["status"] = "stale"
            return True
        return False
    return update(repo, mutate)


def claim_request(repo):
    """Hand the oldest queued chat request to the waiting chat, exactly once.

    The claim flips the record to `taken` so a second wait loop (or a reload
    of the first) cannot execute the same click twice, and marks the chat
    busy so the attachment survives the minutes the work takes."""
    def mutate(state):
        ledger = state.get("requests") or {}
        queued = [(key, record) for key, record in ledger.items()
                  if record.get("status") == "queued"
                  and record.get("via") == "chat"]
        if not queued:
            return None
        key, record = min(queued, key=lambda item: item[1].get("epoch", 0))
        taken_epoch = time.time()
        record.update(status="taken", taken_at=time.strftime("%H:%M:%S"),
                      taken_epoch=taken_epoch)
        state.setdefault("chat", {})["busy"] = {"key": key,
                                                "epoch": taken_epoch}
        return dict(record, key=key)
    return update(repo, mutate)


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


def request(repo, key, kind, n=None, label="", via="agent", payload=None,
            status="queued"):
    """Record a button press, and refuse a second one while the first is out.

    The ledger lives on the SERVER, not in the page: a click enqueues work
    for the chat, and the chat may take minutes. A lock kept in the browser
    is lost on reload, in a second tab, and by the user who presses again
    because nothing visibly happened — and every extra press is another
    event the chat has to work through.

    `preparing` is a click the server still owes a payload to: the chat may
    not claim it yet, and `ready_request` flips it to `queued`.

    Returns (record, created). created is False when one was already out.
    """
    def mutate(state):
        ledger = state.setdefault("requests", {})
        existing = ledger.get(key)
        if existing and existing.get("status") in ("queued", "taken",
                                                    "preparing"):
            if not expired(existing):
                return existing, False
            existing["status"] = "stale"
        record = {"kind": kind, "n": n, "label": label, "status": status,
                  "via": via, "payload": payload or {},
                  "at": time.strftime("%H:%M:%S"), "epoch": time.time()}
        ledger[key] = record
        return record, True
    return update(repo, mutate)


def ready_request(repo, key, payload):
    """The payload a click was waiting for is in: the chat may claim it now.
    The clock restarts here, so the claim grace counts from readiness."""
    def mutate(state):
        record = (state.get("requests") or {}).get(key)
        if not record or record.get("status") != "preparing":
            return None
        record.update(status="queued", payload=payload or {},
                      epoch=time.time())
        return dict(record)
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


def request_provider_refresh(repo):
    """Tell every open desk tab to force one fresh provider snapshot."""
    def mutate(state):
        state["provider_refresh"] = {
            "token": str(time.time_ns()), "at": time.strftime("%H:%M:%S")}
        return state["provider_refresh"]
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
            per_row.setdefault(int(number), {})[kind] = effective(record)
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
