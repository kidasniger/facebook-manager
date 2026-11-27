import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import glm_manager as gm
import multi_pages as mp
import page_context as pc

UID_A, PAGE_A = "UA1", "1234567"
UID_B, PAGE_B = "UB2", "7654321"
CFG = {"api_url": "http://x.test", "modele": "m"}


def jeton(page):
    return "TOKEN-SECRET-" + page


def post_avec(cid, texte="Super", nom="Awa Kone"):
    return {"id": "post-" + cid, "message": "Un post", "comments": {"data": [{
        "id": cid, "message": texte, "created_time": "2026-11-01T10:00:00+0000",
        "from": {"id": "visiteur-" + cid, "name": nom}, "parent_id": None, "root_id": cid}]}}


class FauxGlm:
    def __init__(self):
        self.systemes, self.appels = [], 0

    def __call__(self, cfg, system, user, temperature=0.7, max_tokens=1500):
        self.appels += 1
        self.systemes.append(system)
        return json.dumps({"repondre": True, "urgence": False, "raison": "x", "reponse": "Merci beaucoup !"})


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = os.path.join(self.tmp.name, "data")
        pc.ContextePage._actif = None
        self.addCleanup(setattr, pc.ContextePage, "_actif", None)
        self.avant = (gm.TOKEN, gm.PAGE_ID, gm.ROOT, gm.DRY, gm.PROFIL)
        self.glm = FauxGlm()
        self.posts = {}
        self.fb_forcee = None
        self.envoyes = []

        def faux_posts():
            if self.fb_forcee is not None:
                return self.fb_forcee
            return 200, {"data": self.posts.get(gm.PAGE_ID, [])}

        def faux_reply(cible, message):
            self.envoyes.append((gm.PAGE_ID, gm.TOKEN, cible, message))
            return 200, {"id": "reply-" + cible, "_delivery": "visible",
                         "_parent_comment_id": cible, "_verification": {}}

        for cible, valeur in (("glm", self.glm), ("fb_posts", faux_posts), ("fb_reply", faux_reply)):
            patcher = mock.patch.object(gm, cible, valeur)
            patcher.start()
            self.addCleanup(patcher.stop)

    def ctx(self, uid=UID_A, page=PAGE_A, profil=None, quota=None, dry=True, etiquette="Page 1"):
        dossier = pc.dossier_page(self.base, uid, page, creer=False)
        return pc.ContextePage(uid, page, jeton(page), dossier, etiquette=etiquette,
                               profil=profil, quota=quota, dry_run=dry)

    def lire(self, ctx, *chemin):
        with open(os.path.join(ctx.dossier, *chemin), encoding="utf-8") as f:
            return json.load(f)

    def assert_gm_restaure(self):
        self.assertEqual((gm.TOKEN, gm.PAGE_ID, gm.ROOT, gm.DRY, gm.PROFIL), self.avant)
        self.assertIs(gm.glm, self.glm)
        self.assertIsNone(pc.ContextePage._actif)


