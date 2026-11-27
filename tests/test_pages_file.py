import datetime as dt
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import multi_runner as mr
import pages_budget as pb
import pages_file as pf
import pages_registry as pr
import pages_sync as ps

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
UID, PAGE, PAGE1 = "U1", "1234567", "1111111"
JETON = "TOKEN-SECRET-page"
DOC = "projects/proj-test/databases/(default)/documents/"


def ok(**extra):
    d = {"pageId": PAGE, "type": "post", "message": "Bonjour", "status": "pending"}
    d.update(extra)
    return d


class TestValider(unittest.TestCase):
    def test_ignores(self):
        for doc in (None, "x", [1], ok(status="published"), ok(status="error"), ok(status="done")):
            self.assertEqual(pf.valider(doc, NOW)["etat"], "ignore", doc)

    def test_post_ok(self):
        v = pf.valider(ok(message="  Salut  "), NOW)
        self.assertEqual((v["etat"], v["type"], v["page_id"], v["message"], v["photo_url"], v["programme"]),
                         ("ok", "post", PAGE, "Salut", "", None))

    def test_page_et_type_invalides(self):
        for doc in (ok(pageId="abc"), ok(pageId=""), ok(pageId="12"), ok(pageId=None)):
            self.assertEqual(pf.valider(doc, NOW), {"etat": "invalide", "erreur": pf.MESSAGES["page"]})
        for genre in ("video", "", None):
            self.assertEqual(pf.valider(ok(type=genre), NOW)["erreur"], pf.MESSAGES["type"])

    def test_messages(self):
        self.assertEqual(pf.valider(ok(message="   "), NOW)["erreur"], pf.MESSAGES["vide"])
        self.assertEqual(pf.valider(ok(message=None), NOW)["erreur"], pf.MESSAGES["vide"])
        self.assertEqual(pf.valider(ok(message="x" * 5001), NOW)["erreur"], pf.MESSAGES["long"])
        self.assertEqual(pf.valider(ok(message="x" * 5000), NOW)["etat"], "ok")

    def test_photo(self):
        v = pf.valider(ok(type="photo", message="", photoUrl="https://x.test/a.jpg"), NOW)
        self.assertEqual((v["etat"], v["photo_url"], v["message"]), ("ok", "https://x.test/a.jpg", ""))
        for url in ("", None, "http://x.test/a.jpg", "https://", "https://a b"):
            self.assertEqual(pf.valider(ok(type="photo", photoUrl=url), NOW)["erreur"], pf.MESSAGES["photo"], url)

    def test_programmation(self):
        v = pf.valider(ok(scheduledAt="2026-11-02T10:00:00Z"), NOW)
        self.assertEqual((v["etat"], v["programme"]), ("ok", 1793613600))
        self.assertEqual(pf.valider(ok(scheduledAt="2026-11-01T10:10:00Z"), NOW)["etat"], "ok")
        self.assertEqual(pf.valider(ok(scheduledAt="2026-12-01T10:00:00Z"), NOW)["etat"], "ok")
        for date in ("2026-11-01T10:09:59Z", "2026-12-01T10:00:01Z", "2026-10-01T10:00:00Z", "hier"):
            self.assertEqual(pf.valider(ok(scheduledAt=date), NOW)["erreur"], pf.MESSAGES["date"], date)


