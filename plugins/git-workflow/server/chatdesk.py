"""The attached chat's side of the desk — wait for clicks, publish results.

The desk server stays detached and never talks to a conversation. When a chat
session chooses to stay attached after launching the desk, IT does the work
the buttons enqueue, and the pair of commands here is its whole contract:

    python3 chatdesk.py listen --repo owner/repo --session chat-id --desk pr
        The attached chat's ear, for hosts with a background monitor (Claude
        Code's Monitor tool): heartbeat forever, claim each click as it comes
        and print it as TWO lines — the command it stands for, then the
        record as JSON. Ends by itself only when the selected desk
        is gone (both for --desk both) (stop button, idle exit, kill), with a `■ desk chiuso` line
        and a {"closed": true} record — the chat's cue to mark its title
        closed; killed with the monitor, it detaches on the way out. Because
        it keeps heartbeating while the chat works, later clicks queue
        behind the conversation instead of slipping to a one-shot agent.

    python3 chatdesk.py doze --repo owner/repo --session chat-id --desk pr
        The ear between two monitors, for Claude Code, whose Monitor dies at
        30 minutes: run as a background shell when the monitor expires, it
        heartbeats with no deadline, claims nothing, and exits with a
        `⏰ sveglia` line as soon as a click is queued for this chat — the
        chat then arms `listen` again, which claims it. Exits with
        `■ desk chiuso` once the selected desk is gone.

    python3 chatdesk.py wait --repo owner/repo --session chat-id --desk pr [--timeout 540]
        The same ear for hosts without a monitor: heartbeat until a click
        arrives, claim it, print it as JSON and exit. Prints {"idle": true}
        on timeout and {"closed": true} once the selected desk is no longer running. While a chat waits here, the server routes every
        non-triage button to the chat instead of starting a one-shot agent.

    python3 chatdesk.py result --repo owner/repo --session chat-id
        --request analyze:1145 --request-id click-id out.json
        Validate the structured result exactly as a job's would be, persist
        the allowed fields, and close the request so the desk shows the
        outcome. The result file uses the same schema the one-shot agent
        would have returned.

    python3 chatdesk.py fail --repo owner/repo --session chat-id
        --request analyze:1145 --request-id click-id "why"
        Close the request as failed, with the reason the desk shows.

    python3 chatdesk.py park --repo owner/repo --session chat-id
        --request run:pr-loop --request-id click-id "what runs in background"
        The work went to background agents: the request reads `running`, its
        button stays locked, and result or fail close it later — but the chat
        is free, so the next click is claimed instead of queuing behind it.

    python3 chatdesk.py detach --repo owner/repo --session chat-id
        Drop the mark: the very next click goes back to a one-shot agent.

    python3 chatdesk.py close --repo owner/repo --session chat-id --desk both
        The ear died, so the desk dies with it: terminate the servers the
        listener was watching, drop the mark, print the same `■ desk chiuso`
        line. Idempotent — a desk already gone is not an error. The chat runs
        this instead of arming a second listener.

Both result and fail heartbeat on the way out: the chat is about to run wait
again, and the seconds in between must not hand a click to a one-shot agent.

A desk over several repositories (prdesk.py --org, a folder of clones)
registers its scope by name: listen, doze, wait and close then take
`--scope <name>` instead of `--repo`, heartbeat every member, and hand back
each click tagged with the `repo` it belongs to and the `cwd` of its clone
(None when there is none). result, fail and detach keep `--repo`: the one the
click named.
"""

import argparse
import fcntl
import hashlib
import json
import os
import signal
import sys
import time
from contextlib import contextmanager

import deskstate
import jobs

HEARTBEAT_EVERY = 5
LISTEN_POLL = 1


def command_for(record):
    """The click as the command the user would have typed: what the chat
    shows before doing it, so a reader sees `/pr-loop 1099 1055 batch=4`
    and not a request key. A click claimed off a scope names its
    repository, the one the command must run against."""
    kind = record.get("kind")
    n = record.get("n")
    payload = record.get("payload") or {}
    if kind == "run":
        flow = payload.get("flow") or "pr-loop"
        parts = ["/" + flow]
        parts += [str(x) for x in payload.get("ns") or []]
        if (payload.get("batch") or 1) > 1:
            parts.append("batch=%s" % payload["batch"])
        command = " ".join(parts)
    elif kind == "order":
        command = "/pr-loop order #%s" % n
    elif kind == "analyze":
        command = "/pr-analyze %s" % n
    elif kind == "explain":
        command = "/pr-explain %s" % n
    elif kind == "issue-analyze":
        command = "/issue-analyze %s" % n
    else:
        command = "/%s %s" % (kind, n if n is not None else "")
    return command + (" --repo %s" % record["repo"] if record.get("repo") else "")


