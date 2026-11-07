import unittest
from unittest.mock import patch

from scripts import glm_manager


class FacebookReplyDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.original_page_id = glm_manager.PAGE_ID
        glm_manager.PAGE_ID = "page-1"

    def tearDown(self):
        glm_manager.PAGE_ID = self.original_page_id

    def test_reply_is_visible_only_when_present_in_target_comment_edge(self):
        with (
            patch.object(glm_manager, "http", return_value=(200, {"id": "reply-1"})),
            patch.object(glm_manager.comment_store, "fetch_all", return_value=[
                {"id": "reply-1", "message": "Merci !", "from": {"id": "page-1"}, "is_hidden": False}
            ]) as fetch,
        ):
            status, result = glm_manager.fb_reply("comment-1", "Merci !")
        self.assertEqual(200, status)
        self.assertEqual("visible", result["_delivery"])
        self.assertIn("/comment-1/comments?", fetch.call_args.args[0])

    def test_hidden_reply_is_not_reported_as_visible(self):
        with (
            patch.object(glm_manager, "http", return_value=(200, {"id": "reply-2"})),
            patch.object(glm_manager.comment_store, "fetch_all", return_value=[
                {"id": "reply-2", "message": "Merci !", "from": {"id": "page-1"}, "is_hidden": True}
            ]),
        ):
            status, result = glm_manager.fb_reply("comment-1", "Merci !")
        self.assertEqual(200, status)
        self.assertEqual("hidden", result["_delivery"])

    def test_unverified_reply_is_distinguished(self):
        with (
            patch.object(glm_manager, "http", return_value=(200, {"id": "reply-3"})),
            patch.object(glm_manager.comment_store, "fetch_all", side_effect=RuntimeError("Forbidden")),
        ):
            status, result = glm_manager.fb_reply("comment-1", "Merci !")
        self.assertEqual(200, status)
        self.assertEqual("unverified", result["_delivery"])
        self.assertIn("Forbidden", result["_verification"]["verification_error"])


if __name__ == "__main__":
    unittest.main()
