"""Disk cache with single-flight, for the provider reads.

The provider's cost is GitHub's, not ours: one `is:open is:pr involves:me`
search costs 4-7s no matter how the query is written (measured — see
tests/bench.py). So the desk stops paying it on the critical path:

  * every fetch is written to disk, so a restart serves the previous
    payload immediately and revalidates behind the browser;
  * a stale-but-present entry is served at once and refreshed in a
    background thread (stale-while-revalidate), so nothing blocks on a
    round trip whose answer is already almost right;
  * concurrent misses on the same key collapse into one provider call
    (single-flight), instead of N identical 5s searches.

Path: ~/.local/state/git-workflow/<owner>__<repo>__cache.json
"""

import json
import threading
import time

from deskstate import STATE_DIR

FRESH = 120          # serve without touching the provider
STALE = 3600         # serve, but refresh behind the caller
_locks = {}
_locks_guard = threading.Lock()
_inflight = {}
# one blob holds every key, so read-modify-write must be atomic: three keys
# warming in parallel would otherwise each write back a copy that has lost
# the other two.
_file_guard = threading.Lock()


def cache_path(repo):
    return STATE_DIR / ("%s__cache.json" % repo.replace("/", "__"))


def _read(repo):
    path = cache_path(repo)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _write(repo, blob):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = cache_path(repo).with_suffix(".tmp")
    tmp.write_text(json.dumps(blob))
    tmp.replace(cache_path(repo))


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
    with _file_guard:
        blob = _read(repo)
        blob[key] = {"at": time.time(), "data": data}
        _write(repo, blob)
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
    path = cache_path(repo)
    if path.exists():
        path.unlink()
