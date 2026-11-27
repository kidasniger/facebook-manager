import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import glm_manager as gm
import page_context as pc

UID, PAGE, TOKEN = "U1", "1234567890", "EAAjeton-SECRET-1"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = self.tmp.name
        self.sauve = {n: getattr(gm, n) for n in ("TOKEN", "PAGE_ID", "ROOT", "DRY", "NOW")}
        self.usage, self.appels = dict(gm.USAGE), dict(gm.APPELS)
        self.journal, self.erreurs = list(gm.journal), list(gm.erreurs)
        pc.ContextePage._actif = None
        self.addCleanup(self.rendre)
        # État « ancien » reconnaissable pour vérifier la restauration.
        gm.TOKEN, gm.PAGE_ID, gm.ROOT, gm.DRY = "ancien-token", "111", "/ancien", False
        gm.USAGE.update(pct=40, attente_min=3)
        gm.APPELS["graph"] = 7
        gm.journal[:] = ["avant"]
        gm.erreurs[:] = ["erreur-avant"]

    def rendre(self):
        for nom, valeur in self.sauve.items():
            setattr(gm, nom, valeur)
        gm.USAGE.clear()
        gm.USAGE.update(self.usage)
        gm.APPELS.clear()
        gm.APPELS.update(self.appels)
        gm.journal[:] = self.journal
        gm.erreurs[:] = self.erreurs
        pc.ContextePage._actif = None

    def ctx(self, **extra):
        dossier = pc.dossier_page(self.base, UID, PAGE, creer=False)
        return pc.ContextePage(UID, PAGE, TOKEN, dossier, etiquette="Page 1", **extra)

    def assert_ancien_etat(self):
        self.assertEqual((gm.TOKEN, gm.PAGE_ID, gm.ROOT, gm.DRY), ("ancien-token", "111", "/ancien", False))
        self.assertEqual(gm.USAGE, {"pct": 40, "attente_min": 3})
        self.assertEqual(gm.APPELS, {"graph": 7})
        self.assertEqual(gm.journal, ["avant"])
        self.assertEqual(gm.erreurs, ["erreur-avant"])
        self.assertIsNone(pc.ContextePage._actif)


class TestDossier(Base):
    def test_dans_la_base_et_cree(self):
        d = pc.dossier_page(self.base, UID, PAGE)
        self.assertTrue(os.path.isdir(d))
        self.assertEqual(os.path.dirname(d), os.path.abspath(self.base))
        self.assertEqual(os.path.basename(d), f"{UID}__{PAGE}")

    def test_sans_creation(self):
        d = pc.dossier_page(self.base, UID, PAGE, creer=False)
        self.assertFalse(os.path.exists(d))

    def test_identifiants_invalides(self):
        for uid, page in (("a/b", PAGE), ("", PAGE), ("..", PAGE), ("a__b", PAGE),
                          (UID, "abc"), (UID, "12"), (UID, ""), (UID, "1/2"), (UID, "../1")):
            with self.assertRaises(ValueError, msg=(uid, page)):
                pc.dossier_page(self.base, uid, page)
        self.assertEqual(os.listdir(self.base), [])


class TestConstruction(Base):
    def test_refus(self):
        for uid, page, token in (("a/b", PAGE, TOKEN), (UID, "abc", TOKEN), (UID, PAGE, ""), (UID, PAGE, None)):
            with self.assertRaises(ValueError):
                pc.ContextePage(uid, page, token, self.base)

    def test_repr_sans_secret(self):
        texte = repr(self.ctx())
        self.assertEqual(texte, "<ContextePage Page 1>")
        for interdit in (TOKEN, UID, PAGE):
            self.assertNotIn(interdit, texte)


class TestActivation(Base):
    def test_installe_la_page(self):
        c = self.ctx()
        with c.activer(gm):
            self.assertEqual((gm.TOKEN, gm.PAGE_ID, gm.ROOT, gm.DRY), (TOKEN, PAGE, c.dossier, False))
            self.assertTrue(os.path.isdir(c.dossier))
            self.assertEqual(gm.USAGE, {"pct": 0, "attente_min": 0})
            self.assertEqual(gm.APPELS, {"graph": 0})
            self.assertEqual((gm.journal, gm.erreurs), ([], []))
            self.assertIs(pc.ContextePage._actif, c)
        self.assert_ancien_etat()

    def test_dry_run(self):
        with self.ctx(dry_run=True).activer(gm):
            self.assertTrue(gm.DRY)
        self.assertFalse(gm.DRY)

    def test_compteurs_conserves_sans_texte(self):
        c = self.ctx()
        with c.activer(gm):
            gm.APPELS["graph"] += 5
            gm.USAGE.update(pct=62, attente_min=9)
            gm.erreurs.append("erreur avec identifiant 999")
            gm.erreurs.append("autre")
            gm.journal.append("ligne")
        self.assertEqual(c.appels_graph, 5)
        self.assertEqual(c.usage, {"pct": 62, "attente_min": 9})
        self.assertEqual(c.nb_erreurs, 2)
        self.assertNotIn("999", repr(c.__dict__))
        self.assert_ancien_etat()

    def test_identite_des_listes_conservee(self):
        journal, erreurs, usage, appels = gm.journal, gm.erreurs, gm.USAGE, gm.APPELS
        with self.ctx().activer(gm):
            pass
        self.assertIs(gm.journal, journal)
        self.assertIs(gm.erreurs, erreurs)
        self.assertIs(gm.USAGE, usage)
        self.assertIs(gm.APPELS, appels)

    def test_now_restaure(self):
        avant = gm.NOW
        with self.ctx().activer(gm):
            gm.NOW = "autre"
        self.assertEqual(gm.NOW, avant)

    def test_restauration_apres_exception(self):
        c = self.ctx()
        with self.assertRaises(KeyError):
            with c.activer(gm):
                gm.APPELS["graph"] += 2
                raise KeyError("x")
        self.assertEqual(c.appels_graph, 2)
        self.assert_ancien_etat()
        with c.activer(gm):          # réutilisable après une erreur
            self.assertEqual(gm.PAGE_ID, PAGE)
        self.assert_ancien_etat()

    def test_deux_contextes_simultanes_refuses(self):
        premier = self.ctx()
        second = pc.ContextePage("U2", "9999999", "autre-token", os.path.join(self.base, "b"))
        with premier.activer(gm):
            with self.assertRaises(RuntimeError):
                with second.activer(gm):
                    pass
            self.assertEqual((gm.TOKEN, gm.PAGE_ID), (TOKEN, PAGE))
            self.assertIs(pc.ContextePage._actif, premier)
        self.assert_ancien_etat()

    def test_sequence_de_deux_pages(self):
        a, b = self.ctx(), pc.ContextePage("U2", "9999999", "token-b", os.path.join(self.base, "b"))
        with a.activer(gm):
            gm.APPELS["graph"] += 1
        with b.activer(gm):
            self.assertEqual((gm.TOKEN, gm.PAGE_ID), ("token-b", "9999999"))
            self.assertEqual(gm.APPELS, {"graph": 0})
            gm.APPELS["graph"] += 3
        self.assertEqual((a.appels_graph, b.appels_graph), (1, 3))
        self.assert_ancien_etat()


if __name__ == "__main__":
    unittest.main()