def _repos(repo):
    """One repository, or the members of a scope (names, or the registry's
    {repo, cwd} records): every function below takes either, and a scope's
    clicks come back tagged with their repo."""
    if isinstance(repo, str):
        return [repo]
    return [m["repo"] if isinstance(m, dict) else m for m in repo]


def _name(repo):
    return repo if isinstance(repo, str) else " + ".join(_repos(repo))


def closed_record(repo):
    return {"closed": True, "repo": _name(repo)}


@contextmanager
def listener(repo, session):
    identity = hashlib.sha256(session.encode()).hexdigest()
    path = deskstate.runtime_path(_repos(repo)[0], "listener-%s.lock" % identity)
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("session already has a running listener") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _heartbeat(repos, session, desk):
    for repo in repos:
        deskstate.chat_heartbeat(repo, session, desk)


def _detach(repos, session):
    for repo in repos:
        deskstate.chat_detach(repo, session)


def _tagged(record, repo, tag):
    """A click claimed off a scope says its repository and the clone the
    command must run in (None: read only, no checkout)."""
    if not tag:
        return record
    return dict(record, repo=repo, cwd=(tag.get(repo) or {}).get("cwd"))


def _claim(repos, session, desk, tag):
    for repo in repos:
        record = deskstate.claim_request(repo, session, desk)
        if record:
            return _tagged(record, repo, tag)
    return None


def _waiting(repos, session, desk, tag):
    for repo in repos:
        record = deskstate.request_waiting(repo, session, desk)
        if record:
            return _tagged(record, repo, tag)
    return None


def _tags(repo):
    """{} for one repository; for a scope, its members by repo."""
    if isinstance(repo, str):
        return {}
    return {(m["repo"] if isinstance(m, dict) else m): (m if isinstance(m, dict) else {"repo": m})
            for m in repo}


def _closed(repos, desk):
    return all(deskstate.desks_closed(repo, desk=desk) for repo in repos)


def listen(repo, session, timeout=None, out=sys.stdout, desk="pr"):
    repos, tag = _repos(repo), _tags(repo)
    _heartbeat(repos, session, desk)
    deadline = time.time() + timeout if timeout else None
    last_beat = 0

    def bye(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, bye)
    try:
        while True:
            now = time.time()
            if now - last_beat >= HEARTBEAT_EVERY:
                _heartbeat(repos, session, desk)
                last_beat = now
            record = _claim(repos, session, desk, tag)
            if record:
                out.write("▶ %s  · richiesta %s\n"
                          % (command_for(record), record["key"]))
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                out.flush()
                continue
            if _closed(repos, desk):
                out.write("■ desk chiuso · %s\n" % _name(repo))
                out.write(json.dumps(closed_record(repo)) + "\n")
                out.flush()
                return
            if deadline and now >= deadline:
                return
            time.sleep(LISTEN_POLL)
    finally:
        _detach(repos, session)


def doze(repo, session, out=sys.stdout, desk="pr"):
    """The ear between two monitors. A monitor cannot outlive its cap, and a
    desk that died with it was dying under the user's hands: this keeps the
    chat attached with no deadline, claims nothing, and returns as soon as a
    click is queued for it — the cue to arm `listen` again, which takes it."""
    repos, tag = _repos(repo), _tags(repo)

    def bye(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, bye)
    last_beat = 0
    try:
        while True:
            now = time.time()
            if now - last_beat >= HEARTBEAT_EVERY:
                _heartbeat(repos, session, desk)
                last_beat = now
            record = _waiting(repos, session, desk, tag)
            if record:
                out.write("⏰ sveglia · %s\n" % command_for(record))
                out.flush()
                return
            if _closed(repos, desk):
                _detach(repos, session)
                out.write("■ desk chiuso · %s\n" % _name(repo))
                out.write(json.dumps(closed_record(repo)) + "\n")
                out.flush()
                return
            time.sleep(LISTEN_POLL)
    except SystemExit:
        _detach(repos, session)
        raise


