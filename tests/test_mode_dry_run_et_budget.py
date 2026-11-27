import datetime as dt
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import glm_manager as gm
import multi_pages as mp
import multi_runner as mr
import page_context as pc
import pages_budget as pb
import pages_registry as pr
import pages_sync as ps

NOW = dt.datetime(2026, 11, 1, 10, 0, tzinfo=dt.timezone.utc)
DOC = "projects/proj-test/databases/(default)/documents/"
UID, PAGE_A, PAGE_B = "U1", "1234567", "7654321"


class FauxFs:
    projet = "proj-test"

    def __init__(self):
        self.profils, self.docs, self.commits = {}, [], []

    def requete(self, requete):
        return list(self.docs)

    def lire_document(self, chemin):
        return self.profils.get(chemin)

    def commit(self, ecritures):
        self.commits.append(ecritures)
        return len(ecritures)


class FauxRegistre:
    pages = {}

    def __init__(self, page_ids):
        self.ids = page_ids

    def actives(self):
        return [{"uid": UID, "page_id": p, "nom": "N"} for p in self.ids]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        pc.ContextePage._actif = None
        self.addCleanup(setattr, pc.ContextePage, "_actif", None)
        self.fs = FauxFs()
        self.budget = pb.BudgetEcritures(maintenant=lambda: NOW.timestamp())
        self.runner = mr.Runner(self.fs, None, os.path.join(self.tmp.name, "data"), page1_id="1111111",
                                maintenant=lambda: NOW.timestamp(),
                                sync=ps.Synchro(self.fs, self.budget))
        self.runner.registre = FauxRegistre([PAGE_A, PAGE_B])
        for p in (PAGE_A, PAGE_B):
            self.runner.jetons[(UID, p)] = "TOKEN-" + p
        self.appels = []
        self.gm = SimpleNamespace(http=lambda url, data=None, **kw: (self.appels.append(url) or (200, {"id": "1_9"})),
                                  GRAPH="https://g.test", DRY=False)

    def profil(self, page, **champs):
        self.fs.profils[f"users/{UID}/profil/{page}"] = champs

    def ajouter_element(self, element_id, page):
        champs = {"pageId": page, "type": "post", "message": "Bonjour", "status": "pending"}
        self.fs.docs.append({"name": f"{DOC}users/{UID}/file/{element_id}",
                             "fields": {k: pr.encoder_valeur(v) for k, v in champs.items()}})


class TestDryRunParPage(Base):
    def test_dry_run_d_une_seule_page(self):
        self.profil(PAGE_A, dryRun=True)
        contextes, _ = self.runner.contextes()
        self.assertEqual(self.runner.dry_pages, {(UID, PAGE_A)})
        for ctx in contextes:
            attendu = ctx.page_id == PAGE_A
            with ctx.activer(gm):
                self.assertEqual(gm.DRY, attendu, ctx.page_id)

    def test_retour_au_mode_reel(self):
        self.profil(PAGE_A, dryRun=True)
        self.runner.contextes()
        self.profil(PAGE_A, dryRun=False)
        self.runner.profils.clear()
        self.runner.contextes()
        self.assertEqual(self.runner.dry_pages, set())

    def test_seul_un_vrai_true_active_le_mode(self):
        for valeur in ("true", 1, "on", None):
            self.runner.profils.clear()
            self.profil(PAGE_A, dryRun=valeur)
            self.runner.contextes()
            self.assertEqual(self.runner.dry_pages, set(), valeur)

    def test_file_d_une_page_en_test_reste_en_attente(self):
        self.profil(PAGE_A, dryRun=True)
        self.runner.contextes()
        self.ajouter_element("ea", PAGE_A)
        self.ajouter_element("eb", PAGE_B)
        bilan = self.runner.traiter_file(self.gm)
        self.assertEqual((bilan["publies"], bilan["retard"]), (1, 1))
        self.assertEqual(self.appels, [f"https://g.test/{PAGE_B}/feed"])
        ecrits = [e["update"]["name"] for lot in self.fs.commits for e in lot]
        self.assertEqual(len(ecrits), 1)
        self.assertTrue(ecrits[0].endswith("/file/eb"))


class TestRalentissement(Base):
    def test_budget_depasse_ralentit_toutes_les_pages(self):
        etats = {(UID, p): mp.EtatPage() for p in (PAGE_A, PAGE_B)}
        self.runner.etats.update(etats)
        self.budget.enregistrer(self.budget.cible)          # facteur maximal (8)
        self.runner._ralentir()
        attendu = NOW.timestamp() + mr.INTERVALLE_BASE * (pb.FACTEUR_MAX - 1)
        for etat in etats.values():
            self.assertEqual(etat.pause_jusqua, attendu)

    def test_ne_raccourcit_jamais_une_pause(self):
        etat = self.runner.etats.setdefault((UID, PAGE_A), mp.EtatPage())
        etat.pause_jusqua = NOW.timestamp() + 99999
        self.budget.enregistrer(self.budget.cible)
        self.runner._ralentir()
        self.assertEqual(etat.pause_jusqua, NOW.timestamp() + 99999)

    def test_rythme_normal_sans_ralentissement(self):
        etat = self.runner.etats.setdefault((UID, PAGE_A), mp.EtatPage())
        self.budget.enregistrer(10)
        self.runner._ralentir()
        self.assertEqual(etat.pause_jusqua, 0.0)

    def test_sans_synchro(self):
        self.runner.sync = None
        etat = self.runner.etats.setdefault((UID, PAGE_A), mp.EtatPage())
        self.runner._ralentir()
        self.assertEqual(etat.pause_jusqua, 0.0)


if __name__ == "__main__":
    unittest.main()
