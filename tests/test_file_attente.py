import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from scripts import file_attente


def faux_gm(racine, reponses):
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
        return reponses.pop(0)

    gm = SimpleNamespace(ROOT=racine, P=P, jload=jload, jsave=jsave, http=http, log=lambda m: None,
                         GRAPH="https://g/v", PAGE_ID="page-1", TOKEN="tok", appels=appels)
    return gm


class FileAttenteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.racine = self.tmp.name
        self.sauvegardes = []

    def tearDown(self):
        self.tmp.cleanup()

    def ecrire(self, chemin, data):
        absolu = os.path.join(self.racine, *chemin.split("/"))
        os.makedirs(os.path.dirname(absolu), exist_ok=True)
        with open(absolu, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def lire(self, chemin):
        with open(os.path.join(self.racine, *chemin.split("/")), encoding="utf-8") as f:
            return json.load(f)

    def sauver(self, message, chemins):
        # photographie du fichier au moment de l'enregistrement (pour vérifier la prise en charge)
        self.sauvegardes.append((message, {c: self.lire(c) for c in chemins}))
        return True

    def traiter(self, gm, **kw):
        return file_attente.traiter(gm, self.sauver, pause=0, **kw)

    def test_publication_texte_marque_avant_d_appeler_facebook(self):
        self.ecrire("posts/queue.json", [{"message": "Bonjour", "status": "pending"}])
        gm = faux_gm(self.racine, [(200, {"id": "page-1_99"})])
        vu_pendant_appel = {}
        original = gm.http

        def espion(url, data=None, **kw):
            vu_pendant_appel["item"] = self.lire("posts/queue.json")[0]
            return original(url, data, **kw)
        gm.http = espion
        self.assertEqual(1, self.traiter(gm))
        self.assertIn("claimed_at", vu_pendant_appel["item"])           # marqué AVANT l'envoi
        self.assertIn("claimed_at", self.sauvegardes[0][1]["posts/queue.json"][0])  # et enregistré
        final = self.lire("posts/queue.json")[0]
        self.assertEqual(("published", "page-1_99"), (final["status"], final["post_id"]))
        self.assertNotIn("claimed_at", final)

    def test_erreur_facebook_donne_le_statut_error(self):
        self.ecrire("posts/queue.json", [{"message": "x", "status": "pending"}])
        gm = faux_gm(self.racine, [(400, {"error": {"code": 190, "message": "jeton"}})])
        self.traiter(gm)
        final = self.lire("posts/queue.json")[0]
        self.assertEqual("error", final["status"])
        self.assertIn("190", final["erreur"])

    def test_elements_deja_traites_sont_ignores(self):
        self.ecrire("posts/queue.json", [{"message": "x", "status": "published"},
                                         {"message": "y", "status": "error"}])
        gm = faux_gm(self.racine, [])
        self.assertEqual(0, self.traiter(gm))
        self.assertEqual([], gm.appels)

    def test_programmation_envoie_la_date_et_donne_scheduled(self):
        self.ecrire("posts/avance.json", [{"message": "Plus tard", "status": "pending",
                                           "publish_at": "2030-01-01T12:00:00Z"}])
        gm = faux_gm(self.racine, [(200, {"id": "111"})])
        self.traiter(gm)
        envoye = gm.appels[0]["data"]
        self.assertEqual("false", envoye["published"])
        self.assertEqual("1893499200", envoye["scheduled_publish_time"])
        self.assertEqual("scheduled", self.lire("posts/avance.json")[0]["status"])

    def test_fichier_hors_du_depot_de_donnees_refuse(self):
        self.ecrire("posts/avance.json", [{"message": "x", "status": "pending", "image_path": "../../etc/passwd"}])
        gm = faux_gm(self.racine, [])
        self.traiter(gm)
        self.assertEqual("error", self.lire("posts/avance.json")[0]["status"])
        self.assertEqual([], gm.appels)

    def test_reponse_a_un_commentaire(self):
        self.ecrire("posts/replies.json", [{"comment_id": "c9", "message": "Merci", "status": "pending"}])
        gm = faux_gm(self.racine, [(200, {"id": "r1"})])
        self.traiter(gm)
        self.assertTrue(gm.appels[0]["url"].endswith("/c9/comments"))
        self.assertEqual(("replied", "r1"), tuple(self.lire("posts/replies.json")[0][k] for k in ("status", "reply_id")))

    def test_suppression_utilise_delete(self):
        self.ecrire("posts/actions.json", [{"action": "delete", "post_id": "p5", "status": "pending"}])
        gm = faux_gm(self.racine, [(200, {"success": True})])
        self.traiter(gm)
        self.assertEqual("DELETE", gm.appels[0]["method"])
        self.assertEqual("done", self.lire("posts/actions.json")[0]["status"])

    def test_plafond_par_cycle_et_mode_test(self):
        self.ecrire("posts/queue.json", [{"message": str(i), "status": "pending"} for i in range(4)])
        gm = faux_gm(self.racine, [(200, {"id": str(i)}) for i in range(4)])
        self.assertEqual(2, self.traiter(gm, maxi=2))
        self.assertEqual(2, len(gm.appels))
        gm2 = faux_gm(self.racine, [])
        self.assertEqual(0, self.traiter(gm2, dry=True))   # mode test : rien n'est envoyé
        self.assertEqual([], gm2.appels)

    def test_element_interrompu_passe_en_erreur_sans_renvoi(self):
        self.ecrire("posts/queue.json", [
            {"message": "a", "status": "pending", "claimed_at": "2026-11-08T10:00:00Z"},
            {"message": "b", "status": "pending"}])
        gm = faux_gm(self.racine, [])
        touches = file_attente.recuperer_interrompus(gm, self.sauver)
        self.assertEqual(["posts/queue.json"], touches)
        data = self.lire("posts/queue.json")
        self.assertEqual("error", data[0]["status"])
        self.assertNotIn("claimed_at", data[0])
        self.assertEqual("pending", data[1]["status"])   # l'autre reste à envoyer
        self.assertEqual([], gm.appels)


if __name__ == "__main__":
    unittest.main()
