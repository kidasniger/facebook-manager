import json
import unittest
from unittest.mock import patch

from scripts import glm_manager as gm


class CommentRetryTests(unittest.TestCase):
    def test_glm_failure_does_not_stop_later_comments_or_mark_failure_handled(self):
        posts = [{
            "id": "post-1",
            "message": "Publication",
            "comments": {"data": [
                {"id": "comment-failed", "message": "Premier", "from": {"id": "user-1"}},
                {"id": "comment-ok", "message": "Deuxième", "from": {"id": "user-2"}},
            ]},
        }]
        state = {"reponses": [], "a_traiter": [], "comment_ledger": []}
        cfg = {"reponses": {"actif": True}}
        calls = []

        def fake_reply(comment_id, message):
            calls.append(comment_id)
            return 200, {"id": "reply-2", "_delivery": "visible"}

        with patch.object(gm, "PAGE_ID", "page-1"), \
             patch.object(gm, "DRY", False), \
             patch.object(gm, "NOW"), \
             patch.object(gm, "glm", side_effect=[
                 RuntimeError("indisponible"),
                 json.dumps({"repondre": True, "urgence": False, "reponse": "Merci pour ton commentaire !"}),
             ]), \
             patch.object(gm, "fb_reply", side_effect=fake_reply), \
             patch.object(gm, "jsave"), \
             patch.object(gm.comment_store, "save_ledger"):
            gm.journal.clear()
            gm.erreurs.clear()
            sent, human = gm.gerer_reponses(cfg, state, posts)

        self.assertEqual(["comment-ok"], calls)
        self.assertEqual(1, len(sent))
        self.assertFalse(gm.comment_store.is_handled(state["comment_ledger"], "comment-failed"))
        self.assertTrue(gm.comment_store.is_handled(state["comment_ledger"], "comment-ok"))
        self.assertTrue(any("comment-failed" in error for error in gm.erreurs))


    def test_reply_visibility_requires_presence_in_target_parent_thread(self):
        with (
            patch.object(gm, "PAGE_ID", "page-1"),
            patch.object(gm, "http", return_value=(200, {"id": "reply-expected"})),
            patch.object(gm.comment_store, "fetch_all", return_value=[
                {"id": "reply-expected", "message": "Réponse du bot", "from": {"id": "page-1"}, "is_hidden": False}
            ]) as fetch,
        ):
            status, payload = gm.fb_reply("nested-comment-7", "Réponse du bot")
        self.assertEqual(200, status)
        self.assertEqual("visible", payload["_delivery"])
        self.assertIn("/nested-comment-7/comments?", fetch.call_args.args[0])

    def test_missing_reply_in_parent_thread_is_not_reported_visible(self):
        with (
            patch.object(gm, "PAGE_ID", "page-1"),
            patch.object(gm.comment_store, "fetch_all", return_value=[]),
        ):
            delivery, detail = gm.fb_verify_reply("nested-comment-7", "reply-expected", "Réponse du bot")
        self.assertEqual("not_in_parent_thread", delivery)
        self.assertEqual("nested-comment-7", detail["parent_comment_id"])

    def test_pending_reply_is_verified_before_any_republication(self):
        state = {
            "reponses": [], "a_traiter": [], "comment_ledger": [],
            "pending_verification": [{
                "comment_id": "nested-comment-7", "parent_id": "bot-reply-3",
                "reply_id": "reply-expected", "message": "Réponse du bot",
            }],
        }
        with (
            patch.object(gm, "PAGE_ID", "page-1"),
            patch.object(gm, "DRY", False),
            patch.object(gm, "NOW"),
            patch.object(gm, "fb_verify_reply", return_value=("visible", {})),
            patch.object(gm, "glm") as glm_call,
            patch.object(gm, "jsave"),
            patch.object(gm.comment_store, "save_ledger"),
        ):
            sent, human = gm.gerer_reponses({"reponses": {"actif": True}}, state, [])
        self.assertEqual([], sent)
        self.assertEqual([], human)
        self.assertEqual([], state["pending_verification"])
        self.assertTrue(gm.comment_store.is_handled(state["comment_ledger"], "nested-comment-7"))
        glm_call.assert_not_called()

    def test_content_and_old_human_classification_never_filter_a_user_comment(self):
        comment = {
            "id": "comment-sensitive",
            "message": "Quel est le prix ?",
            "from": {"id": "user-1"},
            "page_replied": False,
            "created_time": "2020-01-01T00:00:00+0000",
        }
        state = {
            "reponses": [],
            "a_traiter": [{"comment_id": "comment-sensitive"}],
            "comment_ledger": [{"comment_id": "comment-sensitive", "status": "human"}],
            "pending_verification": [],
        }
        with patch.object(gm, "PAGE_ID", "page-1"):
            verdict, reason = gm.pre_filtre(
                comment, "post-1", state,
                {"age_max_heures": 1, "filtre_sensible": True},
            )
        self.assertIsNone(verdict)
        self.assertIsNone(reason)

    def test_page_authored_comments_are_skipped_to_prevent_reply_loops(self):
        comment = {
            "id": "page-comment",
            "message": "Réponse publiée par la Page",
            "from": {"id": "page-1"},
        }
        with patch.object(gm, "PAGE_ID", "page-1"):
            verdict, reason = gm.pre_filtre(
                comment, "post-1",
                {"reponses": [], "a_traiter": [], "comment_ledger": []},
                {},
            )
        self.assertEqual("ignorer", verdict)
        self.assertIn("Page", reason)

    def test_comment_already_replied_by_page_is_not_replied_again(self):
        comment = {
            "id": "comment-already-answered",
            "message": "Merci !",
            "from": {"id": "user-1"},
            "page_replied": True,
        }
        state = {"reponses": [], "comment_ledger": [], "pending_verification": []}
        with patch.object(gm, "PAGE_ID", "page-1"):
            verdict, reason = gm.pre_filtre(comment, "post-1", state, {})
        self.assertEqual("ignorer", verdict)
        self.assertIn("réponse de la Page existe déjà", reason)

if __name__ == "__main__":
    unittest.main()
