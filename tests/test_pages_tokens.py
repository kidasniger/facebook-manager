import base64
import datetime as dt
import json
import os
import sys
import types
import unittest
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import pages_crypto as pc
import pages_tokens as pt

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    CRYPTO = True
except ImportError:
    CRYPTO = False

DOC = "projects/proj-test/databases/(default)/documents/"
UID, PAGE = "U1", "1234567890"
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
    def __init__(self):
        self.appels = []
        self.echange = (200, {"access_token": LONG})
        self.comptes = [(200, {"data": [{"id": PAGE, "access_token": JETON_PAGE}]})]

    def __call__(self, url):
        self.appels.append(url)
        if "/oauth/access_token?" in url:
            return self.echange
        if len(self.comptes) > 1:
            return self.comptes.pop(0)
        return self.comptes[0]

    def requete(self, i):
        return urllib.parse.parse_qs(urllib.parse.urlparse(self.appels[i]).query)


class FauxFs:
    def __init__(self, docs=None):
        self.docs = docs or {}
        self.lus = []

    def lire_document(self, chemin):
        self.lus.append(chemin)
        return self.docs.get(chemin)


@unittest.skipUnless(CRYPTO, "cryptography absent")
class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cle = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.cle_stock = os.urandom(32)

    def setUp(self):
        self.fb = FauxFacebook()
        self.masques = []
        self.pauses = []
        self.ctx = pt.Contexte(
            projet="proj-test", graph="https://graph.facebook.com/v26.0", app_id="APP-1",
            app_secret=APP_SECRET, cle_privee=self.cle, cle_stockage=self.cle_stock,
            get_json=self.fb, maintenant=lambda: dt.datetime(2026, 11, 1, 10, 0, tzinfo=dt.timezone.utc),
            dormir=self.pauses.append, masquer=self.masques.append)

    def rec(self, **extra):
        base = {"uid": UID, "page_id": PAGE, "nom": "Nom", "demande": "connecter",
                "tokenEnc": chiffrer_comme_l_appli(COURT, self.cle.public_key())}
        base.update(extra)
        return base

    def assert_sans_fuite(self, res):
        texte = json.dumps(res.ecritures) + res.message + repr(res)
        for secret in (COURT, LONG, JETON_PAGE, APP_SECRET):
            if secret == JETON_PAGE and res.issue == "active":
                continue  # le jeton de Page n'existe qu'en mémoire dans jeton_page
            self.assertNotIn(secret, texte)
        self.assertNotIn(JETON_PAGE, json.dumps(res.ecritures))
        self.assertNotIn(JETON_PAGE, repr(res))


class TestConnexion(Base):
    def test_flux_complet(self):
        res = pt.preparer_demande(self.ctx, self.rec())
        self.assertEqual(res.issue, "active")
        self.assertEqual(res.jeton_page, JETON_PAGE)
        self.assertEqual(len(res.ecritures), 2)
        stock, statut = res.ecritures
        self.assertEqual(stock["update"]["name"], DOC + f"bot_tokens/{UID}__{PAGE}")
        enc = stock["update"]["fields"]["tokenEnc"]["stringValue"]
        self.assertTrue(enc.startswith("v1."))
        self.assertEqual(pc.dechiffrer_stockage(enc, self.cle_stock, UID, PAGE), JETON_PAGE)
        self.assertEqual(stock["update"]["fields"]["maj"], {"timestampValue": "2026-11-01T10:00:00.000000Z"})
        self.assertEqual(statut["update"]["name"], DOC + f"users/{UID}/pages/{PAGE}")
        self.assertEqual(statut["update"]["fields"], {"status": {"stringValue": "active"}})
        self.assertEqual(statut["updateMask"]["fieldPaths"], ["status", "demande", "tokenEnc", "message"])
        self.assertEqual(statut["currentDocument"], {"exists": True})
        self.assertNotIn("maj", statut["updateMask"]["fieldPaths"])
        self.assert_sans_fuite(res)

    def test_ordre_des_jetons_et_secret_applicatif(self):
        pt.preparer_demande(self.ctx, self.rec())
        echange, comptes = self.fb.requete(0), self.fb.requete(1)
        self.assertEqual(echange["grant_type"], ["fb_exchange_token"])
        self.assertEqual(echange["client_id"], ["APP-1"])
        self.assertEqual(echange["client_secret"], [APP_SECRET])
        self.assertEqual(echange["fb_exchange_token"], [COURT])
        self.assertEqual(comptes["access_token"], [LONG])
        self.assertEqual(comptes["fields"], ["id,access_token"])
        self.assertEqual(len(self.fb.appels), 2)

    def test_jetons_masques_dans_l_ordre(self):
        pt.preparer_demande(self.ctx, self.rec())
        self.assertEqual(self.masques, [COURT, LONG, JETON_PAGE])

    def test_precondition_update_time(self):
        res = pt.preparer_demande(self.ctx, self.rec(_update_time="2026-11-01T09:59:59.123456Z"))
        self.assertEqual(res.ecritures[1]["currentDocument"], {"updateTime": "2026-11-01T09:59:59.123456Z"})

    def test_pagination_des_comptes(self):
        self.fb.comptes = [(200, {"data": [{"id": "999", "access_token": "autre"}],
                                  "paging": {"next": "https://next.test/page2"}}),
                           (200, {"data": [{"id": PAGE, "access_token": JETON_PAGE}]})]
        res = pt.preparer_demande(self.ctx, self.rec())
        self.assertEqual(res.issue, "active")
        self.assertEqual(res.jeton_page, JETON_PAGE)

    def test_repr_sans_secret(self):
        self.assertNotIn(APP_SECRET, repr(self.ctx))
        self.assertNotIn("PRIVATE", repr(self.ctx))
        res = pt.preparer_demande(self.ctx, self.rec())
        self.assertNotIn(JETON_PAGE, repr(res))


