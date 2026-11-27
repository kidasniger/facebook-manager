import base64
import contextlib
import datetime as dt
import io
import os
import sys
import tempfile
import unittest
import urllib.parse
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import multi_pages as mp
import multi_runner as mr
import pages_crypto as pc
import pages_registry as pr
import pages_tokens as pt
import json

try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    CRYPTO = True
except ImportError:
    CRYPTO = False

DOC = "projects/proj-test/databases/(default)/documents/"
UID, PAGE = "UA1", "1234567890"
UID2, PAGE2 = "UB2", "7654321"
PAGE1 = "1111111"
COURT, LONG, JETON_PAGE = "EAAcourt-SECRET-1", "EAAlong-SECRET-2", "EAApage-SECRET-3"
APP_SECRET = "APPSECRET-xyz"


def chiffrer_comme_l_appli(clair, pub):
    cle, iv = os.urandom(32), os.urandom(12)
    sortie = AESGCM(cle).encrypt(iv, clair.encode(), None)
    ek = pub.encrypt(cle, padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                                       algorithm=hashes.SHA256(), label=None))
    b = lambda x: base64.b64encode(x).decode()
    return json.dumps({"v": 1, "alg": pc.ALG, "ek": b(ek), "iv": b(iv),
                       "ct": b(sortie[:-16]), "tag": b(sortie[-16:])})


class FauxFacebook:
    def __init__(self, page_id=PAGE):
        self.appels = []
        self.echange = (200, {"access_token": LONG})
        self.comptes = [(200, {"data": [{"id": page_id, "access_token": JETON_PAGE}]})]

    def __call__(self, url):
        self.appels.append(url)
        if "/oauth/access_token?" in url:
            return self.echange
        if len(self.comptes) > 1:
            return self.comptes.pop(0)
        return self.comptes[0]


class FauxFs:
    projet = "proj-test"

    def __init__(self):
        self.docs, self.tokens = {}, {}
        self.commits, self.lus = [], []
        self.lectures = self.ecritures = 0
        self.echec_commit = self.echec_lecture = self.echec_requete = None

    def ajouter(self, uid, pid, maj="2026-11-01T10:00:00.000000Z", **champs):
        fields = {"maj": {"timestampValue": maj}}
        for cle, valeur in champs.items():
            fields[cle] = pr.encoder_valeur(valeur)
        self.docs[(uid, pid)] = {"name": f"{DOC}users/{uid}/pages/{pid}", "fields": fields, "updateTime": maj}

    def requete(self, requete):
        if self.echec_requete:
            raise self.echec_requete
        docs = sorted(self.docs.values(), key=lambda d: d["fields"]["maj"]["timestampValue"])
        self.lectures += max(1, len(docs))
        return docs

    def lire_document(self, chemin):
        self.lus.append(chemin)
        self.lectures += 1
        if self.echec_lecture:
            raise self.echec_lecture
        return self.tokens.get(chemin)

    def commit(self, ecritures):
        if self.echec_commit:
            raise self.echec_commit
        self.commits.append(ecritures)
        self.ecritures += len(ecritures)
        return len(ecritures)


@unittest.skipUnless(CRYPTO, "cryptography absent")
class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cle = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.cle_stock = os.urandom(32)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = os.path.join(self.tmp.name, "data")
        self.fs, self.fb, self.h = FauxFs(), FauxFacebook(), [1_000_000.0]
        self.ctx_tokens = pt.Contexte(
            projet="proj-test", graph="https://graph.facebook.com/v26.0", app_id="APP-1",
            app_secret=APP_SECRET, cle_privee=self.cle, cle_stockage=self.cle_stock, get_json=self.fb,
            maintenant=lambda: dt.datetime(2026, 11, 1, 10, 0, tzinfo=dt.timezone.utc),
            dormir=lambda s: None, masquer=lambda v: None)
        self.runner = mr.Runner(self.fs, self.ctx_tokens, self.base, page1_id=PAGE1, quota_ia=7,
                                maintenant=lambda: self.h[0])
        self.vus = []

    def ajouter_demande(self, uid=UID, page=PAGE, demande="connecter", **extra):
        champs = {"nom": "Nom secret", "demande": demande}
        if demande == "connecter":
            champs["tokenEnc"] = chiffrer_comme_l_appli(COURT, self.cle.public_key())
        champs.update(extra)
        self.fs.ajouter(uid, page, **champs)
        self.runner.registre.rafraichir()

    def ajouter_active(self, uid=UID, page=PAGE, stocke=True, **extra):
        self.fs.ajouter(uid, page, nom="Nom secret", status="active", **extra)
        if stocke:
            self.fs.tokens[f"bot_tokens/{uid}__{page}"] = {
                "tokenEnc": pc.chiffrer_stockage(JETON_PAGE, self.cle_stock, uid, page)}
        self.runner.registre.rafraichir()

    def faux_traiter_page(self, gm_, ctx, cfg, sauver=None, silencieux=True):
        self.vus.append(ctx)
        return {"etiquette": ctx.etiquette, "statut": "ok", "reponses": 1, "a_traiter": 0,
                "erreurs": 0, "quota_pct": 0}


