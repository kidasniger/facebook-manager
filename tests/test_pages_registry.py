import base64
import json
import os
import sys
import unittest
import urllib.parse
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import pages_registry as pr

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    CRYPTO = True
except ImportError:
    CRYPTO = False

COMPTE = {"client_email": "bot@proj-test.iam.gserviceaccount.com",
          "private_key": "PEM-DE-TEST", "project_id": "proj-test"}


def b64dec(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Horloge:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


class Serveur:
    """Faux Firestore + faux serveur de jetons Google."""

    def __init__(self, pub=None):
        self.docs = {}
        self.requetes = []
        self.jetons = 0
        self.appels = 0
        self.pub = pub
        self.charge = None
        self.forcees = []

    def ajouter(self, uid, pid, maj, **champs):
        fields = {"maj": {"timestampValue": maj}}
        for cle, val in champs.items():
            fields[cle] = {"stringValue": val}
        self.docs[(uid, pid)] = {
            "name": f"projects/proj-test/databases/(default)/documents/users/{uid}/pages/{pid}",
            "fields": fields}

    def __call__(self, methode, url, en_tetes, corps):
        self.appels += 1
        if url == pr.TOKEN_URL:
            self.jetons += 1
            assertion = urllib.parse.parse_qs(corps.decode())["assertion"][0]
            entete, charge, sig = assertion.split(".")
            if self.pub is not None:
                self.pub.verify(b64dec(sig), f"{entete}.{charge}".encode(),
                                padding.PKCS1v15(), hashes.SHA256())
            self.charge = json.loads(b64dec(charge))
            return 200, {"access_token": "jeton-test", "expires_in": 3600}
        if self.forcees:
            return self.forcees.pop(0)
        assert en_tetes["Authorization"] == "Bearer jeton-test"
        sq = json.loads(corps)["structuredQuery"]
        self.requetes.append(sq)
        docs = sorted(self.docs.values(), key=lambda d: d["fields"]["maj"]["timestampValue"])
        w = sq.get("where")
        if w:
            seuil = w["fieldFilter"]["value"]["timestampValue"]
            docs = [d for d in docs if d["fields"]["maj"]["timestampValue"] >= seuil]
        docs = docs[: sq["limit"]]
        if not docs:
            return 200, [{"readTime": "2026-11-01T00:00:00Z"}]
        return 200, [{"document": d} for d in docs]


class Base(unittest.TestCase):
    def setUp(self):
        original = pr._signer_rs256
        pr._signer_rs256 = lambda pem, msg: b"signature-test"
        self.addCleanup(setattr, pr, "_signer_rs256", original)
        self.srv = Serveur()
        self.horloge = Horloge()
        self.pauses = []
        self.fs = pr.Firestore(COMPTE, http=self.srv, maintenant=self.horloge,
                               dormir=self.pauses.append)
        self.reg = pr.RegistrePages(self.fs, maintenant=self.horloge)


class TestCompteService(unittest.TestCase):
    def test_json_et_base64(self):
        brut = json.dumps(COMPTE)
        self.assertEqual(pr.charger_compte_service(brut)["project_id"], "proj-test")
        enc = base64.b64encode(brut.encode()).decode()
        self.assertEqual(pr.charger_compte_service(enc)["client_email"], COMPTE["client_email"])

    def test_erreurs_sans_fuite(self):
        for brut in ("", "   ", "pas du json secret-xyz", '{"client_email": "a"}', "[1]"):
            with self.assertRaises(ValueError) as ctx:
                pr.charger_compte_service(brut)
            self.assertNotIn("secret-xyz", str(ctx.exception))


@unittest.skipUnless(CRYPTO, "cryptography absent")
class TestJwt(unittest.TestCase):
    def setUp(self):
        cle = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = cle.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
        self.compte = dict(COMPTE, private_key=pem)
        self.srv = Serveur(pub=cle.public_key())
        self.h = Horloge()
        self.fs = pr.Firestore(self.compte, http=self.srv, maintenant=self.h, dormir=lambda s: None)

    def test_signature_et_revendications(self):
        self.assertEqual(self.fs._jeton_acces(), "jeton-test")
        c = self.srv.charge
        self.assertEqual(c["iss"], COMPTE["client_email"])
        self.assertEqual(c["scope"], pr.SCOPE)
        self.assertEqual(c["aud"], pr.TOKEN_URL)
        self.assertEqual(c["exp"] - c["iat"], 3600)

    def test_jeton_reutilise_puis_renouvele(self):
        self.fs._jeton_acces()
        self.fs._jeton_acces()
        self.assertEqual(self.srv.jetons, 1)
        self.h.t += 3600
        self.fs._jeton_acces()
        self.assertEqual(self.srv.jetons, 2)


class TestDecodage(unittest.TestCase):
    def test_valeurs(self):
        champs = {"n": {"integerValue": "5"}, "b": {"booleanValue": True},
                  "m": {"mapValue": {"fields": {"a": {"stringValue": "x"}}}},
                  "l": {"arrayValue": {"values": [{"stringValue": "y"}]}}}
        self.assertEqual(pr.decoder_champs(champs), {"n": 5, "b": True, "m": {"a": "x"}, "l": ["y"]})

    def test_decoder_page(self):
        doc = {"name": "projects/p/databases/(default)/documents/users/U/pages/P",
               "fields": {"nom": {"stringValue": "N"}}}
        rec = pr.decoder_page(doc)
        self.assertEqual((rec["uid"], rec["page_id"], rec["nom"]), ("U", "P", "N"))
        self.assertIsNone(pr.decoder_page({"name": "projects/p/databases/(default)/documents/users/U/inbox/I"}))

    def test_etats(self):
        cas = [({"demande": "connecter", "status": "active"}, "attente"),
               ({"demande": "deconnecter"}, "deconnexion"),
               ({"status": "active"}, "active"), ({"status": "erreur"}, "erreur"),
               ({"status": "deconnectee"}, "deconnectee"), ({}, "attente")]
        for rec, attendu in cas:
            self.assertEqual(pr.etat_page(rec), attendu, rec)


class TestRegistre(Base):
    def test_complet_puis_incremental(self):
        self.srv.ajouter("u1", "p1", "2026-11-01T10:00:00.000000Z", nom="A", demande="connecter")
        self.srv.ajouter("u2", "p2", "2026-11-01T11:00:00.500000Z", nom="B", status="active")
        bilan = self.reg.rafraichir()
        self.assertEqual(len(bilan["nouvelles"]), 2)
        self.assertNotIn("where", self.srv.requetes[0])
        self.assertEqual(self.reg.curseur, "2026-11-01T11:00:00.500000Z")
        self.horloge.t += 60
        bilan = self.reg.rafraichir()
        self.assertEqual(bilan, {"nouvelles": [], "modifiees": [], "supprimees": []})
        w = self.srv.requetes[1]["where"]["fieldFilter"]
        self.assertEqual(w["value"]["timestampValue"], "2026-11-01T11:00:00.500000Z")
        self.srv.ajouter("u2", "p2", "2026-11-01T12:00:00.000000Z", nom="B", status="erreur")
        self.srv.ajouter("u3", "p3", "2026-11-01T12:30:00.000000Z", nom="C")
        self.horloge.t += 60
        bilan = self.reg.rafraichir()
        self.assertEqual(bilan["modifiees"], [("u2", "p2")])
        self.assertEqual(bilan["nouvelles"], [("u3", "p3")])
        self.assertEqual(self.reg.curseur, "2026-11-01T12:30:00.000000Z")

    def test_resync_detecte_suppression(self):
        self.srv.ajouter("u1", "p1", "2026-11-01T10:00:00.000000Z", nom="A")
        self.srv.ajouter("u2", "p2", "2026-11-01T11:00:00.000000Z", nom="B")
        self.reg.rafraichir()
        del self.srv.docs[("u1", "p1")]
        self.horloge.t += 60
        self.assertEqual(self.reg.rafraichir()["supprimees"], [])
        self.horloge.t += pr.RESYNC_SECONDS
        bilan = self.reg.rafraichir()
        self.assertEqual(bilan["supprimees"], [("u1", "p1")])
        self.assertNotIn("where", self.srv.requetes[-1])
        self.assertEqual(list(self.reg.pages), [("u2", "p2")])

    def test_pagination(self):
        for i in range(1, 6):
            self.srv.ajouter("u", f"p{i}", f"2026-11-01T10:0{i}:00.000000Z", nom=str(i))
        with mock.patch.object(pr, "TAILLE_PAGE", 2):
            self.reg.rafraichir()
        self.assertEqual(len(self.reg.pages), 5)

    def test_filtres(self):
        d = "2026-11-01T10:00:00.000000Z"
        self.srv.ajouter("u1", "p1", d, demande="connecter")
        self.srv.ajouter("u2", "p2", d, status="active")
        self.srv.ajouter("u3", "p3", d, demande="deconnecter")
        self.srv.ajouter("u4", "p4", d, status="deconnectee")
        self.srv.ajouter("u5", "p5", d, status="erreur")
        self.srv.ajouter("u6", "p6", d)
        self.reg.rafraichir()
        self.assertEqual([r["page_id"] for r in self.reg.a_traiter()], ["p1", "p3"])
        self.assertEqual([r["page_id"] for r in self.reg.actives()], ["p2"])
        self.assertEqual([r["page_id"] for r in self.reg.a_servir()], ["p1", "p2", "p6"])

    def test_appliquer_local(self):
        self.srv.ajouter("u1", "p1", "2026-11-01T10:00:00.000000Z", demande="connecter")
        self.reg.rafraichir()
        self.reg.appliquer_local("u1", "p1", {"status": "active", "demande": None})
        self.assertEqual(self.reg.actives()[0]["page_id"], "p1")
        self.assertNotIn("demande", self.reg.pages[("u1", "p1")])

    def test_resume_sans_identifiant(self):
        self.srv.ajouter("uid-secret-1", "page-secret-1", "2026-11-01T10:00:00.000000Z",
                         nom="Nom secret", status="active")
        self.reg.rafraichir()
        texte = json.dumps(self.reg.resume())
        for interdit in ("uid-secret-1", "page-secret-1", "Nom secret"):
            self.assertNotIn(interdit, texte)
        self.assertEqual(self.reg.resume()["active"], 1)
        self.assertGreaterEqual(self.reg.resume()["lectures"], 1)

    def test_lecture_vide_compte_une_lecture(self):
        self.reg.rafraichir()
        self.assertEqual(self.fs.lectures, 1)


class TestErreurs(Base):
    def test_403_sans_lien_ni_reprise(self):
        self.srv.forcees = [(403, {"error": {"status": "PERMISSION_DENIED",
                                             "message": "Refus https://firestore.googleapis.com/x?key=SECRET"}})]
        with self.assertRaises(pr.FirestoreError) as ctx:
            self.reg.rafraichir()
        msg = str(ctx.exception)
        self.assertNotIn("SECRET", msg)
        self.assertNotIn("https", msg)
        self.assertEqual(ctx.exception.status, 403)
        self.assertEqual(self.srv.appels, 2)

    def test_reprise_sur_429_et_503(self):
        self.srv.forcees = [(429, {}), (503, {})]
        self.srv.ajouter("u1", "p1", "2026-11-01T10:00:00.000000Z", nom="A")
        self.reg.rafraichir()
        self.assertEqual(len(self.reg.pages), 1)
        self.assertEqual(self.pauses, [1, 2])

    def test_jeton_google_refuse(self):
        def refuse(methode, url, en_tetes, corps):
            return 400, {"error": "invalid_grant"}
        fs = pr.Firestore(COMPTE, http=refuse, dormir=lambda s: None)
        with self.assertRaises(pr.FirestoreError) as ctx:
            pr.RegistrePages(fs).rafraichir()
        self.assertIn("Jeton Google refusé", str(ctx.exception))
        self.assertNotIn("PEM-DE-TEST", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
