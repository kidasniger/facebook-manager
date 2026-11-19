import json
import os
import tempfile
import unittest
from unittest.mock import patch

from scripts import glm_manager

os.environ.setdefault("FB_PAGE_ID", "page-1")
os.environ.setdefault("FB_PAGE_TOKEN", "tok")
os.environ.setdefault("ZAI_API_KEY", "cle")

from scripts import repondre_auto


class ReglagesReponsesTests(unittest.TestCase):
    """Ton et longueur des réponses : fragments de prompt + lecture de la config."""

    def test_phrases_par_defaut(self):
        self.assertIn("chaleureuse", glm_manager.phrase_ton("chaleureux"))
        self.assertIn("280", glm_manager.phrase_longueur("normale"))

    def test_phrases_neutre_et_courte(self):
        self.assertIn("professionnelle", glm_manager.phrase_ton("neutre"))
        self.assertIn("vouvoie", glm_manager.phrase_ton("neutre"))
        self.assertIn("140", glm_manager.phrase_longueur("courte"))

    def test_phrases_direct_et_detaillee(self):
        self.assertIn("franche", glm_manager.phrase_ton("direct"))
        self.assertIn("450", glm_manager.phrase_longueur("detaillee"))

    def test_valeurs_inconnues_reviennent_au_defaut(self):
        self.assertEqual(
            glm_manager.phrase_ton("exotique"), glm_manager.phrase_ton("chaleureux"))
        self.assertEqual(
            glm_manager.phrase_longueur("zzz"), glm_manager.phrase_longueur("normale"))

    def test_repondre_auto_lit_la_config(self):
        with tempfile.TemporaryDirectory() as racine:
            dossier = os.path.join(racine, "glm")
            os.makedirs(dossier)
            with open(os.path.join(dossier, "config.json"), "w", encoding="utf-8") as f:
                json.dump({"reponses": {"ton": "neutre", "longueur": "courte"}}, f)
            with patch.object(repondre_auto.comment_store, "data_path",
                              lambda *parts: os.path.join(racine, *parts)):
                prompt = repondre_auto.build_system_prompt()
        self.assertIn("professionnelle", prompt)
        self.assertIn("140", prompt)
        self.assertIn("Réponse : 1 phrase, 140 caractères maximum", prompt)

    def test_repondre_auto_defaut_sans_config(self):
        with tempfile.TemporaryDirectory() as racine:
            with patch.object(repondre_auto.comment_store, "data_path",
                              lambda *parts: os.path.join(racine, *parts)):
                prompt = repondre_auto.build_system_prompt()
        self.assertIn("280", prompt)
        self.assertIn("chaleureuse", prompt)


if __name__ == "__main__":
    unittest.main()
