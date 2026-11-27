import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import pages_budget as pb
import pages_registry as pr
import pages_sync as ps

CH = "users/U1/stats/1234567__2026-11-01"


def chemin(n):
    return f"users/U1/stats/1234567__2026-11-{n:02d}"


class FauxFs:
    projet = "proj-test"

    def __init__(self):
        self.appels = []
        self.echecs = []     # numéros d'appels (à partir de 1) qui échouent

    def commit(self, ecritures):
        self.appels.append(ecritures)
        if len(self.appels) in self.echecs:
            raise pr.FirestoreError("Firestore HTTP 500", status=500)
        return len(ecritures)

    def chemins(self):
        return [e["update"]["name"].split("/documents/", 1)[1] for lot in self.appels for e in lot]


class Base(unittest.TestCase):
    def setUp(self):
        self.fs = FauxFs()
        self.budget = pb.BudgetEcritures()
        self.s = ps.Synchro(self.fs, self.budget)


class TestFile(Base):
    def test_ajouter_et_vider(self):
        self.assertTrue(self.s.ajouter(CH, {"jour": "x"}))
        bilan = self.s.vider()
        self.assertEqual(bilan, {"envoyes": 1, "restants": 0, "refus_budget": 0, "erreurs": 0})
        self.assertEqual(self.fs.chemins(), [CH])

    def test_document_identique_ignore(self):
        self.s.ajouter(CH, {"jour": "x"})
        self.s.vider()
        self.assertFalse(self.s.ajouter(CH, {"jour": "x"}))
        self.assertEqual(self.s.vider()["envoyes"], 0)
        self.assertEqual(len(self.fs.appels), 1)

    def test_document_modifie_renvoye(self):
        self.s.ajouter(CH, {"jour": "x"})
        self.s.vider()
        self.assertTrue(self.s.ajouter(CH, {"jour": "y"}))
        self.assertEqual(self.s.vider()["envoyes"], 1)
        self.assertEqual(self.budget.utilise, 2)

    def test_remplacement_dans_la_file(self):
        self.s.ajouter(CH, {"jour": "x"})
        self.assertTrue(self.s.ajouter(CH, {"jour": "x"}))     # pas encore envoyé : remplacé
        self.assertEqual(len(self.s.file), 1)
        self.s.vider()
        self.assertEqual(self.budget.utilise, 1)

    def test_priorite_conservee_au_remplacement(self):
        self.s.ajouter(CH, {"jour": "x"}, prioritaire=True)
        self.s.ajouter(CH, {"jour": "y"})
        self.assertTrue(self.s.file[CH][2])

    def test_supprimer(self):
        self.s.ajouter(CH, {"status": "ok"}, supprimer=["erreur"])
        self.s.vider()
        masque = self.fs.appels[0][0]["updateMask"]["fieldPaths"]
        self.assertEqual(sorted(masque), ["erreur", "status"])


class TestLots(Base):
    def test_decoupage_en_lots(self):
        with mock.patch.object(pr, "LOT_MAX", 2):
            for n in range(1, 6):
                self.s.ajouter(chemin(n), {"n": n})
            bilan = self.s.vider()
        self.assertEqual([len(lot) for lot in self.fs.appels], [2, 2, 1])
        self.assertEqual((bilan["envoyes"], bilan["restants"]), (5, 0))

    def test_prioritaires_d_abord(self):
        with mock.patch.object(pr, "LOT_MAX", 1):
            self.s.ajouter(chemin(1), {"n": 1})
            self.s.ajouter(chemin(2), {"n": 2}, prioritaire=True)
            self.s.ajouter(chemin(3), {"n": 3})
            self.s.vider()
        self.assertEqual(self.fs.chemins()[0], chemin(2))
        self.assertEqual(len(self.fs.chemins()), 3)


