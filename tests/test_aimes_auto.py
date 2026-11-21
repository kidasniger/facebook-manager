import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import aimes_auto  # noqa: E402


def faux_gm(racine, reponses):
    appels = []

    def P(*parts):
        return os.path.join(racine, *parts)

    def jload(chemin, defaut):
        try:
            with open(chemin, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return defaut

    def jsave(chemin, data):
        os.makedirs(os.path.dirname(chemin), exist_ok=True)
        with open(chemin, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def http(url, data=None, headers=None, timeout=90, method=None):
        appels.append({"url": url, "data": data, "method": method})
        if not reponses:
            raise AssertionError("appel Facebook imprévu : " + url)
        return reponses.pop(0)

    return SimpleNamespace(
        ROOT=racine, P=P, jload=jload, jsave=jsave, http=http,
        log=lambda m: None, GRAPH="https://g/v26.0", PAGE_ID="page-1",
        TOKEN="tok", appels=appels,
    )


class AimesCommentairesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.racine = self.tmp.name
        self.sauves = []

    def tearDown(self):
        self.tmp.cleanup()

    def ecrire(self, chemin, data):
        absolu = os.path.join(self.racine, *chemin.split("/"))
        os.makedirs(os.path.dirname(absolu), exist_ok=True)
        with open(absolu, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def memo(self):
        with open(os.path.join(self.racine, "glm", "aimes-commentaires.json"), encoding="utf-8") as f:
            return json.load(f)

    def sauver(self, message, chemins):
        self.sauves.append((message, chemins))
        return True

    def config(self, actif=True):
        self.ecrire("glm/config.json", {"aimes": {"actif": actif}})

    def posts(self, comments):
        return [{"id": "post-1", "comments": {"data": comments}}]

    def get_like(self, liked):
        return (200, {"id": "comment-id", "user_likes": liked})

    def test_desactive_ne_rien_faire(self):
        self.config(False)
        gm = faux_gm(self.racine, [])
        self.assertEqual(aimes_auto.traiter(gm, self.sauver, self.posts([])), 0)
        self.assertEqual(gm.appels, [])

    def test_absence_posts_ne_like_rien(self):
        self.config()
        gm = faux_gm(self.racine, [])
        self.assertEqual(aimes_auto.traiter(gm, self.sauver), 0)
        self.assertEqual(gm.appels, [])

    def test_like_commentaire_et_reponse_visiteur_uniquement(self):
        self.config()
        comments = [
            {"id": "c1", "from": {"id": "visitor-1"}},
            {"id": "r1", "from": {"id": "visitor-2"}, "parent_id": "c1"},
            {"id": "own", "from": {"id": "page-1"}},
        ]
        replies = [
            self.get_like(False), (200, {"success": True}), self.get_like(True),
            self.get_like(False), (200, {"success": True}), self.get_like(True),
        ]
        gm = faux_gm(self.racine, replies)
        self.assertEqual(aimes_auto.traiter(gm, self.sauver, self.posts(comments)), 2)
        post_urls = [a["url"] for a in gm.appels if a["data"] is not None]
        self.assertEqual(len(post_urls), 2)
        self.assertTrue(all(url.endswith("/likes") for url in post_urls))
        self.assertTrue(any("/c1/likes" in url for url in post_urls))
        self.assertTrue(any("/r1/likes" in url for url in post_urls))
        self.assertFalse(any("/post-1/likes" in a["url"] for a in gm.appels))
        faits = self.memo()["faits"]
        self.assertEqual(faits["c1"]["like"], "ok")
        self.assertEqual(faits["r1"]["like"], "ok")
        self.assertNotIn("own", faits)

    def test_commentaire_deja_aime_pas_de_post(self):
        self.config()
        gm = faux_gm(self.racine, [self.get_like(True)])
        self.assertEqual(aimes_auto.traiter(
            gm, self.sauver, self.posts([{"id": "c1", "from": {"id": "u1"}}])
        ), 0)
        self.assertEqual(len(gm.appels), 1)
        self.assertEqual(self.memo()["faits"]["c1"]["like"], "ok")

    def test_post_accepte_mais_user_likes_faux_n_est_pas_un_succes(self):
        self.config()
        gm = faux_gm(self.racine, [
            self.get_like(False), (200, {"success": True}), self.get_like(False),
        ])
        self.assertEqual(aimes_auto.traiter(
            gm, self.sauver, self.posts([{"id": "c1", "from": {"id": "u1"}}])
        ), 1)
        entry = self.memo()["faits"]["c1"]
        self.assertEqual(entry["like"], "erreur")
        self.assertNotIn("verifie", entry)

    def test_echec_persistance_annule_le_post(self):
        self.config()
        gm = faux_gm(self.racine, [self.get_like(False)])
        self.assertEqual(aimes_auto.traiter(
            gm, lambda message, chemins: False,
            self.posts([{"id": "c1", "from": {"id": "u1"}}])
        ), 0)
        self.assertEqual(len(gm.appels), 1)
        self.assertFalse(any(a["data"] is not None for a in gm.appels))

    def test_dry_run_ne_publie_pas(self):
        self.config()
        gm = faux_gm(self.racine, [self.get_like(False)])
        self.assertEqual(aimes_auto.traiter(
            gm, self.sauver, self.posts([{"id": "c1", "from": {"id": "u1"}}]), dry=True
        ), 0)
        self.assertEqual(len(gm.appels), 1)

    def test_commentaire_duplique_traite_une_seule_fois(self):
        self.config()
        comment = {"id": "c1", "from": {"id": "u1"}}
        gm = faux_gm(self.racine, [
            self.get_like(False), (200, {"success": True}), self.get_like(True),
        ])
        self.assertEqual(aimes_auto.traiter(
            gm, self.sauver, self.posts([comment, comment])
        ), 1)

    def test_limite_trois_par_cycle(self):
        self.config()
        comments = [{"id": f"c{i}", "from": {"id": f"u{i}"} } for i in range(5)]
        responses = []
        for _ in range(3):
            responses += [self.get_like(False), (200, {"success": True}), self.get_like(True)]
        gm = faux_gm(self.racine, responses)
        self.assertEqual(aimes_auto.traiter(gm, self.sauver, self.posts(comments)), 3)
        self.assertEqual(sum(a["data"] is not None for a in gm.appels), 3)

    def test_ne_rejoue_pas_un_like_deja_confirme(self):
        self.config()
        self.ecrire("glm/aimes-commentaires.json", {
            "faits": {"c1": {"like": "ok", "verifie": True, "essais": 1}}
        })
        gm = faux_gm(self.racine, [])
        self.assertEqual(aimes_auto.traiter(
            gm, self.sauver, self.posts([{"id": "c1", "from": {"id": "u1"}}])
        ), 0)
        self.assertEqual(gm.appels, [])


if __name__ == "__main__":
    unittest.main()
