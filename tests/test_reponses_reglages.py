import unittest

from scripts import glm_manager


class ReglagesReponsesTests(unittest.TestCase):
    """Les réglages de ton et de longueur utilisés par le moteur GLM central."""

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


if __name__ == "__main__":
    unittest.main()
