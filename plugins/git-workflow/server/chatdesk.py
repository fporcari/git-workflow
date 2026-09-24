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

    python3 chatdesk.py detach --repo owner/repo --session chat-id
        Drop the mark: the very next click goes back to a one-shot agent.

    python3 chatdesk.py close --repo owner/repo --session chat-id --desk both
        The ear died, so the desk dies with it: terminate the servers the
        listener was watching, drop the mark, print the same `■ desk chiuso`
        line. Idempotent — a desk already gone is not an error. The chat runs
        this instead of arming a second listener.

Both result and fail heartbeat on the way out: the chat is about to run wait
again, and the seconds in between must not hand a click to a one-shot agent.
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
    and not a request key."""
    kind = record.get("kind")
    n = record.get("n")
    payload = record.get("payload") or {}
    if kind == "run":
        flow = payload.get("flow") or "pr-loop"
        parts = ["/" + flow]
        parts += [str(x) for x in payload.get("ns") or []]
        if (payload.get("batch") or 1) > 1:
            parts.append("batch=%s" % payload["batch"])
        return " ".join(parts)
    if kind == "order":
        return "/pr-loop order #%s" % n
    if kind == "analyze":
        return "/pr-analyze %s" % n
    if kind == "explain":
        return "/pr-explain %s" % n
    if kind == "issue-analyze":
        return "/issue-analyze %s" % n
    return "/%s %s" % (kind, n if n is not None else "")


def closed_record(repo):
    return {"closed": True, "repo": repo}


@contextmanager
def listener(repo, session):
    identity = hashlib.sha256(session.encode()).hexdigest()
    path = deskstate.runtime_path(repo, "listener-%s.lock" % identity)
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("session already has a running listener") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def listen(repo, session, timeout=None, out=sys.stdout, desk="pr"):
    deskstate.chat_heartbeat(repo, session, desk)
    deadline = time.time() + timeout if timeout else None
    last_beat = 0

    def bye(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, bye)
    try:
        while True:
            now = time.time()
            if now - last_beat >= HEARTBEAT_EVERY:
                deskstate.chat_heartbeat(repo, session, desk)
                last_beat = now
            record = deskstate.claim_request(repo, session, desk)
            if record:
                out.write("\u25b6 %s  \u00b7 richiesta %s\n"
                          % (command_for(record), record["key"]))
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                out.flush()
                continue
            if deskstate.desks_closed(repo, desk=desk):
                out.write("■ desk chiuso · %s\n" % repo)
                out.write(json.dumps(closed_record(repo)) + "\n")
                out.flush()
                return
            if deadline and now >= deadline:
                return
            time.sleep(LISTEN_POLL)
    finally:
        deskstate.chat_detach(repo, session)


def doze(repo, session, out=sys.stdout, desk="pr"):
    """The ear between two monitors. A monitor cannot outlive its cap, and a
    desk that died with it was dying under the user's hands: this keeps the
    chat attached with no deadline, claims nothing, and returns as soon as a
    click is queued for it — the cue to arm `listen` again, which takes it."""
    def bye(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, bye)
    last_beat = 0
    try:
        while True:
            now = time.time()
            if now - last_beat >= HEARTBEAT_EVERY:
                deskstate.chat_heartbeat(repo, session, desk)
                last_beat = now
            record = deskstate.request_waiting(repo, session, desk)
            if record:
                out.write("\u23f0 sveglia · %s\n" % command_for(record))
                out.flush()
                return
            if deskstate.desks_closed(repo, desk=desk):
                deskstate.chat_detach(repo, session)
                out.write("■ desk chiuso · %s\n" % repo)
                out.write(json.dumps(closed_record(repo)) + "\n")
                out.flush()
                return
            time.sleep(LISTEN_POLL)
    except SystemExit:
        deskstate.chat_detach(repo, session)
        raise


def wait(repo, timeout, session, desk="pr"):
    deadline = time.time() + timeout
    while True:
        deskstate.chat_heartbeat(repo, session, desk)
        record = deskstate.claim_request(repo, session, desk)
        if record:
            return record
        if deskstate.desks_closed(repo, desk=desk):
            deskstate.chat_detach(repo, session)
            return closed_record(repo)
        if time.time() >= deadline:
            return None
        time.sleep(min(HEARTBEAT_EVERY, max(0.1, deadline - time.time())))


def close(repo, session, desk="both", out=sys.stdout, grace=5):
    """A dead ear is a dead desk: SIGTERM the servers this listener covered,
    detach, and report. The server's own handler records the stop."""
    state = deskstate.load(repo)
    kinds = ("pr", "issue") if desk == "both" else (desk,)
    live = deskstate.live_desks(repo, state)
    stopped = []
    for kind in kinds:
        if kind not in live:
            continue
        pid = ((state.get("desks") or {}).get(kind) or {}).get("pid")
        try:
            os.kill(int(pid), signal.SIGTERM)
        except (OSError, TypeError, ValueError):
            continue
        stopped.append(kind)
    deadline = time.time() + grace
    survivors = stopped
    while survivors and time.time() < deadline:
        survivors = sorted(set(stopped) & set(deskstate.live_desks(repo)))
        if survivors:
            time.sleep(0.2)
    deskstate.chat_detach(repo, session)
    out.write("\u25a0 desk chiuso \u00b7 %s%s\n"
              % (repo, " \u00b7 " + " ".join(stopped) if stopped else ""))
    if survivors:
        out.write("ancora vivi dopo SIGTERM: %s\n" % " ".join(survivors))
    out.flush()
    return stopped


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
            or record.get("status") not in ("taken", "needs-input")
            or deskstate.expired(record)):
        raise ValueError("request is expired, replaced, or belongs to another session")
    return record


def _release(state, record):
    mark = state.get("working") or {}
    n = record.get("n")
    if mark and n is not None and int(n) in mark.get("ns", []):
        state.pop("working", None)
    chat = (state.get("chats") or {}).get(record["session"])
    if chat and (chat.get("busy") or {}).get("id") == record["id"]:
        chat.pop("busy", None)
        chat.update(epoch=time.time(), at=time.strftime("%H:%M:%S"))


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
                                 "detach", "close"))
    parser.add_argument("--repo", required=True)
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
                             "fail: the reason")
    args = parser.parse_args()
    if args.action == "listen":
        with listener(args.repo, args.session):
            listen(args.repo, args.session, args.timeout, desk=args.desk)
    elif args.action == "doze":
        with listener(args.repo, args.session):
            doze(args.repo, args.session, desk=args.desk)
    elif args.action == "wait":
        timeout = 540 if args.timeout is None else args.timeout
        with listener(args.repo, args.session):
            record = wait(args.repo, timeout, args.session, args.desk)
        print(json.dumps(record if record else {"idle": True}, indent=1))
    elif args.action == "result":
        if not (args.request and args.path and args.request_id):
            parser.error("result needs --request, --request-id and a JSON file")
        print(result(args.repo, args.request, args.path, args.session, args.request_id))
    elif args.action == "fail":
        if not (args.request and args.path and args.request_id):
            parser.error("fail needs --request, --request-id and a reason")
        fail(args.repo, args.request, args.path, args.session, args.request_id)
    elif args.action == "close":
        close(args.repo, args.session, args.desk)
    else:
        deskstate.chat_detach(args.repo, args.session)


if __name__ == "__main__":
    try:
        main()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