class TestDemandes(Base):
    def test_connexion(self):
        self.ajouter_demande()
        bilan = self.runner.traiter_demandes()
        self.assertEqual((bilan["demandes"], bilan["connectees"], bilan["erreurs"], bilan["refus"]), (1, 1, 0, 0))
        self.assertEqual(len(self.fs.commits), 1)
        stock, statut = self.fs.commits[0]
        self.assertEqual(stock["update"]["name"], f"{DOC}bot_tokens/{UID}__{PAGE}")
        self.assertEqual(statut["update"]["name"], f"{DOC}users/{UID}/pages/{PAGE}")
        self.assertEqual(self.runner.jetons[(UID, PAGE)], JETON_PAGE)
        rec = self.runner.registre.pages[(UID, PAGE)]
        self.assertEqual(rec["status"], "active")
        self.assertNotIn("demande", rec)
        self.assertNotIn("tokenEnc", rec)
        self.assertEqual([r["page_id"] for r in self.runner.registre.actives()], [PAGE])

    def test_page_1_jamais_servie(self):
        self.ajouter_demande(page=PAGE1)
        bilan = self.runner.traiter_demandes()
        self.assertEqual(bilan["demandes"], 0)
        self.assertEqual((self.fs.commits, self.fb.appels), ([], []))
        self.ajouter_active(page=PAGE1)
        self.assertEqual(self.runner.contextes(), ([], 0))
        self.assertEqual(self.fs.lus, [])

    def test_deconnexion(self):
        self.ajouter_demande(demande="deconnecter")
        self.runner.jetons[(UID, PAGE)] = "en-memoire"
        bilan = self.runner.traiter_demandes()
        self.assertEqual((bilan["deconnectees"], bilan["connectees"]), (1, 0))
        statut, suppression = self.fs.commits[0]
        self.assertEqual(suppression, {"delete": f"{DOC}bot_tokens/{UID}__{PAGE}"})
        self.assertNotIn((UID, PAGE), self.runner.jetons)
        self.assertEqual(self.runner.registre.pages[(UID, PAGE)]["status"], "deconnectee")
        self.assertEqual(self.fb.appels, [])

    def test_echec_definitif(self):
        self.fb.comptes = [(200, {"data": [{"id": "999", "access_token": "x"}]})]
        self.ajouter_demande()
        bilan = self.runner.traiter_demandes()
        self.assertEqual((bilan["erreurs"], bilan["connectees"]), (1, 0))
        statut, suppression = self.fs.commits[0]
        self.assertIn("delete", suppression)
        rec = self.runner.registre.pages[(UID, PAGE)]
        self.assertEqual((rec["status"], rec["message"]), ("erreur", pt.MESSAGES["introuvable"]))
        self.assertEqual(self.runner.jetons, {})

    def test_echec_temporaire_puis_reprise(self):
        self.fb.echange = (429, {})
        self.ajouter_demande()
        premier = self.runner.traiter_demandes()
        self.assertEqual((premier["demandes"], premier["attente"]), (1, 1))
        self.assertEqual(self.fs.commits, [])
        self.assertEqual(self.runner.traiter_demandes()["demandes"], 0)      # pas de rafale
        self.h[0] += mr.RETARD_SECONDES + 1
        self.fb.echange = (200, {"access_token": LONG})
        self.assertEqual(self.runner.traiter_demandes()["connectees"], 1)
        self.assertEqual(len(self.fs.commits), 1)

    def test_commit_refuse_puis_reprise(self):
        self.fs.echec_commit = pr.FirestoreError("Firestore HTTP 403", status=403)
        self.ajouter_demande()
        bilan = self.runner.traiter_demandes()
        self.assertEqual((bilan["refus"], bilan["connectees"]), (1, 0))
        self.assertEqual(self.runner.jetons, {})
        self.assertEqual(self.runner.registre.pages[(UID, PAGE)]["demande"], "connecter")
        self.assertEqual(self.runner.traiter_demandes()["demandes"], 0)
        self.h[0] += mr.RETARD_SECONDES + 1
        self.fs.echec_commit = None
        self.assertEqual(self.runner.traiter_demandes()["connectees"], 1)

    def test_identifiants_invalides_ignores(self):
        self.ajouter_demande(page="abc")
        bilan = self.runner.traiter_demandes()
        self.assertEqual((self.fs.commits, self.fb.appels), ([], []))
        self.assertEqual(bilan["connectees"], 0)