def wait(repo, timeout, session, desk="pr"):
    repos, tag = _repos(repo), _tags(repo)
    deadline = time.time() + timeout
    while True:
        _heartbeat(repos, session, desk)
        record = _claim(repos, session, desk, tag)
        if record:
            return record
        if _closed(repos, desk):
            _detach(repos, session)
            return closed_record(repo)
        if time.time() >= deadline:
            return None
        time.sleep(min(HEARTBEAT_EVERY, max(0.1, deadline - time.time())))


def close(repo, session, desk="both", out=sys.stdout, grace=5):
    """A dead ear is a dead desk: SIGTERM the servers this listener covered,
    detach, and report. The server's own handler records the stop. One
    server behind a whole scope is registered in every member: it is
    stopped once, and kept when another live chat listens to any of them."""
    repos = _repos(repo)
    kinds = ("pr", "issue") if desk == "both" else (desk,)
    targets, kept = {}, []
    for member in repos:
        state = deskstate.load(member)
        live = deskstate.live_desks(member, state)
        for kind in kinds:
            if kind not in live:
                continue
            # a desk another live chat listens to is that chat's, not ours to kill
            owner = deskstate.chat_attached(member, state, desk=kind)
            pid = ((state.get("desks") or {}).get(kind) or {}).get("pid")
            if owner and owner.get("session") != session:
                kept.append((kind, owner["session"], pid))
                continue
            targets.setdefault(pid, kind)
    stopped = []
    for pid, kind in targets.items():
        if any(pid == held for _, _, held in kept):
            continue
        try:
            os.kill(int(pid), signal.SIGTERM)
        except (OSError, TypeError, ValueError):
            continue
        stopped.append(kind)
    deadline = time.time() + grace

    def still_up():
        return sorted({kind for member in repos for kind in deskstate.live_desks(member)}
                      & set(stopped))
    survivors = still_up() if stopped else []
    while survivors and time.time() < deadline:
        time.sleep(0.2)
        survivors = still_up()
    _detach(repos, session)
    for kind, owner in dict.fromkeys((k, o) for k, o, _ in kept):
        out.write("%s desk lasciato aperto: lo ascolta la sessione %s\n" % (kind, owner))
    out.write("■ desk chiuso · %s%s\n"
              % (_name(repo), " · " + " ".join(sorted(set(stopped))) if stopped else ""))
    if survivors:
        out.write("ancora vivi dopo SIGTERM: %s\n" % " ".join(survivors))
    out.flush()
    return sorted(set(stopped))


def _persist(repo, record, result, state):
    kind = record.get("kind")
    n = record.get("n")
    payload = record.get("payload") or {}
    raw = json.dumps(result)
    if kind == "analyze":
        parsed = jobs.parse_result("chat", raw, expected_n=n)
        jobs.persist(repo, parsed,
                     payload.get("analysis_keys") or payload.get("analysis_key"),
                     state=state)
        return parsed["propose"]
    if kind == "explain":
        jobs.persist_explanation(repo, result, n, payload.get("what_key"), state=state)
        return result["what"]
    if kind == "issue-analyze":
        jobs.persist_issue_analysis(repo, result, n, state=state)
        return result["finding"]
    if kind in ("order", "run"):
        parsed = jobs.parse_operation("chat", raw)
        flow = payload.get("flow")
        jobs.persist_operation(repo, parsed,
                               n if kind == "order" else None, flow, state=state)
        return parsed["report"]
    raise ValueError("unknown request kind %r" % kind)


def _record(state, key, session, request_id):
    record = (state.get("requests") or {}).get(key) or {}
    if (record.get("via") != "chat-session" or record.get("session") != session
            or record.get("id") != request_id
            or record.get("status") not in ("taken", "needs-input", "running")
            or deskstate.expired(record)):
        raise ValueError("request is expired, replaced, or belongs to another session")
    return record


def _release(state, record):
    mark = state.get("working") or {}
    n = record.get("n")
    if mark and n is not None and int(n) in mark.get("ns", []):
        state.pop("working", None)
    _free_chat(state, record)