class TestOutils(unittest.TestCase):
    def test_interrupteur(self):
        self.assertFalse(mp.multi_pages_actif(None))
        self.assertFalse(mp.multi_pages_actif({}))
        for valeur in ("off", "", "OFF", "non", "1", "true"):
            self.assertFalse(mp.multi_pages_actif({"MULTI_PAGES": valeur}), valeur)
        for valeur in ("on", "ON", " On "):
            self.assertTrue(mp.multi_pages_actif({"MULTI_PAGES": valeur}), valeur)

    def test_cfg_pour_page(self):
        cfg = {"api_url": "http://x", "modele": "m", "actif": False, "posts": {"actif": True},
               "reponses": {"ton": "neutre"}, "moderation": {"actif": True, "mots": ["a"]},
               "horaires": {"actif": True}}
        copie = json.loads(json.dumps(cfg))
        profil = {"reponses": {"ton": "direct"}}
        res = mp.cfg_pour_page(cfg, profil)
        self.assertEqual((res["api_url"], res["modele"]), ("http://x", "m"))
        self.assertEqual(res["posts"], {"actif": False})
        self.assertEqual(res["reponses"], {"ton": "direct"})
        self.assertIsNot(res["reponses"], profil["reponses"])
        self.assertEqual(res["moderation"], {})
        self.assertNotIn("horaires", res)
        self.assertIs(res["actif"], True)
        self.assertEqual(cfg, copie)

    def test_cfg_profil_absent(self):
        for profil in (None, "x", [1]):
            res = mp.cfg_pour_page({"api_url": "u"}, profil)
            self.assertEqual((res["reponses"], res["moderation"], res["posts"]), ({}, {}, {"actif": False}))

    def test_quota(self):
        self.assertFalse(mp.QuotaIA(3, 2).atteint)
        self.assertTrue(mp.QuotaIA(3, 3).atteint)
        self.assertTrue(mp.QuotaIA(0).atteint)

    def test_pauses(self):
        etat = mp.EtatPage()
        ok = lambda pct=0: {"statut": "ok", "quota_pct": pct}
        self.assertEqual(mp.pause_apres(ok(), etat), 0)
        self.assertEqual(mp.pause_apres(ok(75), etat), 300)
        self.assertEqual(mp.pause_apres(ok(90), etat), 900)
        self.assertEqual(mp.pause_apres({"statut": "jeton"}, etat), 3600)
        etat = mp.EtatPage()
        self.assertEqual([mp.pause_apres({"statut": "limite"}, etat) for _ in range(3)], [300, 600, 1200])
        self.assertEqual(mp.pause_apres(ok(), etat), 0)
        self.assertEqual(etat.echecs, 0)
        etat = mp.EtatPage()
        self.assertEqual([mp.pause_apres({"statut": "facebook"}, etat) for _ in range(3)], [60, 120, 240])
        self.assertLessEqual(mp.pause_apres({"statut": "exception"}, etat), 1800)


