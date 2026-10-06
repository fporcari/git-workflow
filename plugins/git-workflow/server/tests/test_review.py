"""Review actions from the desk: approve, request changes, skip to tomorrow.

A review is public and has no undo, so a click carries exactly the rows it
showed, on the head it showed, with the text it showed, and runs in the
attached chat with its command echoed — never without one, never on the
user's own PR, never on a PR that moved."""

import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import date
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cache            # noqa: E402
import chatdesk         # noqa: E402
import deskstate        # noqa: E402
import jobs             # noqa: E402
import prdesk           # noqa: E402
from providers import get_provider  # noqa: E402

REPO = "genropy/genropy"
ME = "genro"
SESSION = "review-chat"
_SAVED = {}


def setUpModule():
    home = tempfile.mkdtemp(prefix="review-")
    _SAVED.update(home=os.environ.get("HOME"), state=deskstate.STATE_DIR,
                  runtime=deskstate.RUNTIME_DIR)
    os.environ["HOME"] = home
    deskstate.STATE_DIR = Path(home) / "state"
    deskstate.RUNTIME_DIR = Path(home) / "runtime"


def tearDownModule():
    if _SAVED.get("home") is not None:
        os.environ["HOME"] = _SAVED["home"]
    deskstate.STATE_DIR = _SAVED["state"]
    deskstate.RUNTIME_DIR = _SAVED["runtime"]


