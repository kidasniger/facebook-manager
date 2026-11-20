import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import aimes_auto  # noqa: E402


def faux_gm(racine, reponses, verification=True):
    """Mini module « gm » : fichiers réels dans `racine`, Facebook simulé par `reponses`."""
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
        if method == "GET" and "/likes?" in url:
            return 200, {"summary": {"has_liked": verification}}
        if not reponses:
            raise AssertionError("appel Facebook imprévu : " + url)
        return reponses.pop(0)

    return SimpleNamespace(ROOT=racine, P=P, jload=jload, jsave=jsave, http=http,
                           log=lambda m: None, GRAPH="https://g/v", PAGE_ID="page-1",
                           TOKEN="tok", appels=appels)


class AimesAutoTests(unittest.TestCase):
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
        with open(os.path.join(self.racine, "glm", "aimes.json"), encoding="utf-8") as f:
            return json.load(f)

    def sauver(self, message, chemins):
        self.sauves.append((message, chemins))
        return True

    def test_desactive_ne_rien_faire(self):
        self.ecrire("glm/config.json", {"aimes": {"actif": False}})
        gm = faux_gm(self.racine, [])
        self.assertEqual(aimes_auto.traiter(gm, self.sauver), 0)
        self.assertEqual(gm.appels, [])

    def test_absent_de_la_config_ne_rien_faire(self):
        self.ecrire("glm/config.json", {})
        gm = faux_gm(self.racine, [])
        self.assertEqual(aimes_auto.traiter(gm, self.sauver), 0)
        self.assertEqual(gm.appels, [])

    def test_like_des_nouvelles_publications(self):
        self.ecrire("glm/config.json", {"aimes": {"actif": True}})
        feed = {"data": [
            {"id": "p1", "from": {"id": "page-1"}},
            {"id": "p2", "from": {"id": "u9"}},
        ]}
        gm = faux_gm(self.racine, [(200, feed), (200, {"success": True}), (200, {"success": True})])
        self.assertEqual(aimes_auto.traiter(gm, self.sauver), 2)
        self.assertEqual(gm.appels[0]["method"], "GET")
        self.assertIsNone(gm.appels[0]["data"])
        self.assertIn("fields=id%2Cfrom%2Ccreated_time", gm.appels[0]["url"])
        self.assertIn("access_token=tok", gm.appels[0]["url"])
        urls = [a["url"] for a in gm.appels]
        self.assertIn("p2/likes", urls[1])    # les plus anciennes d'abord
        self.assertIn("p1/likes", urls[3])
        self.assertEqual(self.memo()["faits"]["p1"]["like"], "ok")
        # cycle suivant : rien de nouveau à aimer (l'appel feed est légitime)
        gm2 = faux_gm(self.racine, [(200, feed)])
        self.assertEqual(aimes_auto.traiter(gm2, self.sauver), 0)
        self.assertEqual([a for a in gm2.appels if "/likes" in a["url"]], [])

    def test_message_prive_a_l_auteur_seulement(self):
        self.ecrire("glm/config.json", {"aimes": {"actif": True, "message": "Décris en détail"}})
        feed = {"data": [
            {"id": "p1", "from": {"id": "page-1"}},   # post de la Page : pas de MP
            {"id": "p2", "from": {"id": "u9"}},       # post visiteur : MP
        ]}
        # ordre : fil, like p2, MP u9, like p1
        gm = faux_gm(self.racine, [
            (200, feed),
            (200, {"success": True}),
            (200, {"message_id": "m1"}),
            (200, {"success": True}),
            (200, {"summary": {"has_liked": True}}),
        ])
        self.assertEqual(aimes_auto.traiter(gm, self.sauver), 2)
        mp = [a for a in gm.appels if "/messages" in a["url"]]
        self.assertEqual(len(mp), 1)
        self.assertIn("u9", mp[0]["url"])
        memo = self.memo()["faits"]
        self.assertEqual(memo["p2"]["message"], "replied")
        self.assertIn("ignor\u00e9", memo["p1"]["message"])

    def test_mp_en_echec_ne_bloque_pas_le_like(self):
        self.ecrire("glm/config.json", {"aimes": {"actif": True, "message": "Salut"}})
        feed = {"data": [{"id": "p2", "from": {"id": "u9"}}]}
        gm = faux_gm(self.racine, [
            (200, feed),
            (200, {"success": True}),
            (200, {"summary": {"has_liked": True}}),
            (200, {"error": {"code": 10, "error_subcode": 2018278, "message": "24h"}}),
        ])
        self.assertEqual(aimes_auto.traiter(gm, self.sauver), 1)
        memo = self.memo()["faits"]["p2"]
        self.assertEqual(memo["like"], "ok")
        self.assertEqual(memo["message"], "error")

    def test_echec_persistance_claim_n_envoie_pas_le_like(self):
        self.ecrire("glm/config.json", {"aimes": {"actif": True}})
        feed = {"data": [{"id": "p1", "from": {"id": "u9"}}]}
        gm = faux_gm(self.racine, [(200, feed)])
        self.assertEqual(0, aimes_auto.traiter(gm, lambda message, chemins: False))
        self.assertEqual(1, len(gm.appels))  # lecture du fil uniquement
        self.assertFalse(any("/likes" in a["url"] for a in gm.appels))
        self.assertEqual("pending", self.memo()["faits"]["p1"]["like"])

    def test_limite_par_cycle(self):
        self.ecrire("glm/config.json", {"aimes": {"actif": True}})
        feed = {"data": [{"id": f"p{i}", "from": {"id": "u9"}} for i in range(5)]}
        gm = faux_gm(self.racine, [(200, feed)] + [(200, {"success": True})] * 3)
        self.assertEqual(aimes_auto.traiter(gm, self.sauver), 3)   # LIMITE_PAR_CYCLE
        # au cycle suivant, les 2 restantes passent
        gm2 = faux_gm(self.racine, [(200, feed)] + [(200, {"success": True})] * 2)
        self.assertEqual(aimes_auto.traiter(gm2, self.sauver), 2)
        gm3 = faux_gm(self.racine, [(200, feed)])
        self.assertEqual(aimes_auto.traiter(gm3, self.sauver), 0)
        self.assertEqual([a for a in gm3.appels if "/likes" in a["url"]], [])

    def test_success_api_sans_confirmation_ne_est_pas_un_like_ok(self):
        self.ecrire("glm/config.json", {"aimes": {"actif": True}})
        feed = {"data": [{"id": "p1", "from": {"id": "u9"}}]}
        gm = faux_gm(self.racine, [(200, feed), (200, {"success": True})], verification=False)
        self.assertEqual(aimes_auto.traiter(gm, self.sauver), 1)
        entree = self.memo()["faits"]["p1"]
        self.assertEqual(entree["like"], "erreur")
        self.assertNotIn("verifie", entree)

    def test_trois_echecs_puis_abandon(self):
        self.ecrire("glm/config.json", {"aimes": {"actif": True}})
        feed = {"data": [{"id": "p1", "from": {"id": "u9"}}]}
        for cycle in range(3):
            gm = faux_gm(self.racine, [
                (200, feed),
                (500, {"error": {"code": 1, "message": "panne"}}),
            ])
            self.assertEqual(aimes_auto.traiter(gm, self.sauver), 1)
        # au 4e cycle : abandonné, plus aucun appel de like
        gm = faux_gm(self.racine, [(200, feed)])
        self.assertEqual(aimes_auto.traiter(gm, self.sauver), 0)
        self.assertEqual([a for a in gm.appels if "/likes" in a["url"]], [])
        self.assertEqual(self.memo()["faits"]["p1"]["essais"], 3)


if __name__ == "__main__":
    unittest.main()
