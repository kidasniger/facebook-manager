import os
import unittest

os.environ.setdefault("DATA_DIR", "/tmp/kidas-test-data")

from scripts import glm_manager as gm


class QuotaTests(unittest.TestCase):
    def test_usage_page_lit_le_pourcentage_le_plus_haut(self):
        gm._lire_usage({"x-business-use-case-usage":
                        '{"123": [{"type": "pages", "call_count": 82, "total_cputime": 10, "total_time": 40, '
                        '"estimated_time_to_regain_access": 5}]}'})
        self.assertEqual(82, gm.USAGE["pct"])
        self.assertEqual(5, gm.USAGE["attente_min"])

    def test_usage_application(self):
        gm._lire_usage({"x-app-usage": '{"call_count": 28, "total_time": 91, "total_cputime": 25}'})
        self.assertEqual(91, gm.USAGE["pct"])

    def test_en_tetes_absents_ou_invalides_remettent_a_zero(self):
        gm._lire_usage({"x-app-usage": "pas du json"})
        self.assertEqual(0, gm.USAGE["pct"])
        gm._lire_usage(None)


if __name__ == "__main__":
    unittest.main()
