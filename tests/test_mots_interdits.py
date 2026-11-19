import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("FB_PAGE_ID", "page-1")
os.environ.setdefault("FB_PAGE_TOKEN", "tok")
os.environ.setdefault("ZAI_API_KEY", "cle")

from scripts import glm_manager as gm


class MotsInterditsTests(unittest.TestCase):
    """Mots interdits réglés dans l'appli : détection, accents, désactivation."""

    def test_detection_simple(self):
        cfg = {"moderation": {"actif": True, "mots": ["arnaque"]}}
        self.assertEqual(gm.mot_interdit("C'est une arnaque ici !", cfg), "arnaque")
        self.assertEqual(gm.mot_interdit("Gros ARNAQUE sur ta page", cfg), "arnaque")

    def test_accents_et_casse_ignores(self):
        cfg = {"moderation": {"actif": True, "mots": ["enfoire"]}}
        self.assertEqual(gm.mot_interdit("Quel enfoiré ce bot", cfg), "enfoire")

    def test_expression_multi_mots(self):
        cfg = {"moderation": {"actif": True, "mots": ["achat followers"]}}
        self.assertEqual(
            gm.mot_interdit("Vends achat followers pas cher", cfg), "achat followers")

    def test_sans_correspondance(self):
        cfg = {"moderation": {"actif": True, "mots": ["arnaque", "crypto"]}}
        self.assertIsNone(gm.mot_interdit("Superbe astuce, merci !", cfg))
        self.assertIsNone(gm.mot_interdit("", cfg))
        self.assertIsNone(gm.mot_interdit(None, cfg))

    def test_desactive_ou_absent(self):
        self.assertIsNone(
            gm.mot_interdit("arnaque", {"moderation": {"actif": False, "mots": ["arnaque"]}}))
        self.assertIsNone(gm.mot_interdit("arnaque", {}))
        self.assertIsNone(gm.mot_interdit("arnaque", {"moderation": {"actif": True}}))
        self.assertIsNone(
            gm.mot_interdit("arnaque", {"moderation": {"actif": True, "mots": "arnaque"}}))

    def test_mot_d_un_seul_caractere_ignore(self):
        cfg = {"moderation": {"actif": True, "mots": ["a", "1"]}}
        self.assertIsNone(gm.mot_interdit("Quel beau jour, tout va bien", cfg))

    def test_gerer_reponses_route_vers_humain_sans_appeler_glm(self):
        posts = [{
            "id": "post-1",
            "message": "Publication",
            "comments": {"data": [
                {"id": "c-spam", "message": "Achète des followers pas chers, contacte-moi",
                 "from": {"id": "user-1"}},
            ]},
        }]
        state = {"reponses": [], "a_traiter": [], "comment_ledger": []}
        cfg = {"reponses": {"actif": True},
               "moderation": {"actif": True, "mots": ["followers"]}}
        with patch.object(gm, "PAGE_ID", "page-1"), \
             patch.object(gm, "DRY", False), \
             patch.object(gm, "glm") as fake_glm, \
             patch.object(gm, "fb_reply"), \
             patch.object(gm, "jsave"), \
             patch.object(gm.comment_store, "save_ledger"):
            gm.journal.clear()
            gm.erreurs.clear()
            envoyees, humain = gm.gerer_reponses(cfg, state, posts)
        fake_glm.assert_not_called()
        self.assertEqual([], envoyees)
        self.assertEqual(1, len(humain))
        self.assertEqual("c-spam", humain[0]["comment_id"])
        self.assertIn("mot interdit", humain[0]["raison"])
        self.assertIn("followers", humain[0]["raison"])

    def test_commentaire_normal_toujours_repondu_quand_filtre_actif(self):
        posts = [{
            "id": "post-1",
            "message": "Publication",
            "comments": {"data": [
                {"id": "c-ok", "message": "Merci, super astuce !", "from": {"id": "user-1"}},
            ]},
        }]
        state = {"reponses": [], "a_traiter": [], "comment_ledger": []}
        cfg = {"reponses": {"actif": True},
               "moderation": {"actif": True, "mots": ["followers"]}}
        with patch.object(gm, "PAGE_ID", "page-1"), \
             patch.object(gm, "DRY", False), \
             patch.object(gm, "glm", return_value=json.dumps(
                 {"repondre": True, "urgence": False, "reponse": "Merci d'être passé !"})), \
             patch.object(gm, "fb_reply",
                          return_value=(200, {"id": "r-1", "_delivery": "visible"})), \
             patch.object(gm, "jsave"), \
             patch.object(gm.comment_store, "save_ledger"):
            envoyees, humain = gm.gerer_reponses(cfg, state, posts)
        self.assertEqual(1, len(envoyees))
        self.assertEqual([], humain)


if __name__ == "__main__":
    unittest.main()