class TestTraiterPage(Base):
    def test_mode_test_ne_repond_pas_et_n_ecrit_rien(self):
        self.posts[PAGE_A] = [post_avec("c1")]
        ctx = self.ctx(profil={"nom": "Boutique Test"})
        bilan = mp.traiter_page(gm, ctx, CFG)
        self.assertEqual((bilan["statut"], bilan["reponses"], bilan["a_traiter"], bilan["erreurs"]),
                         ("ok", 0, 0, 0))
        self.assertEqual(self.glm.appels, 1)
        self.assertEqual(self.envoyes, [])
        self.assertFalse(os.path.exists(os.path.join(ctx.dossier, "glm", "etat.json")))
        self.assert_gm_restaure()

    def test_prompt_autre_page_sans_proprietaire(self):
        self.posts[PAGE_A] = [post_avec("c1")]
        for profil in (None, {"nom": "Boutique Test", "description": "Vend des tissus."}):
            self.glm.systemes.clear()
            mp.traiter_page(gm, self.ctx(profil=profil), CFG)
            prompt = self.glm.systemes[0]
            for mot in ("Aboubacar", "KI DAS", "RENDRAX", "rendrax", "kidas-portfolio"):
                self.assertNotIn(mot, prompt)
        self.assertIn("Boutique Test", prompt)

    def test_ton_et_mots_du_proprietaire_non_herites(self):
        cfg = dict(CFG, reponses={"ton": "neutre"}, moderation={"actif": True, "mots": ["spam"]})
        self.posts[PAGE_A] = [post_avec("c1", texte="j'adore ce spam")]
        mp.traiter_page(gm, self.ctx(), cfg)
        self.assertEqual(self.glm.appels, 1)        # « spam » n'est pas bloqué pour cette Page
        prompt = self.glm.systemes[0]
        self.assertIn(gm.TONS["chaleureux"], prompt)
        self.assertNotIn(gm.TONS["neutre"], prompt)

    def test_ton_de_la_page(self):
        self.posts[PAGE_A] = [post_avec("c1")]
        mp.traiter_page(gm, self.ctx(profil={"reponses": {"ton": "direct"}}), CFG)
        self.assertIn(gm.TONS["direct"], self.glm.systemes[0])

    def test_reponse_reelle_avec_le_jeton_de_la_page(self):
        self.posts[PAGE_A] = [post_avec("c1")]
        ctx = self.ctx(dry=False)
        bilan = mp.traiter_page(gm, ctx, CFG, sauver=lambda msg, chemins: True)
        self.assertEqual((bilan["statut"], bilan["reponses"], bilan["erreurs"]), ("ok", 1, 0))
        self.assertEqual(self.envoyes, [(PAGE_A, jeton(PAGE_A), "c1", "Merci beaucoup !")])
        self.assertEqual([r["comment_id"] for r in self.lire(ctx, "posts", "replies.json")], ["c1"])
        self.assertTrue(os.path.exists(os.path.join(ctx.dossier, "glm", "etat.json")))
        self.assert_gm_restaure()

    def test_quota_ia_atteint(self):
        self.posts[PAGE_A] = [post_avec("c1")]
        bilan = mp.traiter_page(gm, self.ctx(dry=False, quota=mp.QuotaIA(limite=0)), CFG,
                                sauver=lambda msg, chemins: True)
        self.assertEqual((bilan["reponses"], bilan["erreurs"], bilan["quota_ia_atteint"]), (0, 1, True))
        self.assertEqual((self.glm.appels, self.envoyes), (0, []))

    def test_quota_ia_compte_les_appels(self):
        self.posts[PAGE_A] = [post_avec("c1")]
        quota = mp.QuotaIA(limite=5)
        bilan = mp.traiter_page(gm, self.ctx(quota=quota), CFG)
        self.assertEqual(quota.utilise, 1)
        self.assertFalse(bilan["quota_ia_atteint"])

    def test_erreurs_facebook(self):
        cas = [((400, {"error": {"code": 190}}), "jeton"), ((400, {"error": {"code": "463"}}), "jeton"),
               ((400, {"error": {"code": 4}}), "limite"), ((400, {"error": {"code": 80001}}), "limite"),
               ((429, {}), "limite"), ((500, {}), "facebook"), ((0, {"error": {"message": "x"}}), "facebook")]
        for reponse, statut in cas:
            self.fb_forcee = reponse
            bilan = mp.traiter_page(gm, self.ctx(), CFG)
            self.assertEqual(bilan["statut"], statut, reponse)
            self.assertEqual(self.glm.appels, 0)
        self.assert_gm_restaure()

    def test_a_traiter_sans_doublon(self):
        profil = {"moderation": {"actif": True, "mots": ["spam"]}}
        self.posts[PAGE_A] = [post_avec("c1", texte="du spam ici")]
        ctx = self.ctx(profil=profil, dry=False)
        premier = mp.traiter_page(gm, ctx, CFG, sauver=lambda msg, chemins: True)
        second = mp.traiter_page(gm, ctx, CFG, sauver=lambda msg, chemins: True)
        self.assertEqual((premier["a_traiter"], second["a_traiter"]), (1, 0))
        self.assertEqual(len(self.lire(ctx, "inbox", "a_traiter.json")), 1)
        self.assertEqual(self.glm.appels, 0)

    def test_sortie_silencieuse_et_bilan_sans_identifiant(self):
        self.posts[PAGE_A] = [post_avec("c-secret-1", nom="Awa Kone")]
        tampon = io.StringIO()
        with contextlib.redirect_stdout(tampon):
            bilan = mp.traiter_page(gm, self.ctx(dry=False), CFG, sauver=lambda msg, chemins: True)
        sortie = tampon.getvalue() + repr(bilan)
        for interdit in ("c-secret-1", "Awa", PAGE_A, UID_A, jeton(PAGE_A), "visiteur"):
            self.assertNotIn(interdit, sortie)
        self.assertEqual(tampon.getvalue(), "")

    def test_sans_silence_la_sortie_existe(self):
        self.posts[PAGE_A] = [post_avec("c1")]
        tampon = io.StringIO()
        with contextlib.redirect_stdout(tampon):
            mp.traiter_page(gm, self.ctx(), CFG, silencieux=False)
        self.assertIn("Réponses", tampon.getvalue())