class TestPublier(unittest.TestCase):
    def demande(self, **extra):
        d = {"type": "post", "page_id": PAGE, "message": "Bonjour", "photo_url": "", "programme": None}
        d.update(extra)
        return d

    def appel(self, reponse, **extra):
        vus = []

        def envoyer(url, data):
            vus.append((url, data))
            if isinstance(reponse, Exception):
                raise reponse
            return reponse

        res = pf.publier(envoyer, "https://g.test", JETON, self.demande(**extra))
        return res, vus

    def test_dry_run_sans_appel(self):
        vus = []
        res = pf.publier(lambda u, d: vus.append(u), "https://g.test", JETON, self.demande(), dry_run=True)
        self.assertEqual((res["issue"], vus), ("dry_run", []))

    def test_post(self):
        res, vus = self.appel((200, {"id": "1_9"}))
        self.assertEqual(res, {"issue": "published", "post_id": "1_9", "erreur": ""})
        url, data = vus[0]
        self.assertEqual(url, f"https://g.test/{PAGE}/feed")
        self.assertEqual(data, {"access_token": JETON, "message": "Bonjour"})

    def test_photo(self):
        res, vus = self.appel((200, {"post_id": "1_8"}), type="photo", photo_url="https://x.test/a.jpg")
        self.assertEqual((res["issue"], res["post_id"]), ("published", "1_8"))
        url, data = vus[0]
        self.assertEqual(url, f"https://g.test/{PAGE}/photos")
        self.assertEqual((data["url"], data["caption"]), ("https://x.test/a.jpg", "Bonjour"))
        _, vus = self.appel((200, {"id": "1"}), type="photo", photo_url="https://x.test/a.jpg", message="")
        self.assertNotIn("caption", vus[0][1])

    def test_programmation(self):
        res, vus = self.appel((200, {"id": "1_9"}), programme=1793613600)
        self.assertEqual(res["issue"], "scheduled")
        self.assertEqual((vus[0][1]["published"], vus[0][1]["scheduled_publish_time"]), ("false", "1793613600"))

    def test_reponse_sans_identifiant(self):
        for reponse in ((200, {}), (200, None), (200, "x")):
            res, _ = self.appel(reponse)
            self.assertEqual((res["issue"], res["erreur"]), ("error", pf.MESSAGES["refuse"]), reponse)

    def test_retard(self):
        for reponse in (RuntimeError("reseau"), (429, {}), (500, {}), (503, None),
                        (400, {"error": {"code": 4}}), (400, {"error": {"code": "80001"}})):
            res, _ = self.appel(reponse)
            self.assertEqual((res["issue"], res["erreur"]), ("retard", ""), reponse)

    def test_jeton(self):
        for code in (190, "463"):
            res, _ = self.appel((400, {"error": {"code": code}}))
            self.assertEqual((res["issue"], res["erreur"]), ("error", pf.MESSAGES["jeton"]), code)

    def test_refus_sans_fuite(self):
        res, _ = self.appel((400, {"error": {"code": 100, "message": "Awa secret " + JETON}}))
        self.assertEqual((res["issue"], res["erreur"]), ("error", pf.MESSAGES["refuse"]))
        self.assertNotIn("secret", repr(res))
        res, _ = self.appel((400, "texte"))
        self.assertEqual(res["erreur"], pf.MESSAGES["refuse"])


class TestLecture(unittest.TestCase):
    def test_requete(self):
        q = pf.requete_en_attente(7)
        self.assertEqual(q["limit"], 7)
        self.assertEqual(q["from"], [{"collectionId": "file", "allDescendants": True}])
        self.assertEqual(q["where"]["fieldFilter"]["value"], {"stringValue": "pending"})
        self.assertEqual(pf.requete_en_attente()["limit"], pf.LIMITE_LECTURE)

    def test_decoder_element(self):
        doc = {"name": f"{DOC}users/{UID}/file/abc-1", "fields": {"type": {"stringValue": "post"}}}
        self.assertEqual(pf.decoder_element(doc), {"type": "post", "uid": UID, "element_id": "abc-1"})
        for mauvais in (None, {}, {"name": f"{DOC}users/{UID}/pages/{PAGE}"}, {"name": f"{DOC}users/{UID}/file"}):
            self.assertIsNone(pf.decoder_element(mauvais))

    def test_lister_ignore_les_autres_documents(self):
        class Fs:
            def requete(self, q):
                return [{"name": f"{DOC}users/{UID}/file/a", "fields": {}},
                        {"name": f"{DOC}users/{UID}/pages/{PAGE}", "fields": {}}]
        self.assertEqual([e["element_id"] for e in pf.lister_en_attente(Fs())], ["a"])


