import unittest
from unittest.mock import patch

from scripts import glm_boucle


class ConfigurationBotTests(unittest.TestCase):
    def test_verrou_auto_reponses_off_ne_desactive_que_les_reponses(self):
        cfg = {
            "reponses": {"actif": True, "ton": "neutre"},
            "aimes": {"actif": True},
            "posts": {"actif": True},
        }
        result = glm_boucle.configuration_effective(cfg, "off")
        self.assertFalse(result["reponses"]["actif"])
        self.assertEqual("neutre", result["reponses"]["ton"])
        self.assertTrue(result["aimes"]["actif"])
        self.assertTrue(result["posts"]["actif"])
        self.assertTrue(cfg["reponses"]["actif"])

    def test_verrou_on_preserve_la_configuration(self):
        cfg = {"reponses": {"actif": False}, "aimes": {"actif": True}}
        result = glm_boucle.configuration_effective(cfg, "on")
        self.assertEqual(cfg, result)

    def test_variable_environnement_est_lue_par_defaut(self):
        with patch.dict("os.environ", {"AUTO_REPONSES": "off"}):
            result = glm_boucle.configuration_effective({"reponses": {"actif": True}})
        self.assertFalse(result["reponses"]["actif"])


if __name__ == "__main__":
    unittest.main()
