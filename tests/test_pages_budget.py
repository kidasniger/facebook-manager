import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import pages_budget as pb

MINUIT = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc).timestamp()


class Base(unittest.TestCase):
    def setUp(self):
        self.h = [MINUIT + 12 * 3600]          # midi UTC
        self.b = pb.BudgetEcritures(maintenant=lambda: self.h[0])

    def a(self, heures, minutes=0):
        self.h[0] = MINUIT + heures * 3600 + minutes * 60


class TestBase(Base):
    def test_valeurs_par_defaut(self):
        self.assertEqual((self.b.limite, self.b.cible, self.b.utilise), (20000, 16000, 0))
        self.assertEqual(self.b.facteur(), 1.0)

    def test_parametres_bornes(self):
        self.assertEqual(pb.BudgetEcritures(limite=0).limite, 1)
        self.assertEqual(pb.BudgetEcritures(reserve=5).reserve, 0.9)
        self.assertEqual(pb.BudgetEcritures(reserve=-1).reserve, 0.0)
        self.assertEqual(pb.BudgetEcritures(utilise=-5).utilise, 0)
        self.assertEqual(pb.BudgetEcritures(limite=1000, reserve=0.5).cible, 500)

    def test_enregistrer(self):
        self.assertEqual(self.b.enregistrer(10), 10)
        self.assertEqual(self.b.enregistrer(5), 15)
        self.assertEqual(self.b.enregistrer(-3), 15)
        self.assertEqual(self.b.utilise, 15)


class TestAutorisation(Base):
    def test_ordinaire_limite_a_la_cible(self):
        self.b.enregistrer(15990)
        self.assertEqual(self.b.restant(), 10)
        self.assertTrue(self.b.autoriser(10))
        self.assertFalse(self.b.autoriser(11))

    def test_prioritaire_jusqu_a_la_limite(self):
        self.b.enregistrer(17000)
        self.assertEqual(self.b.restant(), 0)
        self.assertFalse(self.b.autoriser(1))
        self.assertTrue(self.b.autoriser(3000, prioritaire=True))
        self.assertFalse(self.b.autoriser(3001, prioritaire=True))
        self.assertEqual(self.b.restant(prioritaire=True), 3000)

    def test_restant_jamais_negatif(self):
        self.b.enregistrer(30000)
        self.assertEqual((self.b.restant(), self.b.restant(prioritaire=True)), (0, 0))


class TestFacteur(Base):
    def test_pas_de_projection_en_debut_de_journee(self):
        self.a(1)
        self.b.enregistrer(100)
        self.assertEqual(self.b.facteur(), 1.0)

    def test_rythme_normal(self):
        self.b.enregistrer(8000)                 # midi : projection 16 000 = cible
        self.assertEqual(self.b.facteur(), 1.0)

    def test_rythme_trop_rapide(self):
        self.b.enregistrer(9000)                 # projection 18 000
        self.assertAlmostEqual(self.b.facteur(), 1.125)
        self.a(6)
        self.b.utilise = 15000                   # projection 60 000
        self.assertAlmostEqual(self.b.facteur(), 3.75)

    def test_plafond(self):
        self.a(1, 30)
        self.b.enregistrer(15900)                # projection 254 400
        self.assertEqual(self.b.facteur(), pb.FACTEUR_MAX)

    def test_cible_atteinte(self):
        self.a(23)
        self.b.enregistrer(16000)
        self.assertEqual(self.b.facteur(), pb.FACTEUR_MAX)


class TestMinuit(Base):
    def test_remise_a_zero_par_enregistrer(self):
        self.b.enregistrer(500)
        self.h[0] += 86400
        self.assertEqual(self.b.enregistrer(3), 3)

    def test_remise_a_zero_par_restant(self):
        self.b.enregistrer(17000)
        self.h[0] += 86400
        self.assertEqual(self.b.restant(), 16000)
        self.assertEqual(self.b.utilise, 0)

    def test_remise_a_zero_par_facteur(self):
        self.b.enregistrer(17000)
        self.h[0] += 86400
        self.assertEqual(self.b.facteur(), 1.0)
        self.assertEqual(self.b.utilise, 0)

    def test_meme_jour_pas_de_remise(self):
        self.b.enregistrer(500)
        self.a(23, 59)
        self.assertEqual(self.b.restant(), 15500)


class TestResume(Base):
    def test_compteurs_seulement(self):
        self.b.enregistrer(9000)
        r = self.b.resume()
        self.assertEqual(set(r), {"utilise", "cible", "limite", "facteur"})
        self.assertEqual((r["utilise"], r["cible"], r["limite"]), (9000, 16000, 20000))
        self.assertAlmostEqual(r["facteur"], 1.12, places=2)


if __name__ == "__main__":
    unittest.main()