class TestBudget(unittest.TestCase):
    def test_ordinaire_refuse_prioritaire_accepte(self):
        fs = FauxFs()
        budget = pb.BudgetEcritures(limite=10, reserve=0.2)     # cible 8
        ordinaire = ps.Synchro(fs, budget)
        for n in range(1, 10):
            ordinaire.ajouter(chemin(n), {"n": n})
        bilan = ordinaire.vider()
        self.assertEqual((bilan["envoyes"], bilan["refus_budget"], bilan["restants"]), (0, 9, 9))
        self.assertEqual((fs.appels, budget.utilise), ([], 0))
        urgent = ps.Synchro(fs, budget)
        for n in range(1, 10):
            urgent.ajouter(chemin(n), {"n": n}, prioritaire=True)
        self.assertEqual(urgent.vider()["envoyes"], 9)
        self.assertEqual(budget.utilise, 9)

    def test_budget_compte_ce_qui_est_envoye(self):
        fs, budget = FauxFs(), pb.BudgetEcritures()
        s = ps.Synchro(fs, budget)
        for n in range(1, 4):
            s.ajouter(chemin(n), {"n": n})
        s.vider()
        self.assertEqual(budget.utilise, 3)


class TestErreurs(Base):
    def test_echec_total_puis_reprise(self):
        self.fs.echecs = [1]
        self.s.ajouter(CH, {"jour": "x"})
        bilan = self.s.vider()
        self.assertEqual((bilan["envoyes"], bilan["erreurs"], bilan["restants"]), (0, 1, 1))
        self.assertEqual(self.budget.utilise, 0)
        self.assertNotIn(CH, self.s.empreintes)
        bilan = self.s.vider()
        self.assertEqual((bilan["envoyes"], bilan["erreurs"], bilan["restants"]), (1, 0, 0))
        self.assertEqual(self.budget.utilise, 1)

    def test_echec_partiel(self):
        self.fs.echecs = [1]
        with mock.patch.object(pr, "LOT_MAX", 1):
            self.s.ajouter(chemin(1), {"n": 1})
            self.s.ajouter(chemin(2), {"n": 2})
            bilan = self.s.vider()
        self.assertEqual((bilan["envoyes"], bilan["erreurs"], bilan["restants"]), (1, 1, 1))
        self.assertEqual(self.budget.utilise, 1)


class TestLimites(Base):
    def test_dry_run(self):
        s = ps.Synchro(self.fs, self.budget, dry_run=True)
        s.ajouter(CH, {"jour": "x"})
        bilan = s.vider()
        self.assertEqual(bilan, {"envoyes": 0, "restants": 1, "refus_budget": 0, "erreurs": 0})
        self.assertEqual((self.fs.appels, self.budget.utilise), ([], 0))

    def test_file_max(self):
        with mock.patch.object(ps, "FILE_MAX", 3):
            for n in range(1, 6):
                self.s.ajouter(chemin(n), {"n": n})
        self.assertEqual(list(self.s.file), [chemin(3), chemin(4), chemin(5)])

    def test_cache_empreintes_vide(self):
        with mock.patch.object(ps, "EMPREINTES_MAX", 2):
            for n in range(1, 4):
                self.s.ajouter(chemin(n), {"n": n})
            self.s.vider()
        self.assertEqual(self.s.empreintes, {})
        self.assertTrue(self.s.ajouter(chemin(1), {"n": 1}))     # renvoyé : cache perdu, sans danger


class TestBilan(Base):
    def test_compteurs_seulement(self):
        self.s.ajouter(CH, {"texte": "Commentaire secret", "nom": "Awa"})
        bilan = self.s.vider()
        self.assertEqual(set(bilan), {"envoyes", "restants", "refus_budget", "erreurs"})
        self.assertTrue(all(isinstance(v, int) for v in bilan.values()))
        self.assertNotIn("secret", repr(bilan) + repr(self.s.resume() if hasattr(self.s, "resume") else ""))


if __name__ == "__main__":
    unittest.main()
