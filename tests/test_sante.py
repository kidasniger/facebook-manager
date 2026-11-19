import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from scripts import sante


def faux_gm(racine, erreurs=None, usage=(0, 0), appels=0):
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

    return SimpleNamespace(ROOT=racine, P=P, jload=jload, jsave=jsave,
                           erreurs=list(erreurs or []),
                           USAGE={"pct": usage[0], "attente_min": usage[1]},
                           APPELS={"graph": appels})


class SanteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.racine = self.tmp.name
        sante._dernier_push = 0.0

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

    def test_maj_ecrit_rapport_complet(self):
        self.ecrire("posts/actions.json", [
            {"comment_id": "c1", "action": "delete_comment", "status": "pending"},
            {"comment_id": "c2", "action": "delete_comment", "status": "error",
             "erreur": "code 9007 : action non autorisée", "traite_le": "2026-11-19T06:00:00Z"},
            {"comment_id": "c3", "action": "delete_comment", "status": "done"},
        ])
        self.ecrire("posts/queue.json", [{"message": "salut", "status": "pending"}])
        gm = faux_gm(self.racine, erreurs=["Facebook : échec d'envoi (HTTP 400)"],
                     usage=(62, 3), appels=42)
        doc = sante.maj(gm, session={"duree_min": 330, "cycles": 7, "reponses": 5}, echecs=1)
        # le fichier écrit sur disque correspond au document renvoyé
        self.assertEqual(self.lire("bot/sante.json"), doc)
        self.assertEqual(doc["quota"], {"pct": 62, "attente_min": 3})
        self.assertEqual(doc["appels_graph"], 42)
        self.assertEqual(doc["echecs_consecutifs"], 1)
        self.assertEqual(doc["session"], {"duree_min": 330, "cycles": 7, "reponses": 5})
        self.assertEqual(doc["file"]["en_attente"], 2)  # c1 + queue
        details = [e["detail"] for e in doc["erreurs"]]
        self.assertTrue(any("9007" in d for d in details))          # erreur de file
        self.assertTrue(any("HTTP 400" in d for d in details))      # erreur commentaires
        self.assertTrue(all(e["origine"] for e in doc["erreurs"]))

    def test_fusion_deduplique_les_erreurs_persistantes(self):
        gm = faux_gm(self.racine, erreurs=["quota saturé"])
        sante.maj(gm)
        doc = sante.maj(gm)  # même erreur au cycle suivant
        erreurs = [e for e in doc["erreurs"] if e["detail"] == "quota saturé"]
        self.assertEqual(len(erreurs), 1)  # pas d'empilement, heure mise à jour
        # une erreur disparue reste tracée avec son heure d'origine
        gm2 = faux_gm(self.racine, erreurs=[])
        doc2 = sante.maj(gm2)
        self.assertTrue(any(e["detail"] == "quota saturé" for e in doc2["erreurs"]))

    def test_pousser_respecte_le_delai(self):
        appels = []
        gm = faux_gm(self.racine)
        sante.maj(gm)
        self.assertTrue(sante.pousser(gm, lambda m, c: appels.append(m) or True))
        self.assertEqual(appels, ["Santé du bot"])
        self.assertFalse(sante.pousser(gm, lambda m, c: appels.append(m) or True))
        self.assertEqual(len(appels), 1)          # délai non écoulé : rien
        self.assertTrue(sante.pousser(gm, lambda m, c: appels.append(m) or True, force=True))
        self.assertEqual(len(appels), 2)          # force pousse même dans le délai

    def test_pousser_ignore_fichier_inexistant(self):
        gm = faux_gm(self.racine)  # jamais de maj : enregistrer saute le fichier
        self.assertTrue(sante.pousser(gm, lambda m, c: True, force=True))


if __name__ == "__main__":
    unittest.main()
