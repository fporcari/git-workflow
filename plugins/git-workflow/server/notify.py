"""Append a progress notification to the desk feed, and close desk requests.

Used by the chat session while it works, so the dashboard shows what is
happening step by step:

    python3 notify.py --repo owner/repo [--pr N] "message"
    python3 notify.py --repo owner/repo --pr N --working "sto leggendo il diff"
    python3 notify.py --repo owner/repo --batch 1145,1128 --working "fix in parallelo"
    python3 notify.py --repo owner/repo --idle "coda svuotata"
"""

import argparse
import time

import deskstate

FEED_MAX = 100


def notify(repo, msg, pr=None):
    def mutate(state):
        feed = state.setdefault("feed", [])
        feed.append({"at": time.strftime("%H:%M:%S"), "msg": msg, "pr": pr})
        del feed[:-FEED_MAX]
    deskstate.update(repo, mutate)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", type=int)
    parser.add_argument("--batch", help="with --working: the numbers a batch is "
                                        "working in parallel, comma separated")
    parser.add_argument("--pong", help="answer a desk ping: store the token so the UI sees the roundtrip")
    parser.add_argument("--done", metavar="KEY",
                        help="close a desk request (e.g. analyze:1145): the "
                             "button's lock becomes this outcome")
    parser.add_argument("--failed", metavar="KEY",
                        help="close a desk request as failed")
    parser.add_argument("--working", action="store_true",
                        help="with --pr: the desk highlights that row as the "
                             "one being worked right now")
    parser.add_argument("--idle", action="store_true",
                        help="nothing is being worked: drop the highlight")
    parser.add_argument("msg")
    args = parser.parse_args()
    if args.pong:
        deskstate.update(args.repo, lambda state: state.update(pong=args.pong))
    if args.done:
        deskstate.close_request(args.repo, args.done, "done", args.msg)
    if args.failed:
        deskstate.close_request(args.repo, args.failed, "failed", args.msg)
    if args.working and args.batch:
        ns = [int(x) for x in args.batch.replace("#", "").split(",") if x.strip()]
        deskstate.set_working_batch(args.repo, ns, args.msg)
    elif args.working and args.pr:
        deskstate.set_working(args.repo, args.pr, args.msg)
    if args.idle or (args.done or args.failed) and not args.working:
        deskstate.clear_working(args.repo)
    notify(args.repo, args.msg, args.pr)


if __name__ == "__main__":
    main()
