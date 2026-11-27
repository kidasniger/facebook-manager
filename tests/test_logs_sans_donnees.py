import contextlib
import datetime as dt
import io
import os
import re
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
sys.path.insert(0, SCRIPTS)
import glm_manager as gm
import multi_runner as mr
import page_context as pc
import pages_budget as pb
import pages_registry as pr
import pages_sync as ps

NOW = dt.datetime(2026, 11, 1, 10, 0, tzinfo=dt.timezone.utc)
DOC = "projects/proj-test/databases/(default)/documents/"
UID, PAGE, PAGE1 = "UidSecret1", "1234567890", "1111111"
JETON = "TOKEN-SECRET-page-xyz"
COMMENTAIRE, NOM, VISITEUR = "COMMENTAIRE-SECRET-ZZ", "Zorbanom Konesecret", "visiteur-77"
FICHIER, DESCRIPTION, ELEMENT = "FILESECRET-MSG", "DESCSECRET-TXT", "e-secret-9"
INTERDITS = (COMMENTAIRE, "Zorbanom", "Konesecret", VISITEUR, FICHIER, DESCRIPTION, ELEMENT,
             UID, PAGE, JETON)
CFG = {"api_url": "http://x.test", "modele": "m"}


class FauxFs:
    projet = "proj-test"

    def __init__(self):
        champs = {"pageId": PAGE, "type": "post", "message": FICHIER, "status": "pending"}
        self.docs = [{"name": f"{DOC}users/{UID}/file/{ELEMENT}",
                      "fields": {k: pr.encoder_valeur(v) for k, v in champs.items()}}]
        self.commits = []

    def requete(self, requete):
        return list(self.docs)

    def lire_document(self, chemin):
        return {"description": DESCRIPTION} if "/profil/" in chemin else None

    def commit(self, ecritures):
        self.commits.append(ecritures)
        return len(ecritures)


class FauxRegistre:
    pages = {(UID, PAGE): {}}

    def rafraichir(self, forcer_complet=False):
        pass

    def a_traiter(self):
        return []

    def actives(self):
        return [{"uid": UID, "page_id": PAGE, "nom": NOM}]

    def resume(self):
        return {"total": 1, "active": 1, "attente": 0, "lectures": 0, "ecritures": 0}


class TestLogs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        pc.ContextePage._actif = None
        self.addCleanup(setattr, pc.ContextePage, "_actif", None)
        self.fs, self.masques, self.reponses, self.http = FauxFs(), [], [], []
        self.fb_erreur = None
        posts = [{"id": "p-1", "message": "Un post", "comments": {"data": [{
            "id": "c-1", "message": COMMENTAIRE, "created_time": "2026-11-01T10:00:00+0000",
            "from": {"id": VISITEUR, "name": NOM}, "parent_id": None, "root_id": "c-1"}]}}]

        def faux_glm(cfg, system, user, temperature=0.7, max_tokens=1500):
            print("FUITE", user, system)       # tentative de fuite : doit être jetée
            return '{"repondre": true, "urgence": false, "raison": "x", "reponse": "Merci beaucoup !"}'

        def faux_posts():
            if self.fb_erreur:
                raise self.fb_erreur
            return 200, {"data": posts}

        def faux_reply(cible, message):
            print("FUITE", cible, message, gm.TOKEN, gm.PAGE_ID)
            self.reponses.append(gm.TOKEN)
            return 200, {"id": "r-1", "_delivery": "visible", "_parent_comment_id": cible, "_verification": {}}

        def faux_http(url, data=None, **kw):
            print("FUITE", url, data)
            self.http.append(url)
            return 200, {"id": "1_9"}

        for cible, valeur in (("glm", faux_glm), ("fb_posts", faux_posts),
                              ("fb_reply", faux_reply), ("http", faux_http)):
            patcher = mock.patch.object(gm, cible, valeur)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.budget = pb.BudgetEcritures()
        self.runner = mr.Runner(self.fs, SimpleNamespace(masquer=self.masques.append),
                                os.path.join(self.tmp.name, "data"), page1_id=PAGE1,
                                maintenant=lambda: NOW.timestamp(),
                                sync=ps.Synchro(self.fs, self.budget))
        self.runner.registre = FauxRegistre()
        self.runner.jetons[(UID, PAGE)] = JETON

    def cycle(self):
        sortie, erreur = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(sortie), contextlib.redirect_stderr(erreur):
            res = self.runner.cycle(gm, CFG)
        return res, sortie.getvalue() + erreur.getvalue()

    def assert_propre(self, texte):
        for interdit in INTERDITS:
            self.assertNotIn(interdit, texte)
        self.assertNotIn("FUITE", texte)

    def test_cycle_complet_sans_donnees(self):
        res, texte = self.cycle()
        self.assertEqual(res["statut"], "ok")
        self.assertIn("Multi-Pages", texte)              # le test capture bien quelque chose
        self.assert_propre(texte + repr(res))
        self.assertEqual(self.reponses, [JETON])         # la réponse est bien partie
        self.assertEqual(len(self.http), 1)              # la file a bien publié

    def test_exception_avec_donnees(self):
        self.fb_erreur = RuntimeError(COMMENTAIRE + " " + JETON + " " + PAGE)
        res, texte = self.cycle()
        self.assertEqual(res["bilans"][0]["statut"], "exception")
        self.assert_propre(texte + repr(res["bilans"]))

    def test_identifiants_masques(self):
        self.cycle()
        self.assertIn(UID, self.masques)
        self.assertIn(PAGE, self.masques)
        n = len(self.masques)
        self.cycle()
        self.assertEqual(len(self.masques), n)           # chaque identifiant masqué une seule fois

    def test_firestore_sans_donnee_de_visiteur(self):
        self.cycle()
        ecrit = repr(self.fs.commits)
        self.assertGreater(len(self.fs.commits), 0)
        for interdit in (VISITEUR, "Konesecret", JETON):
            self.assertNotIn(interdit, ecrit)


class TestSource(unittest.TestCase):
    FICHIERS = ("multi_runner.py", "multi_pages.py", "page_context.py", "pages_sync.py", "pages_file.py",
                "pages_budget.py", "pages_schema.py", "pages_registry.py", "pages_tokens.py", "pages_crypto.py")
    MOTS = re.compile(r"\{[^}]*(jeton|token|uid|page_id|message|nom|texte|commentaire)[^}]*\}", re.I)

    def test_aucun_print_n_affiche_une_donnee(self):
        fautes = []
        for nom in self.FICHIERS:
            chemin = os.path.join(SCRIPTS, nom)
            if not os.path.exists(chemin):
                continue
            for numero, ligne in enumerate(open(chemin, encoding="utf-8"), start=1):
                if "print(" in ligne and self.MOTS.search(ligne):
                    fautes.append(f"{nom}:{numero}")
        self.assertEqual(fautes, [])


if __name__ == "__main__":
    unittest.main()