class ReviewClicks(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cache.clear(REPO)
        cls.desk = prdesk.Desk(get_provider("fixture"), REPO, ME, str(ROOT))
        for row in cls.desk.provider.data["rows"]:
            row["head"] = "h%d" % row["n"]
        prdesk.Handler.desk = cls.desk
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), prdesk.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        deskstate.save(REPO, {})
        self.targets = sorted(self.desk.review_targets())

    def attach(self):
        deskstate.chat_heartbeat(REPO, SESSION, "pr")

    def post(self, body):
        req = Request("http://127.0.0.1:%s/api/review" % self.port,
                      data=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json",
                               "X-Git-Workflow-Token": self.desk.write_token})
        try:
            with urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def items(self, *ns, **extra):
        return [dict({"n": n, "head": "h%d" % n}, **extra) for n in ns]

    def test_approve_goes_to_the_attached_chat_with_rows_heads_and_texts(self):
        self.attach()
        a, b = self.targets[:2]
        status, payload = self.post({"event": "approve", "items": self.items(a, b)})
        self.assertEqual((status, payload["via"], payload["request"]),
                         (202, "chat", "review:approve"))
        record = deskstate.load(REPO)["requests"]["review:approve"]
        self.assertEqual((record["kind"], record["session"], record["status"]),
                         ("review", SESSION, "queued"))
        self.assertEqual(record["payload"], {"event": "approve", "items": [
            {"n": a, "head": "h%d" % a, "body": ""},
            {"n": b, "head": "h%d" % b, "body": ""}]})
        self.assertEqual(chatdesk.command_for(record), "approva #%d #%d" % (a, b))
        sending = {card["n"]: card.get("sending") for card in
                   self.desk.wizard()["review"]["pending"]}
        self.assertEqual((sending[a], sending[b]), ("approve", "approve"))
        status, again = self.post({"event": "approve", "items": self.items(a)})
        self.assertEqual((status, again["created"]), (202, False))

    def test_changes_carry_the_motivation_as_shown(self):
        self.attach()
        n = self.targets[0]
        status, _ = self.post({"event": "changes",
                               "items": self.items(n, body="Please split it.")})
        self.assertEqual(status, 202)
        record = deskstate.load(REPO)["requests"]["review:changes"]
        self.assertEqual(record["payload"]["items"][0]["body"], "Please split it.")
        self.assertEqual(chatdesk.command_for(record), "chiedi modifiche #%d" % n)

    def test_refusals_leave_no_request(self):
        self.attach()
        n = self.targets[0]
        rows = {row["n"]: row for row in self.desk.queue()["rows"]}
        own = next(k for k, row in rows.items() if row["author"] == ME)
        other = next(k for k, row in rows.items()
                     if row["author"] != ME and k not in self.targets)
        for body, code, why in (
                ({"event": "merge", "items": self.items(n)}, 400, "unknown review event"),
                ({"event": "approve", "items": []}, 400, "righe per clic"),
                ({"event": "approve", "items": self.items(*range(51))}, 400, "righe"),
                ({"event": "approve", "items": self.items(own)}, 409, "è tua"),
                ({"event": "approve", "items": self.items(other)}, 409,
                 "non è una review che ti è chiesta"),
                ({"event": "approve", "items": [{"n": n, "head": "h0"}]}, 409,
                 "cambiata dopo che l'hai vista"),
                ({"event": "approve", "items": [{"n": n}]}, 409, "cambiata"),
                ({"event": "changes", "items": self.items(n)}, 400, "motivazione"),
                ({"event": "changes", "items": self.items(
                    n, body="Split it.\n\nCo-Authored-By: Claude <noreply@anthropic.com>")},
                 400, "quale strumento"),
                ({"event": "approve", "items": [{"repo": "x/y", "n": n}]}, 400, "x/y"),
        ):
            with self.subTest(body=body):
                status, payload = self.post(body)
                self.assertEqual(status, code, payload)
                self.assertIn(why, payload["error"])
        self.assertEqual(deskstate.load(REPO).get("requests") or {}, {})

    def test_without_an_attached_chat_nothing_is_posted(self):
        status, payload = self.post({"event": "approve", "items": self.items(self.targets[0])})
        self.assertEqual(status, 409)
        self.assertIn("chat collegata", payload["error"])
        self.assertEqual(deskstate.load(REPO).get("requests") or {}, {})

    def test_skip_to_tomorrow_needs_no_chat_and_leaves_todays_steps(self):
        n = self.targets[0]
        status, payload = self.post({"event": "skip", "items": [{"n": n}]})
        self.assertEqual((status, payload), (200, {"skipped": [n]}))
        self.assertEqual(deskstate.load(REPO)["prs"][str(n)]["skipped"],
                         date.today().isoformat())
        review = self.desk.wizard()["review"]
        self.assertEqual([card["n"] for card in review["skipped"]], [n])
        self.assertNotIn(n, [card["n"] for card in review["pending"]])

    def test_the_chat_reports_each_row_and_only_the_done_ones_count_as_sent(self):
        self.attach()
        a, b = self.targets[:2]
        self.post({"event": "approve", "items": self.items(a, b)})
        claimed = deskstate.claim_request(REPO, SESSION, "pr")
        path = Path(tempfile.mkdtemp()) / "result.json"
        path.write_text(json.dumps({"status": "done", "report": "1 approvata, 1 rifiutata",
                                    "provider_changed": True, "done": [a],
                                    "refused": [{"n": b, "why": "#%d moved" % b}]}))
        report = chatdesk.result(REPO, "review:approve", str(path), SESSION, claimed["id"])
        self.assertEqual(report, "1 approvata, 1 rifiutata")
        state = deskstate.load(REPO)
        self.assertEqual(state["session_reviews"], {"approve": [a]})
        self.assertEqual(state["requests"]["review:approve"]["status"], "done")
        self.assertTrue(state["provider_refresh"]["token"])
        review = self.desk.wizard()["review"]
        sent = {card["n"]: card.get("sent") for card in review["pending"]}
        self.assertEqual((sent[a], sent[b]), ("approve", None))
        self.assertEqual(review["steps"][-1]["summary"]["approve"], [a])

    def test_a_report_on_rows_it_was_not_given_is_refused(self):
        self.attach()
        a = self.targets[0]
        self.post({"event": "approve", "items": self.items(a)})
        claimed = deskstate.claim_request(REPO, SESSION, "pr")
        path = Path(tempfile.mkdtemp()) / "result.json"
        path.write_text(json.dumps({"status": "done", "report": "r",
                                    "provider_changed": True, "done": [a, 1],
                                    "refused": []}))
        with self.assertRaisesRegex(ValueError, "other PRs"):
            chatdesk.result(REPO, "review:approve", str(path), SESSION, claimed["id"])
        state = deskstate.load(REPO)
        self.assertEqual(state["requests"]["review:approve"]["status"], "failed")
        self.assertNotIn("session_reviews", state)