class TestContextes(Base):
    def test_jeton_lu_dans_bot_tokens_une_seule_fois(self):
        self.ajouter_active()
        contextes, sans_jeton = self.runner.contextes()
        self.assertEqual((len(contextes), sans_jeton), (1, 0))
        self.assertEqual(contextes[0]._token, JETON_PAGE)
        self.assertEqual(contextes[0].etiquette, "Page 2")
        self.assertEqual(self.fs.lus, [f"bot_tokens/{UID}__{PAGE}", f"users/{UID}/profil/{PAGE}"])
        self.runner.contextes()
        self.assertEqual(len(self.fs.lus), 2)           # relu depuis la mémoire

    def test_sans_jeton_lisible(self):
        self.ajouter_active(stocke=False)
        self.assertEqual(self.runner.contextes(), ([], 1))
        self.fs.tokens[f"bot_tokens/{UID}__{PAGE}"] = {
            "tokenEnc": pc.chiffrer_stockage(JETON_PAGE, self.cle_stock, UID, "9999999")}   # lié à une autre Page
        self.assertEqual(self.runner.contextes(), ([], 1))

    def test_erreur_de_lecture_firestore(self):
        self.ajouter_active()
        self.fs.echec_lecture = pr.FirestoreError("Firestore HTTP 500", status=500)
        self.assertEqual(self.runner.contextes(), ([], 1))

    def test_deux_pages_numerotees_et_triees(self):
        self.ajouter_active(UID2, PAGE2)
        self.ajouter_active(UID, PAGE)
        contextes, _ = self.runner.contextes()
        self.assertEqual([c.etiquette for c in contextes], ["Page 2", "Page 3"])
        self.assertEqual([c.uid for c in contextes], [UID, UID2])

    def test_profil_et_dossier(self):
        self.ajouter_active(description="Vend des tissus.")
        ctx = self.runner.contextes()[0][0]
        self.assertEqual(ctx.profil, {"nom": "Nom secret", "description": "Vend des tissus."})
        self.assertEqual(ctx.dossier, os.path.join(os.path.abspath(self.base), f"{UID}__{PAGE}"))
        self.assertTrue(os.path.isdir(ctx.dossier))

    def test_profil_explicite(self):
        self.ajouter_active(profil={"description": "D", "reponses": {"ton": "direct"}})
        ctx = self.runner.contextes()[0][0]
        self.assertEqual(ctx.profil, {"description": "D", "reponses": {"ton": "direct"}, "nom": "Nom secret"})

    def test_profil_lu_dans_son_document(self):
        self.ajouter_active(description="Ancienne")
        self.fs.tokens[f"users/{UID}/profil/{PAGE}"] = {
            "description": "Nouvelle", "reponses": {"ton": "direct"}, "ignore": None}
        ctx = self.runner.contextes()[0][0]
        self.assertEqual(ctx.profil, {"description": "Nouvelle", "reponses": {"ton": "direct"},
                                      "nom": "Nom secret"})

    def test_profil_relu_apres_delai(self):
        self.ajouter_active()
        chemin = f"users/{UID}/profil/{PAGE}"
        self.fs.tokens[chemin] = {"description": "V1"}
        self.assertEqual(self.runner.contextes()[0][0].profil["description"], "V1")
        self.fs.tokens[chemin] = {"description": "V2"}
        self.assertEqual(self.runner.contextes()[0][0].profil["description"], "V1")   # en mémoire
        self.h[0] += mr.PROFIL_TTL + 1
        self.assertEqual(self.runner.contextes()[0][0].profil["description"], "V2")
        self.assertEqual(self.fs.lus.count(chemin), 2)

    def test_profil_erreur_firestore_garde_l_ancien(self):
        self.ajouter_active()
        self.fs.tokens[f"users/{UID}/profil/{PAGE}"] = {"description": "V1"}
        self.runner.contextes()
        self.h[0] += mr.PROFIL_TTL + 1
        self.fs.echec_lecture = pr.FirestoreError("Firestore HTTP 500", status=500)
        contextes, sans_jeton = self.runner.contextes()
        self.assertEqual((len(contextes), sans_jeton), (1, 0))
        self.assertEqual(contextes[0].profil["description"], "V1")

    def test_quota_ia_du_jour(self):
        self.ajouter_active()
        premier = self.runner.contextes()[0][0].quota
        self.assertEqual(premier.limite, 7)
        self.assertIs(self.runner.contextes()[0][0].quota, premier)
        premier.utilise = 7
        self.h[0] += 86400
        suivant = self.runner.contextes()[0][0].quota
        self.assertIsNot(suivant, premier)
        self.assertEqual(suivant.utilise, 0)


