"""Disk cache with single-flight, for the provider reads.

The provider's cost is GitHub's, not ours: one `is:open is:pr involves:me`
search costs 4-7s no matter how the query is written (measured — see
tests/bench.py). So the desk stops paying it on the critical path:

  * every fetch is written to disk, so tabs, sibling desks and processes share
    one payload instead of each paying the provider;
  * a stale-but-present entry is served at once and refreshed in a
    background thread (stale-while-revalidate), so nothing blocks on a
    round trip whose answer is already almost right;
  * concurrent misses on the same key collapse into one provider call
    (single-flight), instead of N identical 5s searches.

The cache is SESSION-SCOPED, not durable: launching a desk clears it (see
reset()), because starting the desk is a request for the truth now. What it
buys is everything that happens while the desk is up — a browser reload, the
UI's polling, the two desks sharing one repo, a second tab.

Path: <tempdir>/git-workflow-<uid>/<owner>__<repo>__cache.json
"""

import threading
import time

import deskstate
import safejson

FRESH = 120          # serve without touching the provider
STALE = 3600         # serve, but refresh behind the caller
_locks = {}
_locks_guard = threading.Lock()
_inflight = {}


def cache_path(repo):
    return deskstate.runtime_path(repo, "cache.json")


def _read(repo):
    return safejson.read(cache_path(repo))


def _key_lock(repo, key):
    with _locks_guard:
        return _locks.setdefault((repo, key), threading.Lock())


def peek(repo, key):
    """The stored entry as (age_seconds, data), or None."""
    entry = _read(repo).get(key)
    if not entry:
        return None
    return (time.time() - entry["at"], entry["data"])


def store(repo, key, data):
    def mutate(blob):
        blob[key] = {"at": time.time(), "data": data}
    safejson.update(cache_path(repo), mutate)
    return data


def _refresh(repo, key, loader):
    lock = _key_lock(repo, key)
    if not lock.acquire(blocking=False):
        return None                      # somebody else is already loading
    try:
        return store(repo, key, loader())
    finally:
        lock.release()


def get(repo, key, loader, refresh=False):
    """Return (data, age, source). source is hit | stale | miss."""
    started = time.time()
    hit = peek(repo, key)
    if hit and not refresh:
        age, data = hit
        if age < FRESH:
            return data, age, "hit"
        if age < STALE:
            warm(repo, key, loader)
            return data, age, "stale"
    lock = _key_lock(repo, key)
    with lock:
        again = peek(repo, key)          # another thread may have just filled it
        if again and not refresh and again[0] < FRESH:
            return again[1], again[0], "hit"
        if refresh and again and again[0] <= time.time() - started:
            return again[1], again[0], "hit"
        return store(repo, key, loader()), 0.0, "miss"


def warm(repo, key, loader):
    """Load in the background unless this key is already loading."""
    if _inflight.get((repo, key)):
        return
    _inflight[(repo, key)] = True

    def run():
        try:
            _refresh(repo, key, loader)
        except Exception:
            pass
        finally:
            _inflight.pop((repo, key), None)

    threading.Thread(target=run, daemon=True).start()


def clear(repo):
    safejson.remove(cache_path(repo))


def newest(repo):
    """Seconds since the most recently written entry, or None if empty."""
    blob = _read(repo)
    if not blob:
        return None
    return time.time() - max(entry["at"] for entry in blob.values())


def reset(repo, grace=60):
    """Drop the cache at desk launch — but spare one a sibling desk has just
    filled. The PR desk and the issue desk start back to back on the same
    repo and share this file; wiping it seconds later would throw away the
    first one's fetch and make both pay for it again.

    Returns what it did, so the caller can say so.
    """
    age = newest(repo)
    if age is None:
        return "empty"
    if age < grace:
        return "spared"          # a sibling desk just fetched: reuse it
    clear(repo)
    return "cleared"
