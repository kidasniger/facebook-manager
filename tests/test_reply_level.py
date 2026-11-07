import unittest
from unittest.mock import patch

from scripts import comment_store
from scripts import glm_manager as gm


class ReplyLevelTests(unittest.TestCase):
    def test_every_nested_comment_gets_the_root_id(self):
        tree = {
            "post-1/comments": [{"id": "root", "from": {"id": "u1"}}],
            "root/comments": [{"id": "lvl2", "from": {"id": "u2"}}],
            "lvl2/comments": [{"id": "lvl3", "from": {"id": "u3"}}],
            "lvl3/comments": [],
        }

        def get_json(url):
            path = url.split("/v1/", 1)[1].split("?")[0]
            if path == "page/posts":
                return 200, {"data": [{"id": "post-1"}]}
            return 200, {"data": [dict(c) for c in tree[path]]}

        posts = comment_store.fetch_page_posts("page", "tok", "https://x/v1", get_json)
        by_id = {c["id"]: c for c in posts[0]["comments"]["data"]}
        self.assertEqual({"root", "lvl2", "lvl3"}, set(by_id))
        for c in by_id.values():
            self.assertEqual("root", c["root_id"])
        self.assertFalse(comment_store.is_nested(by_id["root"]))
        self.assertTrue(comment_store.is_nested(by_id["lvl2"]))
        self.assertTrue(comment_store.is_nested(by_id["lvl3"]))

    def test_with_name_prefixes_first_name_once(self):
        self.assertEqual("Ki, merci pour ton message !", comment_store.with_name("Merci pour ton message !", "Ki"))
        self.assertEqual("Ki, c'est ça !", comment_store.with_name("Ki, c'est ça !", "Ki"))
        self.assertEqual("Merci !", comment_store.with_name("Merci !", ""))

    def test_bot_replies_on_root_comment_not_on_nested_one(self):
        posts = [{"id": "post-1", "message": "Post", "comments": {"data": [
            {"id": "root", "root_id": "root", "message": "Salut", "from": {"id": "page-1"}},
            {"id": "lvl2", "root_id": "root", "parent_id": "root", "message": "Explique-moi",
             "from": {"id": "u2", "name": "Ki Dev"}},
        ]}}]
        state = {"reponses": [], "a_traiter": [], "comment_ledger": []}
        calls = []

        def fake_reply(comment_id, message):
            calls.append((comment_id, message))
            return 200, {"id": "new-reply", "_delivery": "visible"}

        with patch.object(gm, "PAGE_ID", "page-1"), patch.object(gm, "DRY", False), \
             patch.object(gm, "NOW"), \
             patch.object(gm, "glm", return_value='{"repondre": true, "urgence": false, "reponse": "Bien sûr, je t\'explique."}'), \
             patch.object(gm, "fb_reply", side_effect=fake_reply), \
             patch.object(gm, "jsave"), patch.object(gm, "jload", return_value=[]), \
             patch.object(gm.comment_store, "save_ledger"):
            gm.journal.clear()
            gm.erreurs.clear()
            gm.gerer_reponses({"reponses": {"actif": True}}, state, posts)

        self.assertEqual([("root", "Ki, bien sûr, je t'explique.")], calls)
        self.assertTrue(gm.comment_store.is_handled(state["comment_ledger"], "lvl2"))


if __name__ == "__main__":
    unittest.main()
