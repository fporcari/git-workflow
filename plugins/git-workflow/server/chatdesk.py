"""The attached chat's side of the desk — wait for clicks, publish results.

The desk server stays detached and never talks to a conversation. When a chat
session chooses to stay attached after launching the desk, IT does the work
the buttons enqueue, and the pair of commands here is its whole contract:

    python3 chatdesk.py wait --repo owner/repo [--timeout 540]
        Heartbeat until a click arrives, claim it, print it as JSON and exit.
        Prints {"idle": true} on timeout. While a chat waits here, the server
        routes every non-triage button to the chat instead of starting a
        one-shot agent.

    python3 chatdesk.py result --repo owner/repo --request analyze:1145 out.json
        Validate the structured result exactly as a job's would be, persist
        the allowed fields, and close the request so the desk shows the
        outcome. The result file uses the same schema the one-shot agent
        would have returned.

    python3 chatdesk.py fail --repo owner/repo --request analyze:1145 "why"
        Close the request as failed, with the reason the desk shows.

    python3 chatdesk.py detach --repo owner/repo
        Drop the mark: the very next click goes back to a one-shot agent.

Both result and fail heartbeat on the way out: the chat is about to run wait
again, and the seconds in between must not hand a click to a one-shot agent.
"""

import argparse
import json
import sys
import time

import deskstate
import jobs

HEARTBEAT_EVERY = 5


def wait(repo, timeout, session):
    deadline = time.time() + timeout
    while True:
        deskstate.chat_heartbeat(repo, session)
        record = deskstate.claim_request(repo)
        if record:
            return record
        if time.time() >= deadline:
            return None
        time.sleep(min(HEARTBEAT_EVERY, max(0.1, deadline - time.time())))


def _persist(repo, record, result):
    kind = record.get("kind")
    n = record.get("n")
    payload = record.get("payload") or {}
    raw = json.dumps(result)
    if kind == "analyze":
        parsed = jobs.parse_result("chat", raw, expected_n=n)
        jobs.persist(repo, parsed,
                     payload.get("analysis_keys") or payload.get("analysis_key"))
        return parsed["propose"]
    if kind == "explain":
        jobs.persist_explanation(repo, result, n, payload.get("what_key"))
        return result["what"]
    if kind == "issue-analyze":
        jobs.persist_issue_analysis(repo, result, n)
        return result["finding"]
    if kind in ("order", "run"):
        parsed = jobs.parse_operation("chat", raw)
        flow = payload.get("flow")
        jobs.persist_operation(repo, parsed,
                               n if kind == "order" else None, flow)
        return parsed["report"]
    raise ValueError("unknown request kind %r" % kind)


def _record(repo, key):
    record = (deskstate.load(repo).get("requests") or {}).get(key)
    if not record:
        raise SystemExit("unknown request %r" % key)
    return record


def _release(repo, record):
    """Drop the working marker only when it is this request's: a one-shot
    job may be on another row at the same time."""
    mark = deskstate.working(repo)
    n = record.get("n")
    if mark and (n is None or int(n) in mark.get("ns", [])):
        deskstate.clear_working(repo)
    deskstate.chat_heartbeat(repo)


def result(repo, key, path):
    record = _record(repo, key)
    data = json.loads(sys.stdin.read() if path == "-"
                      else open(path).read())
    try:
        report = _persist(repo, record, data)
    except ValueError as exc:
        deskstate.close_request(repo, key, "failed", str(exc))
        _release(repo, record)
        raise SystemExit("invalid result: %s" % exc)
    status = data.get("status")
    if status not in ("needs-input", "failed"):
        status = "done"
    deskstate.close_request(repo, key, status, report)
    _release(repo, record)
    return report


def fail(repo, key, why):
    record = _record(repo, key)
    deskstate.close_request(repo, key, "failed", why)
    _release(repo, record)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("wait", "result", "fail", "detach"))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--timeout", type=int, default=540)
    parser.add_argument("--session", default="")
    parser.add_argument("--request", help="result: the request key to close")
    parser.add_argument("path", nargs="?",
                        help="result: the JSON file, or - for stdin; "
                             "fail: the reason")
    args = parser.parse_args()
    if args.action == "wait":
        record = wait(args.repo, args.timeout, args.session)
        print(json.dumps(record if record else {"idle": True}, indent=1))
    elif args.action == "result":
        if not (args.request and args.path):
            parser.error("result needs --request and a JSON file")
        print(result(args.repo, args.request, args.path))
    elif args.action == "fail":
        if not (args.request and args.path):
            parser.error("fail needs --request and a reason")
        fail(args.repo, args.request, args.path)
    else:
        deskstate.chat_detach(args.repo)


if __name__ == "__main__":
    main()