def _free_chat(state, record):
    chat = (state.get("chats") or {}).get(record["session"])
    if chat and (chat.get("busy") or {}).get("id") == record["id"]:
        chat.pop("busy", None)
        chat.update(epoch=time.time(), at=time.strftime("%H:%M:%S"))


def park(repo, key, note, session, request_id):
    """The click's work went to background agents. Its button stays locked
    and the request stays this chat's to close with result or fail, but the
    chat is free: the next click no longer queues behind a loop it is only
    waiting on."""
    def mutate(state):
        record = _record(state, key, session, request_id)
        record.update(status="running", report=note,
                      running_at=time.strftime("%H:%M:%S"),
                      running_epoch=time.time())
        _free_chat(state, record)
        return dict(record, key=key)
    return deskstate.update(repo, mutate)


def result(repo, key, path, session, request_id):
    try:
        with open(path) if path != "-" else sys.stdin as stream:
            data = json.load(stream)
    except (OSError, ValueError) as exc:
        fail(repo, key, "invalid result: %s" % exc, session, request_id)
        raise ValueError("invalid result: %s" % exc) from exc
    def mutate(state):
        record = _record(state, key, session, request_id)
        try:
            report = _persist(repo, record, data, state)
        except ValueError as exc:
            deskstate.close_request(repo, key, "failed", str(exc), state=state)
            _release(state, record)
            return None, str(exc)
        status = data.get("status")
        if status not in ("needs-input", "failed"):
            status = "done"
        deskstate.close_request(repo, key, status, report, state=state)
        _release(state, record)
        return report, None
    report, error = deskstate.update(repo, mutate)
    if error:
        raise ValueError("invalid result: %s" % error)
    return report


def fail(repo, key, why, session, request_id):
    def mutate(state):
        record = _record(state, key, session, request_id)
        deskstate.close_request(repo, key, "failed", why, state=state)
        _release(state, record)
    deskstate.update(repo, mutate)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",
                        choices=("listen", "doze", "wait", "result", "fail",
                                 "park", "detach", "close"))
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--repo", help="owner/repo: one repository's desk, or the member "
                                      "a scope click named (result, fail, detach)")
    where.add_argument("--scope", help="listen, doze, wait, close: every repository of "
                                       "the desk opened on this scope")
    parser.add_argument("--timeout", type=int, default=None,
                        help="wait: seconds before {\"idle\": true} "
                             "(default 540); listen: stop after this many "
                             "seconds (default: never)")
    parser.add_argument("--session", required=True)
    parser.add_argument("--desk", choices=("pr", "issue", "both"), default="pr")
    parser.add_argument("--request-id")
    parser.add_argument("--request", help="result: the request key to close")
    parser.add_argument("path", nargs="?",
                        help="result: the JSON file, or - for stdin; "
                             "fail: the reason; park: what runs in background")
    args = parser.parse_args()
    if args.scope and args.action in ("result", "fail", "park", "detach"):
        parser.error("%s needs --repo, the repository the click named" % args.action)
    ear = deskstate.scope_members(args.scope) if args.scope else args.repo
    if args.action == "listen":
        with listener(ear, args.session):
            listen(ear, args.session, args.timeout, desk=args.desk)
    elif args.action == "doze":
        with listener(ear, args.session):
            doze(ear, args.session, desk=args.desk)
    elif args.action == "wait":
        timeout = 540 if args.timeout is None else args.timeout
        with listener(ear, args.session):
            record = wait(ear, timeout, args.session, args.desk)
        print(json.dumps(record if record else {"idle": True}, indent=1))
    elif args.action == "result":
        if not (args.request and args.path and args.request_id):
            parser.error("result needs --request, --request-id and a JSON file")
        print(result(args.repo, args.request, args.path, args.session, args.request_id))
    elif args.action == "fail":
        if not (args.request and args.path and args.request_id):
            parser.error("fail needs --request, --request-id and a reason")
        fail(args.repo, args.request, args.path, args.session, args.request_id)
    elif args.action == "park":
        if not (args.request and args.path and args.request_id):
            parser.error("park needs --request, --request-id and a note")
        park(args.repo, args.request, args.path, args.session, args.request_id)
    elif args.action == "close":
        close(ear, args.session, args.desk)
    else:
        deskstate.chat_detach(args.repo, args.session)


if __name__ == "__main__":
    try:
        main()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