class TestCycle(Base):
    def bilan_ok(self, reponses=0):
        return {"etiquette": "x", "statut": "ok", "reponses": reponses, "a_traiter": 0,
                "erreurs": 0, "quota_pct": 0}

    def test_exception_d_une_page_ne_bloque_pas_les_autres(self):
        contextes = [self.ctx(UID_A, PAGE_A, etiquette="Page 1"), self.ctx(UID_B, PAGE_B, etiquette="Page 2"),
                     self.ctx("UC3", "1111111", etiquette="Page 3")]
        appels = []

        def faux(gm_, ctx, cfg, sauver=None, silencieux=True):
            appels.append(ctx.etiquette)
            if ctx.etiquette == "Page 2":
                raise ValueError("boom identifiant 999")
            return self.bilan_ok(2)

        tampon = io.StringIO()
        horloge = [1000.0]
        etats = {}
        with mock.patch.object(mp, "traiter_page", faux), contextlib.redirect_stdout(tampon):
            bilans = mp.executer_cycle(gm, contextes, CFG, etats, maintenant=lambda: horloge[0])
            self.assertEqual([b["statut"] for b in bilans], ["ok", "exception", "ok"])
            self.assertEqual(bilans[1]["type"], "ValueError")
            self.assertEqual(appels, ["Page 1", "Page 2", "Page 3"])
            self.assertEqual(etats[(UID_B, PAGE_B)].echecs, 1)
            # 2e cycle, même heure : la Page fautive est en pause, les autres continuent
            bilans = mp.executer_cycle(gm, contextes, CFG, etats, maintenant=lambda: horloge[0])
            self.assertEqual([b["statut"] for b in bilans], ["ok", "pause", "ok"])
            # 3e cycle, après la pause : la Page est retentée, la pause double
            horloge[0] += 61
            mp.executer_cycle(gm, contextes, CFG, etats, maintenant=lambda: horloge[0])
            self.assertEqual(etats[(UID_B, PAGE_B)].echecs, 2)
            self.assertEqual(etats[(UID_B, PAGE_B)].pause_jusqua, horloge[0] + 120)
        sortie = tampon.getvalue()
        self.assertIn("Multi-Pages : 3 Page(s) traitée(s), 4 réponse(s), 1 en erreur, 0 en pause.", sortie)
        for interdit in ("999", PAGE_A, UID_A, "boom"):
            self.assertNotIn(interdit, sortie)

    def test_exception_reelle_restaure_gm(self):
        def casse():
            raise RuntimeError("boom identifiant 999")

        self.posts[PAGE_B] = [post_avec("cB")]
        contextes = [self.ctx(UID_A, PAGE_A), self.ctx(UID_B, PAGE_B, etiquette="Page 2")]
        tampon = io.StringIO()
        with mock.patch.object(gm, "fb_posts", lambda: casse() if gm.PAGE_ID == PAGE_A else (200, {"data": self.posts[PAGE_B]})), \
                contextlib.redirect_stdout(tampon):
            bilans = mp.executer_cycle(gm, contextes, CFG, {})
        self.assertEqual([b["statut"] for b in bilans], ["exception", "ok"])
        self.assertEqual(self.glm.appels, 1)
        self.assertNotIn("999", tampon.getvalue() + repr(bilans))
        self.assert_gm_restaure()

    def test_isolation_des_donnees_et_des_jetons(self):
        self.posts[PAGE_A] = [post_avec("cA")]
        self.posts[PAGE_B] = [post_avec("cB")]
        a, b = self.ctx(UID_A, PAGE_A, dry=False), self.ctx(UID_B, PAGE_B, dry=False, etiquette="Page 2")
        tampon = io.StringIO()
        with contextlib.redirect_stdout(tampon):
            bilans = mp.executer_cycle(gm, [a, b], CFG, {}, sauver=lambda msg, chemins: True)
        self.assertEqual([x["reponses"] for x in bilans], [1, 1])
        self.assertEqual(self.envoyes, [(PAGE_A, jeton(PAGE_A), "cA", "Merci beaucoup !"),
                                        (PAGE_B, jeton(PAGE_B), "cB", "Merci beaucoup !")])
        for ctx, propre, autre in ((a, "cA", "cB"), (b, "cB", "cA")):
            replies = [r["comment_id"] for r in self.lire(ctx, "posts", "replies.json")]
            registre = [r["comment_id"] for r in self.lire(ctx, "inbox", "commentaires-traites.json")]
            self.assertEqual(replies, [propre])
            self.assertEqual(registre, [propre])
            self.assertNotIn(autre, json.dumps(self.lire(ctx, "glm", "etat.json")))
        self.assertNotEqual(a.dossier, b.dossier)
        sortie = tampon.getvalue() + repr(bilans)
        for interdit in (jeton(PAGE_A), jeton(PAGE_B), PAGE_A, PAGE_B, UID_A, UID_B, "cA", "cB"):
            self.assertNotIn(interdit, sortie)
        self.assert_gm_restaure()

    def test_liste_vide(self):
        tampon = io.StringIO()
        with contextlib.redirect_stdout(tampon):
            self.assertEqual(mp.executer_cycle(gm, [], CFG, {}), [])
        self.assertIn("0 Page(s) traitée(s)", tampon.getvalue())


if __name__ == "__main__":
    unittest.main()
