import datetime as dt
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import pages_registry as pr

COMPTE = {"client_email": "bot@proj-test.iam.gserviceaccount.com",
          "private_key": "PEM-DE-TEST", "project_id": "proj-test"}
DOC = "projects/proj-test/databases/(default)/documents/"


class FauxHttp:
    def __init__(self, reponses=None):
        self.reponses = list(reponses or [])
        self.appels = []

    def __call__(self, methode, url, en_tetes, corps):
        if url == pr.TOKEN_URL:
            return 200, {"access_token": "jeton-test", "expires_in": 3600}
        self.appels.append((methode, url, json.loads(corps) if corps else None))
        if self.reponses:
            return self.reponses.pop(0)
        return 200, {}


class Base(unittest.TestCase):
    def setUp(self):
        original = pr._signer_rs256
        pr._signer_rs256 = lambda pem, msg: b"signature-test"
        self.addCleanup(setattr, pr, "_signer_rs256", original)

    def client(self, reponses=None):
        self.http = FauxHttp(reponses)
        return pr.Firestore(COMPTE, http=self.http, dormir=lambda s: None)


class TestEncodage(unittest.TestCase):
    def test_types(self):
        self.assertEqual(pr.encoder_valeur("a"), {"stringValue": "a"})
        self.assertEqual(pr.encoder_valeur(5), {"integerValue": "5"})
        self.assertEqual(pr.encoder_valeur(1.5), {"doubleValue": 1.5})
        self.assertEqual(pr.encoder_valeur(True), {"booleanValue": True})
        self.assertEqual(pr.encoder_valeur(False), {"booleanValue": False})
        self.assertEqual(pr.encoder_valeur(None), {"nullValue": None})

    def test_date_toujours_en_utc(self):
        d = dt.datetime(2026, 11, 1, 10, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(pr.encoder_valeur(d), {"timestampValue": "2026-11-01T10:00:00.000000Z"})
        local = dt.datetime(2026, 11, 1, 11, 0, tzinfo=dt.timezone(dt.timedelta(hours=1)))
        self.assertEqual(pr.encoder_valeur(local), {"timestampValue": "2026-11-01T10:00:00.000000Z"})

    def test_aller_retour_avec_le_decodeur(self):
        champs = {"s": "x", "n": 3, "b": True, "m": {"a": "y", "l": ["z", 2]}}
        fields = pr.ecriture_maj("proj-test", "c/d", champs)["update"]["fields"]
        self.assertEqual(pr.decoder_champs(fields), champs)

    def test_type_non_pris_en_charge_sans_fuite(self):
        class Secret:
            def __repr__(self):
                return "JETON-SECRET"
        with self.assertRaises(TypeError) as ctx:
            pr.encoder_valeur(Secret())
        self.assertNotIn("JETON-SECRET", str(ctx.exception))


class TestEcritures(unittest.TestCase):
    def test_nom_document(self):
        self.assertEqual(pr.nom_document("proj-test", "/bot/x/"), DOC + "bot/x")

    def test_maj_masque_et_suppression(self):
        e = pr.ecriture_maj("proj-test", "users/u/pages/p", {"status": "active"}, supprimer=("demande", "message"))
        self.assertEqual(e["update"]["name"], DOC + "users/u/pages/p")
        self.assertEqual(e["update"]["fields"], {"status": {"stringValue": "active"}})
        self.assertEqual(e["updateMask"]["fieldPaths"], ["status", "demande", "message"])
        self.assertNotIn("currentDocument", e)

    def test_conditions_d_existence(self):
        self.assertEqual(pr.ecriture_maj("p", "c/d", {"a": 1}, existe=True)["currentDocument"], {"exists": True})
        self.assertEqual(pr.ecriture_maj("p", "c/d", {"a": 1}, existe=False)["currentDocument"], {"exists": False})
        self.assertEqual(pr.ecriture_suppression("p", "c/d", existe=True)["currentDocument"], {"exists": True})
        self.assertEqual(pr.ecriture_suppression("proj-test", "c/d"), {"delete": DOC + "c/d"})

    def test_noms_de_champ_invalides(self):
        for nom in ("a.b", "a b", "", "1a", "a`b", "a/b"):
            with self.assertRaises(ValueError):
                pr.ecriture_maj("p", "c/d", {nom: 1})
            with self.assertRaises(ValueError):
                pr.ecriture_maj("p", "c/d", {}, supprimer=(nom,))

    def test_champ_ecrit_et_supprime(self):
        with self.assertRaises(ValueError):
            pr.ecriture_maj("p", "c/d", {"a": 1}, supprimer=("a",))


class TestCommit(Base):
    def ecritures(self, n):
        return [pr.ecriture_maj("proj-test", f"c/d{i}", {"i": i}) for i in range(n)]

    def test_un_seul_lot(self):
        fs = self.client()
        self.assertEqual(fs.commit(self.ecritures(3)), 3)
        self.assertEqual(len(self.http.appels), 1)
        methode, url, corps = self.http.appels[0]
        self.assertEqual(methode, "POST")
        self.assertTrue(url.endswith("/documents:commit"))
        self.assertEqual(len(corps["writes"]), 3)
        self.assertEqual(fs.ecritures, 3)

    def test_decoupage_en_lots(self):
        fs = self.client()
        with mock.patch.object(pr, "LOT_MAX", 2):
            self.assertEqual(fs.commit(self.ecritures(5)), 5)
        self.assertEqual([len(a[2]["writes"]) for a in self.http.appels], [2, 2, 1])
        self.assertEqual(fs.ecritures, 5)

    def test_vide_sans_appel(self):
        fs = self.client()
        self.assertEqual(fs.commit([]), 0)
        self.assertEqual(self.http.appels, [])
        self.assertEqual(fs.ecritures, 0)

    def test_lot_en_echec_sans_fuite(self):
        fs = self.client([(200, {}), (403, {"error": {"status": "PERMISSION_DENIED",
                                                      "message": "Refus https://x.test/?key=SECRET"}})])
        with mock.patch.object(pr, "LOT_MAX", 2):
            with self.assertRaises(pr.FirestoreError) as ctx:
                fs.commit(self.ecritures(5))
        self.assertNotIn("SECRET", str(ctx.exception))
        self.assertNotIn("https", str(ctx.exception))
        self.assertEqual(fs.ecritures, 2)

    def test_condition_non_remplie(self):
        fs = self.client([(409, {"error": {"status": "ALREADY_EXISTS", "message": "existe"}})])
        with self.assertRaises(pr.FirestoreError) as ctx:
            fs.commit([pr.ecriture_maj("proj-test", "c/d", {"a": 1}, existe=False)])
        self.assertEqual(ctx.exception.status, 409)


class TestLireDocument(Base):
    def test_trouve(self):
        fs = self.client([(200, {"fields": {"tokenEnc": {"stringValue": "v1.a.b"}}})])
        self.assertEqual(fs.lire_document("bot_tokens/u__p"), {"tokenEnc": "v1.a.b"})
        methode, url, _ = self.http.appels[0]
        self.assertEqual(methode, "GET")
        self.assertEqual(url, f"{pr.API}/{DOC}bot_tokens/u__p")
        self.assertEqual(fs.lectures, 1)

    def test_absent(self):
        fs = self.client([(404, {"error": {"status": "NOT_FOUND", "message": "absent"}})])
        self.assertIsNone(fs.lire_document("bot_tokens/u__p"))
        self.assertEqual(len(self.http.appels), 1)

    def test_autre_erreur_remontee(self):
        fs = self.client([(403, {"error": {"status": "PERMISSION_DENIED", "message": "non"}})])
        with self.assertRaises(pr.FirestoreError) as ctx:
            fs.lire_document("bot_tokens/u__p")
        self.assertEqual(ctx.exception.status, 403)

    def test_document_sans_champs(self):
        fs = self.client([(200, {})])
        self.assertEqual(fs.lire_document("a/b"), {})


class TestResume(Base):
    def test_resume_contient_les_ecritures(self):
        fs = self.client()
        fs.commit([pr.ecriture_maj("proj-test", "c/d", {"a": 1})])
        self.assertEqual(pr.RegistrePages(fs).resume()["ecritures"], 1)


if __name__ == "__main__":
    unittest.main()