class TestCycle(Base):
    def test_cycle_complet(self):
        self.ajouter_demande()
        tampon = io.StringIO()
        with mock.patch.object(mp, "traiter_page", self.faux_traiter_page), contextlib.redirect_stdout(tampon):
            res = self.runner.cycle(object(), {})
        self.assertEqual(res["statut"], "ok")
        self.assertEqual(res["demandes"]["connectees"], 1)
        self.assertEqual(len(res["bilans"]), 1)
        self.assertEqual((self.vus[0]._token, self.vus[0].etiquette), (JETON_PAGE, "Page 2"))
        sortie = tampon.getvalue()
        self.assertIn("1 connectée(s)", sortie)
        self.assertIn("2 écriture(s)", sortie)
        for interdit in (UID, PAGE, JETON_PAGE, COURT, LONG, APP_SECRET, "Nom secret"):
            self.assertNotIn(interdit, sortie + repr(res["bilans"]))

    def test_erreur_firestore(self):
        self.fs.echec_requete = pr.FirestoreError("Firestore HTTP 403 SECRET", status=403)
        tampon = io.StringIO()
        with mock.patch.object(mp, "executer_cycle") as executer, contextlib.redirect_stdout(tampon):
            res = self.runner.cycle(object(), {})
        self.assertEqual((res["statut"], res["bilans"]), ("firestore", []))
        executer.assert_not_called()
        self.assertIn("HTTP 403", tampon.getvalue())
        self.assertNotIn("SECRET", tampon.getvalue())

    def test_exception_inattendue(self):
        self.fs.echec_requete = RuntimeError("boom identifiant 999")
        tampon = io.StringIO()
        with mock.patch.object(mp, "executer_cycle") as executer, contextlib.redirect_stdout(tampon):
            res = self.runner.cycle(object(), {})
        self.assertEqual(res["statut"], "exception")
        executer.assert_not_called()
        self.assertIn("RuntimeError", tampon.getvalue())
        self.assertNotIn("999", tampon.getvalue())

    def test_page_active_deja_connectee(self):
        self.ajouter_active()
        with mock.patch.object(mp, "traiter_page", self.faux_traiter_page), \
                contextlib.redirect_stdout(io.StringIO()):
            res = self.runner.cycle(object(), {})
        self.assertEqual((res["demandes"]["demandes"], res["sans_jeton"], len(self.vus)), (0, 0, 1))
        self.assertEqual(self.fs.commits, [])

    def test_une_page_sans_jeton_ne_bloque_pas_les_autres(self):
        self.ajouter_active(UID, PAGE, stocke=False)
        self.ajouter_active(UID2, PAGE2)
        with mock.patch.object(mp, "traiter_page", self.faux_traiter_page), \
                contextlib.redirect_stdout(io.StringIO()):
            res = self.runner.cycle(object(), {})
        self.assertEqual((res["sans_jeton"], [c.uid for c in self.vus]), (1, [UID2]))


if __name__ == "__main__":
    unittest.main()