class TestEchecs(Base):
    def attendu_erreur(self, res, message):
        self.assertEqual(res.issue, "erreur")
        self.assertEqual(res.message, message)
        statut, suppression = res.ecritures
        self.assertEqual(statut["update"]["fields"], {"status": {"stringValue": "erreur"},
                                                      "message": {"stringValue": message}})
        self.assertEqual(statut["updateMask"]["fieldPaths"], ["status", "message", "demande", "tokenEnc"])
        self.assertEqual(suppression, {"delete": DOC + f"bot_tokens/{UID}__{PAGE}"})
        self.assertLessEqual(len(message), 80)
        self.assert_sans_fuite(res)

    def test_page_introuvable(self):
        self.fb.comptes = [(200, {"data": [{"id": "999", "access_token": "x"}]})]
        self.attendu_erreur(pt.preparer_demande(self.ctx, self.rec()), pt.MESSAGES["introuvable"])

    def test_page_listee_sans_jeton(self):
        self.fb.comptes = [(200, {"data": [{"id": PAGE}]})]
        self.attendu_erreur(pt.preparer_demande(self.ctx, self.rec()), pt.MESSAGES["permission"])

    def test_jeton_expire_a_l_echange(self):
        self.fb.echange = (400, {"error": {"code": 190, "message": f"jeton {COURT} invalide"}})
        res = pt.preparer_demande(self.ctx, self.rec())
        self.attendu_erreur(res, pt.MESSAGES["jeton"])
        self.assertEqual(self.masques, [COURT])
        self.assertEqual(len(self.fb.appels), 1)

    def test_permission_refusee(self):
        self.fb.echange = (403, {"error": {"code": 10, "message": "x"}})
        self.attendu_erreur(pt.preparer_demande(self.ctx, self.rec()), pt.MESSAGES["permission"])

    def test_refus_definitif_avec_code(self):
        self.fb.echange = (400, {"error": {"code": 100, "message": "x"}})
        self.attendu_erreur(pt.preparer_demande(self.ctx, self.rec()), "Facebook a refusé la demande (code 100).")

    def test_erreur_definitive_sur_les_comptes(self):
        self.fb.comptes = [(400, {"error": {"code": 190, "message": "x"}})]
        self.attendu_erreur(pt.preparer_demande(self.ctx, self.rec()), pt.MESSAGES["jeton"])
        self.assertEqual(len(self.fb.appels), 2)

    def test_temporaires_sans_ecriture(self):
        cas = [(400, {"error": {"code": 4}}), (429, {}), (500, {}), (0, {"erreur": "URLError"}),
               (400, {"error": {"code": 80001}}), (200, {})]
        for reponse in cas:
            self.fb.echange = reponse
            res = pt.preparer_demande(self.ctx, self.rec())
            self.assertEqual(res.issue, "attente", reponse)
            self.assertEqual(res.ecritures, [])
            self.assertTrue(res.message)
            self.assert_sans_fuite(res)

    def test_temporaire_sur_les_comptes_avec_reprises(self):
        self.fb.comptes = [(200, {"error": {"code": 1, "message": "x"}})]
        res = pt.preparer_demande(self.ctx, self.rec())
        self.assertEqual(res.issue, "attente")
        self.assertEqual(res.ecritures, [])
        self.assertEqual(self.pauses, [1.0, 2.0])
        self.assertEqual(len(self.fb.appels), 4)

    def test_jeton_chiffre_absent_ou_altere(self):
        for enc, message in ((None, pt.MESSAGES["incomplete"]), ("", pt.MESSAGES["incomplete"]),
                             ("pas du json " + COURT, pt.MESSAGES["illisible"]),
                             (chiffrer_comme_l_appli(COURT, rsa.generate_private_key(
                                 public_exponent=65537, key_size=2048).public_key()), pt.MESSAGES["illisible"])):
            res = pt.preparer_demande(self.ctx, self.rec(tokenEnc=enc))
            self.attendu_erreur(res, message)
            self.assertEqual(self.fb.appels, [])
            self.assertEqual(self.masques, [])


