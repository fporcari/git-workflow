"""Ownership and generation boundaries for attached desk requests."""

import json
import tempfile
import time
import unittest
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import chatdesk
import deskstate
import prdesk


class ChatSessions(unittest.TestCase):
    repo = "test/chat-sessions"

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(deskstate, "STATE_DIR", Path(scratch.name)).start()

    def enqueue(self, desk="pr", key="explain:7"):
        record, created = deskstate.request(
            self.repo, key, "explain", 7, via="chat", desk=desk)
        self.assertTrue(created)
        return record

    def test_cross_desk_clicks_are_pinned_at_enqueue(self):
        deskstate.chat_heartbeat(self.repo, "pr-chat", "pr")
        deskstate.chat_heartbeat(self.repo, "issue-chat", "issue")
        pr = self.enqueue()
        issue = self.enqueue("issue", "issue-analyze:7")
        self.assertEqual((pr["desk"], pr["session"]), ("pr", "pr-chat"))
        self.assertEqual(issue["session"], "issue-chat")
        self.assertIsNone(deskstate.claim_request(self.repo, "issue-chat", "pr"))
        self.assertEqual(deskstate.claim_request(self.repo, "issue-chat", "issue")["id"],
                         issue["id"])
        self.assertEqual(deskstate.claim_request(self.repo, "pr-chat")["id"], pr["id"])

    def test_two_chats_cannot_attach_to_the_same_desk(self):
        def attach(session):
            try:
                deskstate.chat_heartbeat(self.repo, session)
                return session
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            attached = list(pool.map(attach, ("first", "second")))
        self.assertEqual(sum(x is not None for x in attached), 1)
        self.assertEqual(len(deskstate.load(self.repo)["chats"]), 1)

    def test_heartbeat_and_detach_do_not_change_the_other_session(self):
        deskstate.chat_heartbeat(self.repo, "a", "pr")
        deskstate.chat_heartbeat(self.repo, "b", "issue")
        before = deskstate.load(self.repo)["chats"]["b"]
        deskstate.chat_heartbeat(self.repo, "a", "pr")
        deskstate.chat_detach(self.repo, "a")
        self.assertEqual(deskstate.load(self.repo)["chats"]["b"], before)
        self.assertIsNone(deskstate.chat_listening(self.repo, desk="pr"))
        self.assertTrue(deskstate.chat_listening(self.repo, desk="issue"))

    def test_busy_chat_keeps_queued_clicks_and_claims_only_after_completion(self):
        deskstate.chat_heartbeat(self.repo, "a")
        first = self.enqueue()
        deskstate.claim_request(self.repo, "a")
        second = self.enqueue(key="explain:8")
        self.assertIsNone(deskstate.claim_request(self.repo, "a"))
        deskstate.update(self.repo, lambda state: state["requests"]["explain:8"].update(
            epoch=time.time() - deskstate.CHAT_CLAIM_GRACE - 1))
        self.assertFalse(deskstate.reclaim_request(self.repo, "explain:8"))
        self.assertEqual(deskstate.load(self.repo)["chats"]["a"]["busy"]["id"], first["id"])
        chatdesk.fail(self.repo, "explain:7", "done", "a", first["id"])
        self.assertEqual(deskstate.claim_request(self.repo, "a")["id"], second["id"])

    def test_busy_owner_cannot_be_replaced_after_heartbeat_expires(self):
        deskstate.chat_heartbeat(self.repo, "a")
        self.enqueue()
        deskstate.claim_request(self.repo, "a")
        deskstate.update(self.repo, lambda state: state["chats"]["a"].update(epoch=0))
        with self.assertRaisesRegex(ValueError, "already attached"):
            deskstate.chat_heartbeat(self.repo, "b")
        self.assertIsNone(deskstate.chat_listening(self.repo))
        self.assertTrue(deskstate.chat_attached(self.repo))

    def test_dead_session_clicks_cannot_be_stolen_by_its_replacement(self):
        deskstate.chat_heartbeat(self.repo, "a")
        self.enqueue()
        deskstate.update(self.repo, lambda state: state["chats"]["a"].update(epoch=0))
        deskstate.chat_heartbeat(self.repo, "b")
        self.assertIsNone(deskstate.claim_request(self.repo, "b"))

    def test_legacy_listener_cannot_claim_new_records(self):
        deskstate.save(self.repo, {"chat": {"epoch": time.time(), "session": "legacy"}})
        self.assertIsNone(deskstate.chat_listening(self.repo))
        deskstate.chat_heartbeat(self.repo, "a")
        record = self.enqueue()
        self.assertNotEqual(record["via"], "chat")
        self.assertIsNone(deskstate.claim_request(self.repo, "legacy"))

    def test_old_or_foreign_result_cannot_persist_or_close_replacement(self):
        deskstate.chat_heartbeat(self.repo, "a")
        first = self.enqueue()
        deskstate.claim_request(self.repo, "a")
        deskstate.update(self.repo, lambda state: state["requests"]["explain:7"].update(
            taken_epoch=0))
        second = self.enqueue()
        deskstate.claim_request(self.repo, "a")
        path = deskstate.STATE_DIR / "result.json"
        path.write_text(json.dumps({"n": 7, "what": "stale explanation"}))
        for session, identity in (("a", first["id"]), ("b", second["id"])):
            with self.assertRaises(ValueError):
                chatdesk.result(self.repo, "explain:7", str(path), session, identity)
            with self.assertRaises(ValueError):
                chatdesk.fail(self.repo, "explain:7", "wrong", session, identity)
        state = deskstate.load(self.repo)
        self.assertNotIn("prs", state)
        self.assertEqual(state["requests"]["explain:7"]["status"], "taken")
        chatdesk.result(self.repo, "explain:7", str(path), "a", second["id"])
        self.assertEqual(deskstate.load(self.repo)["prs"]["7"]["what"], "stale explanation")

    def test_slow_payload_cannot_ready_or_fail_a_replacement(self):
        deskstate.chat_heartbeat(self.repo, "a")
        first, _ = deskstate.request(self.repo, "explain:7", "explain", 7,
                                     via="chat", status="preparing")
        deskstate.update(self.repo, lambda state: state["requests"]["explain:7"].update(epoch=0))
        second, _ = deskstate.request(self.repo, "explain:7", "explain", 7,
                                      via="chat", status="preparing")
        self.assertIsNone(deskstate.ready_request(self.repo, "explain:7", {"old": True}, first["id"]))
        self.assertIsNone(deskstate.close_request(self.repo, "explain:7", "failed", request_id=first["id"]))
        self.assertEqual(deskstate.load(self.repo)["requests"]["explain:7"], second)

    def test_second_desk_boot_preserves_live_chat_and_request(self):
        deskstate.register_desk(self.repo, "pr", 8399)
        deskstate.chat_heartbeat(self.repo, "a")
        self.enqueue()
        deskstate.claim_request(self.repo, "a")
        before = deskstate.load(self.repo)
        deskstate.reset(self.repo)
        self.assertEqual(deskstate.load(self.repo), before)

    def test_listener_closes_when_its_desk_closes(self):
        deskstate.register_desk(self.repo, "pr", 8399)
        deskstate.register_desk(self.repo, "issue", 8398)
        deskstate.desk_stopped(self.repo, "pr")
        self.assertTrue(deskstate.desks_closed(self.repo, desk="pr"))
        self.assertFalse(deskstate.desks_closed(self.repo, desk="issue"))

    def test_review_chat_can_own_both_desks(self):
        deskstate.chat_heartbeat(self.repo, "a", "both")
        issue = self.enqueue("issue", "issue-analyze:7")
        self.assertEqual(deskstate.claim_request(self.repo, "a", "both")["id"], issue["id"])
        with self.assertRaises(ValueError):
            deskstate.chat_heartbeat(self.repo, "b", "issue")

    def test_same_session_listener_is_exclusive(self):
        with chatdesk.listener(self.repo, "a"):
            with self.assertRaisesRegex(ValueError, "running listener"):
                with chatdesk.listener(self.repo, "a"):
                    self.fail("duplicate listener admitted")
            with chatdesk.listener(self.repo, "b"):
                pass
        with chatdesk.listener(self.repo, "a"):
            pass

    def test_renewing_one_desk_cannot_reclaim_a_siblings_new_owner(self):
        deskstate.chat_heartbeat(self.repo, "a", "both")
        deskstate.update(self.repo, lambda state: state["chats"]["a"].update(epoch=0))
        deskstate.chat_heartbeat(self.repo, "b", "issue")
        with self.assertRaises(ValueError):
            deskstate.chat_heartbeat(self.repo, "a", "pr")

    def test_http_handoff_uses_the_originating_desk(self):
        deskstate.chat_heartbeat(self.repo, "a", "pr")
        deskstate.chat_heartbeat(self.repo, "b", "issue")
        handler = object.__new__(prdesk.Handler)
        handler.desk = SimpleNamespace(repo=self.repo, kind="issue")
        handler._send = mock.Mock()
        with mock.patch.object(prdesk.notify, "notify"):
            handler._chat_handoff("issue-analyze", 7, {}, "analyze")
        record = deskstate.load(self.repo)["requests"]["issue-analyze:7"]
        self.assertEqual((record["desk"], record["session"]), ("issue", "b"))
        deskstate.chat_detach(self.repo, "b")
        self.assertIsNone(handler._chat_handoff("issue-analyze", 8, {}, "analyze"))

    def test_closed_wait_detaches_only_its_own_chat(self):
        deskstate.register_desk(self.repo, "pr", 8399)
        deskstate.register_desk(self.repo, "issue", 8398)
        deskstate.chat_heartbeat(self.repo, "b", "issue")
        deskstate.desk_stopped(self.repo, "pr")
        self.assertTrue(chatdesk.wait(self.repo, 0, "a", "pr")["closed"])
        self.assertIsNone(deskstate.chat_listening(self.repo, desk="pr"))
        self.assertTrue(deskstate.chat_listening(self.repo, desk="issue"))
