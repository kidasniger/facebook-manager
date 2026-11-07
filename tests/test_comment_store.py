import json
import os
import tempfile
import unittest

from scripts import comment_store


class CommentStoreTests(unittest.TestCase):
    def test_pagination_follows_every_next_page(self):
        calls = []
        def get_json(url):
            calls.append(url)
            if url == "first":
                return 200, {"data": [{"id": "1"}], "paging": {"next": "second"}}
            return 200, {"data": [{"id": "2"}]}
        self.assertEqual([{"id": "1"}, {"id": "2"}], comment_store.fetch_all("first", get_json))
        self.assertEqual(["first", "second"], calls)

    def test_ignore_is_scoped_but_success_is_global(self):
        entries = [{"comment_id": "c1", "status": "ignore", "source": "repondre-auto"}]
        self.assertFalse(comment_store.is_handled(entries, "c1", source="glm-live"))
        self.assertTrue(comment_store.is_handled(entries, "c1", source="repondre-auto"))
        comment_store.record(entries, "c1", "replied", reply_id="r1")
        self.assertTrue(comment_store.is_handled(entries, "c1", source="glm-live"))

    def test_record_updates_instead_of_duplicating(self):
        entries = []
        comment_store.record(entries, "c1", "ignore", source="repondre-auto")
        comment_store.record(entries, "c1", "replied", reply_id="r1")
        self.assertEqual(1, len(entries))
        self.assertEqual("replied", entries[0]["status"])

    def test_legacy_glm_history_is_migrated(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy = os.path.join(directory, "etat.json")
            target = os.path.join(directory, "shared.json")
            with open(legacy, "w", encoding="utf-8") as stream:
                json.dump({"reponses": [{"comment_id": "c1", "reply_id": "r1"}],
                           "a_traiter": [{"comment_id": "c2"}]}, stream)
            entries = comment_store.load_ledger(target, (legacy,))
            self.assertTrue(comment_store.is_handled(entries, "c1"))
            self.assertTrue(comment_store.is_handled(entries, "c2"))


    def test_fetch_page_posts_follows_replies_even_when_comment_count_is_zero(self):
        calls = []

        def get_json(url):
            calls.append(url)
            if "/page/posts?" in url:
                return 200, {"data": [{"id": "post1", "message": "Post"}]}
            if "/post1/comments?" in url:
                return 200, {"data": [{"id": "root", "message": "Commentaire principal", "from": {"id": "user1"}, "comment_count": 1}]}
            if "/root/comments?" in url:
                return 200, {"data": [{"id": "reply", "message": "Réponse du bot", "from": {"id": "page"}, "comment_count": 0}]}
            if "/reply/comments?" in url:
                return 200, {"data": [{"id": "nested", "message": "Réponse de la personne", "from": {"id": "user3"}, "comment_count": 0}]}
            if "/nested/comments?" in url:
                return 200, {"data": [{"id": "deep", "message": "Nouvelle réponse", "from": {"id": "user4"}}]}
            if "/deep/comments?" in url:
                return 200, {"data": []}
            raise AssertionError("URL Facebook inattendue : " + url)

        posts = comment_store.fetch_page_posts("page", "token", "https://graph.facebook.com/vXX.X", get_json)
        comments = posts[0]["comments"]["data"]
        self.assertEqual(["root", "reply", "nested", "deep"], [item["id"] for item in comments])
        self.assertEqual([None, "root", "reply", "nested"], [item["parent_id"] for item in comments])
        self.assertEqual(["post1"] * 4, [item["post_id"] for item in comments])
        self.assertTrue(any("/reply/comments?" in call for call in calls))
        self.assertTrue(any("/nested/comments?" in call for call in calls))


if __name__ == "__main__":
    unittest.main()