class TestDeconnexionEtIgnores(Base):
    def test_deconnexion(self):
        res = pt.preparer_demande(self.ctx, self.rec(demande="deconnecter", tokenEnc=None))
        self.assertEqual(res.issue, "deconnectee")
        statut, suppression = res.ecritures
        self.assertEqual(statut["update"]["fields"], {"status": {"stringValue": "deconnectee"}})
        self.assertEqual(statut["updateMask"]["fieldPaths"], ["status", "demande", "tokenEnc", "message"])
        self.assertEqual(suppression, {"delete": DOC + f"bot_tokens/{UID}__{PAGE}"})
        self.assertEqual(self.fb.appels, [])

    def test_demande_absente_ou_inconnue(self):
        for demande in (None, "", "autre"):
            res = pt.preparer_demande(self.ctx, self.rec(demande=demande))
            self.assertEqual((res.issue, res.ecritures), ("ignoree", []))
        self.assertEqual(self.fb.appels, [])

    def test_identifiants_invalides(self):
        for uid, page in (("a/b", PAGE), ("", PAGE), (UID, "abc"), (UID, "12"), (UID, ""), ("a__b", PAGE)):
            res = pt.preparer_demande(self.ctx, self.rec(uid=uid, page_id=page))
            self.assertEqual((res.issue, res.ecritures), ("ignoree", []), (uid, page))
        self.assertEqual(self.fb.appels, [])


class TestLireJetonPage(Base):
    def stockage(self):
        res = pt.preparer_demande(self.ctx, self.rec())
        champs = res.ecritures[0]["update"]["fields"]
        return FauxFs({f"bot_tokens/{UID}__{PAGE}": {"tokenEnc": champs["tokenEnc"]["stringValue"]}})

    def test_relecture(self):
        fs = self.stockage()
        self.masques.clear()
        self.assertEqual(pt.lire_jeton_page(self.ctx, fs, UID, PAGE), JETON_PAGE)
        self.assertEqual(self.masques, [JETON_PAGE])

    def test_absent_ou_autre_page(self):
        fs = self.stockage()
        self.assertIsNone(pt.lire_jeton_page(self.ctx, FauxFs(), UID, PAGE))
        fs.docs[f"bot_tokens/{UID}__9999"] = fs.docs[f"bot_tokens/{UID}__{PAGE}"]
        self.assertIsNone(pt.lire_jeton_page(self.ctx, fs, UID, "9999"))   # lié à une autre Page
        fs.docs[f"bot_tokens/{UID}__{PAGE}"] = {"tokenEnc": ""}
        self.assertIsNone(pt.lire_jeton_page(self.ctx, fs, UID, PAGE))

    def test_identifiants_invalides_sans_lecture(self):
        fs = FauxFs()
        self.assertIsNone(pt.lire_jeton_page(self.ctx, fs, "a/b", PAGE))
        self.assertIsNone(pt.lire_jeton_page(self.ctx, fs, UID, "abc"))
        self.assertEqual(fs.lus, [])


class TestClasserEtEnv(Base):
    def test_table(self):
        cas = [((400, 190), True), ((400, "463"), True), ((400, 10), True), ((403, 200), True),
               ((400, 100), True), ((400, 4), False), ((400, 613), False), ((400, 80004), False),
               ((429, None), False), ((503, None), False), ((0, None), False), ((400, 2), False)]
        for (statut, code), definitive in cas:
            self.assertEqual(pt.classer_erreur(statut, code).definitive, definitive, (statut, code))

    def env(self, **extra):
        pem = self.cle.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()).decode()
        env = {"FB_APP_ID": "APP-1", "FB_APP_SECRET": APP_SECRET, "MYPAGE_RSA_PRIVATE_KEY": pem,
               "MYPAGE_STORE_KEY": base64.b64encode(self.cle_stock).decode()}
        env.update(extra)
        return env

    def test_contexte_complet(self):
        ctx = pt.contexte_depuis_env(self.env(), types.SimpleNamespace(projet="proj-test"), self.fb)
        self.assertEqual(ctx.graph, "https://graph.facebook.com/v26.0")
        self.assertEqual(ctx.projet, "proj-test")
        self.assertNotIn(APP_SECRET, repr(ctx))
        ctx2 = pt.contexte_depuis_env(self.env(FB_GRAPH_VERSION="v27.0"), types.SimpleNamespace(projet="p"), self.fb)
        self.assertEqual(ctx2.graph, "https://graph.facebook.com/v27.0")

    def test_variable_manquante_nommee_sans_valeur(self):
        for nom in ("FB_APP_ID", "FB_APP_SECRET", "MYPAGE_RSA_PRIVATE_KEY", "MYPAGE_STORE_KEY"):
            env = self.env()
            del env[nom]
            with self.assertRaises(ValueError) as ctx:
                pt.contexte_depuis_env(env, types.SimpleNamespace(projet="p"), self.fb)
            self.assertIn(nom, str(ctx.exception))
            self.assertNotIn(APP_SECRET, str(ctx.exception))

    def test_cle_illisible_sans_fuite(self):
        with self.assertRaises(pc.ErreurChiffrement) as ctx:
            pt.contexte_depuis_env(self.env(MYPAGE_RSA_PRIVATE_KEY="SECRET-pas-une-cle"),
                                   types.SimpleNamespace(projet="p"), self.fb)
        self.assertNotIn("SECRET", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
