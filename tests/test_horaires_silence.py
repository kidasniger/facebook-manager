import datetime as dt
import json
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("FB_PAGE_ID", "page-1")
os.environ.setdefault("FB_PAGE_TOKEN", "tok")
os.environ.setdefault("ZAI_API_KEY", "cle")

from scripts import glm_boucle
# glm_boucle importe glm_manager par le haut du sys.path (insert dans scripts/)
# : c'est ce module-là, et pas scripts.glm_manager, qu'il faut patcher.
import glm_manager  # noqa: E402  (même objet que glm_boucle.gm)


def ecrire_config(racine, horaires):
    dossier = os.path.join(racine, "glm")
    os.makedirs(dossier, exist_ok=True)
    with open(os.path.join(dossier, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"horaires": horaires}, f, ensure_ascii=False)


def u(h, m=0):
    """Instant UTC de test le 2026-11-19."""
    return dt.datetime(2026, 11, 19, h, m, tzinfo=dt.timezone.utc)


class HorairesSilenceTests(unittest.TestCase):
    """Heures de silence réglées dans l'appli : fenêtre, cheval minuit, fuseau."""

    def silence(self, horaires, moment):
        with tempfile.TemporaryDirectory() as racine:
            ecrire_config(racine, horaires)
            with patch.object(glm_manager, "P", lambda *p: os.path.join(racine, *p)):
                return glm_boucle.silence_actif(moment)

    def test_sans_reglage_pas_de_silence(self):
        self.assertFalse(self.silence({}, u(23, 30)))
        self.assertFalse(
            self.silence({"actif": False, "debut": "22:00", "fin": "07:00"}, u(23, 30)))
        self.assertFalse(self.silence({"actif": True}, u(23, 30)))

    def test_fenetre_simple(self):
        h = {"actif": True, "debut": "09:00", "fin": "12:00"}
        self.assertTrue(self.silence(h, u(10)))
        self.assertTrue(self.silence(h, u(9)))
        self.assertFalse(self.silence(h, u(8, 59)))
        self.assertFalse(self.silence(h, u(12)))

    def test_fenetre_cheval_minuit(self):
        h = {"actif": True, "debut": "22:00", "fin": "07:00"}
        self.assertTrue(self.silence(h, u(22)))
        self.assertTrue(self.silence(h, u(23, 30)))
        self.assertTrue(self.silence(h, u(0, 15)))
        self.assertTrue(self.silence(h, u(6, 59)))
        self.assertFalse(self.silence(h, u(7)))
        self.assertFalse(self.silence(h, u(12)))

    def test_plage_nulle_jamais(self):
        h = {"actif": True, "debut": "07:00", "fin": "07:00"}
        self.assertFalse(self.silence(h, u(7)))
        self.assertFalse(self.silence(h, u(22)))

    def test_fuseau_decalage_min(self):
        # 22:00 sur le téléphone (UTC+60 min) correspond à 21:00 UTC.
        h = {"actif": True, "debut": "22:00", "fin": "07:00", "decalage_min": 60}
        self.assertTrue(self.silence(h, u(21)))
        self.assertFalse(self.silence(h, u(20, 59)))
        self.assertFalse(self.silence(h, u(7)))

    def test_valeurs_invalides_inoffensives(self):
        self.assertFalse(
            self.silence({"actif": True, "debut": "zz", "fin": "07:00"}, u(23)))
        self.assertFalse(
            self.silence({"actif": True, "debut": "22:00", "fin": "x:00"}, u(23)))
        self.assertFalse(
            self.silence({"actif": True, "debut": "22:00", "fin": "07:00",
                          "decalage_min": "abc"}, u(23)))


if __name__ == "__main__":
    unittest.main()