class FauxFs:
    projet = "proj-test"

    def __init__(self):
        self.docs, self.commits = [], []
        self.echec_commit = self.echec_requete = None

    def requete(self, requete):
        if self.echec_requete:
            raise self.echec_requete
        return list(self.docs)

    def commit(self, ecritures):
        if self.echec_commit:
            raise self.echec_commit
        self.commits.append(ecritures)
        return len(ecritures)

    def ecrits(self):
        return {e["update"]["name"].split("/documents/", 1)[1]: e["update"]["fields"]
                for lot in self.commits for e in lot}


class FauxRegistre:
    def __init__(self, actives):
        self._actives = actives

    def actives(self):
        return self._actives


class TestTraiterFile(unittest.TestCase):
    def setUp(self):
        self.fs = FauxFs()
        self.budget = pb.BudgetEcritures()
        self.sync = ps.Synchro(self.fs, self.budget)
        self.runner = mr.Runner(self.fs, None, "base", page1_id=PAGE1, maintenant=lambda: NOW.timestamp(),
                                sync=self.sync)
        self.runner.registre = FauxRegistre([{"uid": UID, "page_id": PAGE}])
        self.runner.jetons[(UID, PAGE)] = JETON
        self.appels, self.reponse = [], (200, {"id": "1_9"})

        def http(url, data=None, **kw):
            self.appels.append((url, data))
            return self.reponse

        self.gm = SimpleNamespace(http=http, GRAPH="https://g.test", DRY=False)

    def ajouter(self, element_id="e1", uid=UID, **extra):
        fields = {k: pr.encoder_valeur(v) for k, v in ok(**extra).items()}
        self.fs.docs.append({"name": f"{DOC}users/{uid}/file/{element_id}", "fields": fields})

    def lancer(self):
        return self.runner.traiter_file(self.gm)

    def test_publication(self):
        self.ajouter()
        bilan = self.lancer()
        self.assertEqual(bilan, {"lus": 1, "publies": 1, "programmes": 0, "erreurs": 0, "retard": 0})
        url, data = self.appels[0]
        self.assertEqual((url, data["access_token"]), (f"https://g.test/{PAGE}/feed", JETON))
        champs = self.fs.ecrits()[f"users/{UID}/file/e1"]
        self.assertEqual((champs["status"], champs["postId"]),
                         ({"stringValue": "published"}, {"stringValue": "1_9"}))
        self.assertEqual((self.sync.file, self.runner.faits, self.budget.utilise), ({}, {}, 1))

    def test_programmation(self):
        self.ajouter(scheduledAt="2026-11-02T10:00:00Z")
        bilan = self.lancer()
        self.assertEqual((bilan["programmes"], bilan["publies"]), (1, 0))
        self.assertEqual(self.appels[0][1]["scheduled_publish_time"], "1793613600")
        self.assertEqual(self.fs.ecrits()[f"users/{UID}/file/e1"]["status"], {"stringValue": "scheduled"})

    def test_element_invalide(self):
        self.ajouter(message="")
        bilan = self.lancer()
        self.assertEqual((bilan["erreurs"], self.appels), (1, []))
        champs = self.fs.ecrits()[f"users/{UID}/file/e1"]
        self.assertEqual((champs["status"], champs["erreur"]),
                         ({"stringValue": "error"}, {"stringValue": pf.MESSAGES["vide"]}))

    def test_page_non_connectee(self):
        self.ajouter(pageId="7654321")
        bilan = self.lancer()
        self.assertEqual((bilan["erreurs"], self.appels), (1, []))
        self.assertEqual(self.fs.ecrits()[f"users/{UID}/file/e1"]["erreur"], {"stringValue": mr.MSG_PAGE})

    def test_page_n1_jamais_servie(self):
        self.runner.registre = FauxRegistre([{"uid": UID, "page_id": PAGE1}])
        self.ajouter(pageId=PAGE1)
        self.assertEqual((self.lancer()["erreurs"], self.appels), (1, []))

    def test_page_d_un_autre_utilisateur(self):
        self.ajouter(uid="U2")          # U2 vise la Page de U1
        self.assertEqual((self.lancer()["erreurs"], self.appels), (1, []))
        self.assertIn("users/U2/file/e1", self.fs.ecrits())

    def test_sans_jeton_en_memoire(self):
        self.runner.jetons.clear()
        self.ajouter()
        bilan = self.lancer()
        self.assertEqual((bilan["retard"], self.appels, self.fs.commits), (1, [], []))

    def test_limite_par_page(self):
        for n in range(1, 6):
            self.ajouter(f"e{n}")
        bilan = self.lancer()
        self.assertEqual((bilan["publies"], bilan["retard"], len(self.appels)), (3, 2, mr.LIMITE_FILE_PAGE))
        self.assertEqual(sorted(self.fs.ecrits()), [f"users/{UID}/file/e{n}" for n in (1, 2, 3)])

    def test_erreur_facebook_jeton(self):
        self.reponse = (400, {"error": {"code": 190, "message": "secret"}})
        self.ajouter()
        bilan = self.lancer()
        self.assertEqual(bilan["erreurs"], 1)
        champs = self.fs.ecrits()[f"users/{UID}/file/e1"]
        self.assertEqual(champs["erreur"], {"stringValue": pf.MESSAGES["jeton"]})
        self.assertNotIn("secret", repr(champs))

    def test_limite_facebook_retentee_plus_tard(self):
        self.reponse = (429, {})
        self.ajouter()
        bilan = self.lancer()
        self.assertEqual((bilan["retard"], bilan["publies"], self.fs.commits, self.runner.faits), (1, 0, [], {}))

    def test_dry_run(self):
        self.gm.DRY = True
        self.ajouter()
        bilan = self.lancer()
        self.assertEqual((bilan["retard"], self.appels, self.fs.commits), (1, [], []))

    def test_deja_traite_ignore(self):
        self.ajouter(status="published")
        bilan = self.lancer()
        self.assertEqual((bilan["lus"], self.appels, self.fs.commits), (1, [], []))

    def test_jamais_republie_si_l_ecriture_echoue(self):
        self.fs.echec_commit = pr.FirestoreError("Firestore HTTP 500", status=500)
        self.ajouter()
        self.assertEqual(self.lancer()["publies"], 1)
        self.assertEqual((len(self.appels), len(self.runner.faits), self.budget.utilise), (1, 1, 0))
        self.fs.echec_commit = None                     # l'élément est toujours « pending » côté Firestore
        bilan = self.lancer()
        self.assertEqual((bilan["lus"], bilan["publies"], len(self.appels)), (0, 0, 1))
        self.assertEqual(sorted(self.fs.ecrits()), [f"users/{UID}/file/e1"])
        self.assertEqual((self.runner.faits, self.sync.file, self.budget.utilise), ({}, {}, 1))

    def test_sans_synchro(self):
        self.runner.sync = None
        self.ajouter()
        self.assertEqual(self.lancer(), {"lus": 0, "publies": 0, "programmes": 0, "erreurs": 0, "retard": 0})
        self.assertEqual(self.appels, [])

    def test_erreur_de_lecture(self):
        self.fs.echec_requete = pr.FirestoreError("Firestore HTTP 403", status=403)
        self.assertEqual(self.lancer()["erreurs"], 1)

    def test_bilan_sans_donnee(self):
        self.ajouter(message="Texte secret")
        bilan = self.lancer()
        self.assertTrue(all(isinstance(v, int) for v in bilan.values()))
        self.assertNotIn("secret", repr(bilan))


if __name__ == "__main__":
    unittest.main()
