"""Atomic JSON files shared by threads and sibling desk processes."""

import fcntl
import json
import os
import tempfile
import threading
from contextlib import contextmanager

_locks = {}
_locks_guard = threading.Lock()


def _thread_lock(path):
    with _locks_guard:
        return _locks.setdefault(str(path), threading.RLock())


@contextmanager
def _locked(path, exclusive):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with _thread_lock(path):
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _read_unlocked(path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _write_unlocked(path, value, indent=None):
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, indent=indent)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def read(path):
    with _locked(path, exclusive=False):
        return _read_unlocked(path)


def write(path, value, indent=None):
    with _locked(path, exclusive=True):
        _write_unlocked(path, value, indent)


def update(path, mutate, indent=None):
    with _locked(path, exclusive=True):
        value = _read_unlocked(path)
        result = mutate(value)
        _write_unlocked(path, value, indent)
        return result


def remove(path):
    with _locked(path, exclusive=True):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def archive(path, destination):
    with _locked(path, exclusive=True):
        if path.exists():
            os.replace(path, destination)