class ClosingClicks(unittest.TestCase):
    """Closing an issue a merged PR already fixed is public too: the comment
    shown, the attached chat, nothing else."""

    @classmethod
    def setUpClass(cls):
        cache.clear(REPO)
        cls.desk = prdesk.Desk(get_provider("fixture"), REPO, "fporcari", str(ROOT))
        prdesk.Handler.desk = cls.desk
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), prdesk.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        deskstate.save(REPO, {})
        self.shortlist = [c["n"] for c in self.desk.wizard()["issue"]["pending"]]
        self.fixed = self.shortlist[0]
        jobs.persist_issue_analysis(REPO, {
            "n": self.fixed, "type": "DEFECT", "finding": "f", "size": "EASY",
            "phase": "SINGLE-PHASE", "problem": "p", "cause": "c", "propose": "x",
            "verify": "v", "decision": None, "fixed_by": 1099}, self.fixed)

    def close(self, *items):
        req = Request("http://127.0.0.1:%s/api/close" % self.port,
                      data=json.dumps({"items": list(items)}).encode(),
                      headers={"Content-Type": "application/json",
                               "X-Git-Workflow-Token": self.desk.write_token})
        try:
            with urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_a_close_goes_to_the_chat_with_the_comment_shown(self):
        deskstate.chat_heartbeat(REPO, SESSION, "pr")
        status, payload = self.close({"n": self.fixed, "body": "Fixed by #1099."})
        self.assertEqual((status, payload["request"]), (202, "close:issues"))
        record = deskstate.load(REPO)["requests"]["close:issues"]
        self.assertEqual(record["payload"], {"items": [{"n": self.fixed,
                                                        "body": "Fixed by #1099."}]})
        self.assertEqual(chatdesk.command_for(record), "chiudi #%d" % self.fixed)
        claimed = deskstate.claim_request(REPO, SESSION, "pr")
        path = Path(tempfile.mkdtemp()) / "result.json"
        path.write_text(json.dumps({"status": "done", "report": "1 chiusa",
                                    "provider_changed": True, "done": [self.fixed],
                                    "refused": []}))
        chatdesk.result(REPO, "close:issues", str(path), SESSION, claimed["id"])
        self.assertEqual(deskstate.load(REPO)["session_closed"], {"close": [self.fixed]})
        issue = self.desk.wizard()["issue"]
        self.assertTrue(issue["steps"][0]["rows"][0]["sent"])
        self.assertEqual(issue["steps"][-1]["summary"]["close"], [self.fixed])

    def test_closing_refusals(self):
        other = self.shortlist[1]
        for item, code, why in (
                ({"n": other, "body": "Fixed."}, 409, "già risolta"),
                ({"n": self.fixed, "body": " "}, 400, "commento"),
                ({"n": self.fixed, "body": "Fixed, co-authored-by: a bot"}, 400,
                 "quale strumento"),
                ({"n": self.fixed, "body": "Fixed."}, 409, "chat collegata"),
        ):
            with self.subTest(item=item):
                status, payload = self.close(item)
                self.assertEqual(status, code, payload)
                self.assertIn(why, payload["error"])
        self.assertEqual(deskstate.load(REPO).get("requests") or {}, {})


if __name__ == "__main__":
    unittest.main()
