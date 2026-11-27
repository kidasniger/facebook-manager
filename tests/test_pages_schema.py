import datetime as dt
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import pages_registry as pr
import pages_schema as sc

UID, PAGE = "U1", "1234567890"
UTC = dt.timezone.utc
MAINTENANT = dt.datetime(2026, 11, 1, 10, 0, tzinfo=UTC)


class TestChemins(unittest.TestCase):
    def test_chemins(self):
        self.assertEqual(sc.chemin_profil(UID, PAGE), f"users/{UID}/profil/{PAGE}")
        self.assertEqual(sc.chemin_inbox(UID, PAGE, "c-1_2"), f"users/{UID}/inbox/{PAGE}__c-1_2")
        self.assertEqual(sc.chemin_stats(UID, PAGE, "2026-11-01"), f"users/{UID}/stats/{PAGE}__2026-11-01")
        self.assertEqual(sc.chemin_quota(UID, PAGE, "2026-11-01"), f"users/{UID}/quota/{PAGE}__2026-11-01")
        self.assertEqual(sc.chemin_file(UID, "abc-1"), f"users/{UID}/file/abc-1")

    def test_identifiants_invalides(self):
        for uid, page in (("a/b", PAGE), ("", PAGE), (UID, "abc"), (UID, "12"), (UID, "1/2"), ("..", PAGE)):
            for fonction in (sc.chemin_profil,):
                with self.assertRaises(ValueError, msg=(uid, page)):
                    fonction(uid, page)
            with self.assertRaises(ValueError):
                sc.chemin_inbox(uid, page, "c-1")
            with self.assertRaises(ValueError):
                sc.chemin_stats(uid, page, "2026-11-01")
            with self.assertRaises(ValueError):
                sc.chemin_quota(uid, page, "2026-11-01")
        with self.assertRaises(ValueError):
            sc.chemin_file("a/b", "x")

    def test_elements_et_jours_invalides(self):
        for element in ("", "a/b", "a b", "x" * 101, "é"):
            with self.assertRaises(ValueError, msg=element):
                sc.chemin_inbox(UID, PAGE, element)
            with self.assertRaises(ValueError):
                sc.chemin_file(UID, element)
        for jour in ("", "2026-1-1", "2026/11/01", "demain", "2026-11-01/x"):
            with self.assertRaises(ValueError, msg=jour):
                sc.chemin_stats(UID, PAGE, jour)
            with self.assertRaises(ValueError):
                sc.chemin_quota(UID, PAGE, jour)

    def test_id_avis_et_conversation(self):
        self.assertEqual(sc.id_avis({"id": "111_222"}), "c-111_222")
        self.assertEqual(sc.id_conversation({"id": "t_999"}), "m-t_999")
        for valeur in ({}, None, {"id": ""}, {"id": "a/b"}):
            with self.assertRaises(ValueError):
                sc.id_avis(valeur)
            with self.assertRaises(ValueError):
                sc.id_conversation(valeur)

    def test_id_de_document_contient_la_page(self):
        chemin = sc.chemin_inbox(UID, PAGE, sc.id_avis({"id": "1_2"}))
        self.assertEqual(chemin.rsplit("/", 1)[1].split("__", 1)[0], PAGE)


class TestOutils(unittest.TestCase):
    def test_jour_utc(self):
        local = dt.datetime(2026, 11, 1, 23, 30, tzinfo=dt.timezone(dt.timedelta(hours=-3)))
        self.assertEqual(sc.jour_utc(local), "2026-11-02")
        self.assertEqual(sc.jour_utc(MAINTENANT), "2026-11-01")

    def test_tronquer(self):
        self.assertEqual(sc.tronquer("  a \n\n b  ", 10), "a b")
        self.assertEqual(sc.tronquer("x" * 50, 10), "x" * 10)
        self.assertEqual((sc.tronquer(None, 5), sc.tronquer(12, 5)), ("", "12"))

    def test_prenom(self):
        self.assertEqual(sc.prenom("Awa Kone"), "Awa")
        self.assertEqual(sc.prenom("  Awa   Kone "), "Awa")
        self.assertEqual((sc.prenom(""), sc.prenom(None), sc.prenom("   ")), ("", "", ""))
        self.assertEqual(len(sc.prenom("x" * 200)), 40)

    def test_horodatage(self):
        attendu = dt.datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
        for texte in ("2026-11-01T10:00:00+0000", "2026-11-01T10:00:00Z", "2026-11-01T10:00:00+00:00",
                      "2026-11-01T10:00:00"):
            self.assertEqual(sc.horodatage(texte), attendu, texte)
        self.assertEqual(sc.horodatage("2026-11-01T12:00:00+0200"), attendu)
        for texte in ("", None, "hier", "2026-13-45T00:00:00Z", 5):
            self.assertIsNone(sc.horodatage(texte), texte)

    def test_empreinte(self):
        a = {"x": 1, "y": "z", "ts": MAINTENANT}
        b = {"ts": MAINTENANT, "y": "z", "x": 1}
        self.assertEqual(sc.empreinte(a), sc.empreinte(b))
        self.assertNotEqual(sc.empreinte(a), sc.empreinte(dict(a, x=2)))
        self.assertNotEqual(sc.empreinte(a), sc.empreinte(dict(a, ts=MAINTENANT.replace(minute=1))))
        self.assertEqual(len(sc.empreinte(a)), 40)


class TestAvis(unittest.TestCase):
    def commentaire(self, **extra):
        c = {"id": "1_2", "message": "Super  produit\nmerci", "created_time": "2026-11-01T10:00:00+0000",
             "from": {"id": "visiteur-777", "name": "Awa Kone"}, "page_replied": False,
             "parent_id": None, "root_id": "1_2", "post_id": "1", "like_count": 5}
        c.update(extra)
        return c

    def test_champs(self):
        champs = sc.champs_avis(PAGE, self.commentaire(), "Un post\nsur deux lignes")
        self.assertEqual(champs, {"pageId": PAGE, "type": "avis", "texte": "Super produit merci",
                                  "nom": "Awa", "post": "Un post sur deux lignes", "repondu": False,
                                  "ts": MAINTENANT})

    def test_pas_de_donnee_inutile(self):
        champs = sc.champs_avis(PAGE, self.commentaire(), "post")
        texte = json.dumps(champs, default=str)
        for interdit in ("visiteur-777", "Kone", "like_count", "root_id", "parent_id"):
            self.assertNotIn(interdit, texte)
        self.assertNotIn("traite", champs)

    def test_troncatures(self):
        champs = sc.champs_avis(PAGE, self.commentaire(message="x" * 900), "y" * 400)
        self.assertEqual((len(champs["texte"]), len(champs["post"])), (sc.LIMITE_AVIS, sc.LIMITE_POST))

    def test_repondu(self):
        self.assertTrue(sc.champs_avis(PAGE, self.commentaire(page_replied=True))["repondu"])

    def test_donnees_manquantes(self):
        champs = sc.champs_avis(PAGE, {"id": "1_2", "created_time": "hier"})
        self.assertEqual((champs["texte"], champs["nom"], champs["post"], champs["repondu"]), ("", "", "", False))
        self.assertNotIn("ts", champs)
        self.assertEqual(sc.champs_avis(PAGE, None)["type"], "avis")


class TestConversation(unittest.TestCase):
    def conv(self, **extra):
        c = {"id": "t_1", "updated_time": "2026-11-01T10:00:00+0000", "with": {"id": "psid-555", "name": "Moussa Ali"},
             "messages": [{"message": "Bonjour", "from_page": False}, {"message": "Votre prix ?", "from_page": False}]}
        c.update(extra)
        return c

    def test_dernier_message_seulement(self):
        champs = sc.champs_conversation(PAGE, self.conv())
        self.assertEqual(champs, {"pageId": PAGE, "type": "msg", "texte": "Votre prix ?", "nom": "Moussa",
                                  "repondu": False, "ts": MAINTENANT})
        self.assertNotIn("Bonjour", json.dumps(champs, default=str))
        self.assertNotIn("psid-555", json.dumps(champs, default=str))

    def test_repondu_quand_le_dernier_vient_de_la_page(self):
        conv = self.conv(messages=[{"message": "Salut", "from_page": False}, {"message": "Merci", "from_page": True}])
        self.assertTrue(sc.champs_conversation(PAGE, conv)["repondu"])

    def test_vide_ou_invalide(self):
        for conv in ({}, None, {"messages": "x"}, {"messages": [None]}):
            champs = sc.champs_conversation(PAGE, conv)
            self.assertEqual((champs["texte"], champs["nom"], champs["repondu"]), ("", "", False))
        self.assertEqual(len(sc.champs_conversation(PAGE, self.conv(
            messages=[{"message": "x" * 900}]))["texte"]), sc.LIMITE_MESSAGE)


class TestStatsQuotaFile(unittest.TestCase):
    def test_stats(self):
        champs = sc.champs_stats(PAGE, "2026-11-01", {"reponses": 4, "a_traiter": 2.5, "Mauvais": 1,
                                                      "x y": 2, "ok": True, "texte": "abc", "1a": 3})
        self.assertEqual(champs, {"pageId": PAGE, "jour": "2026-11-01", "valeurs": {"reponses": 4, "a_traiter": 2.5}})
        self.assertEqual(sc.champs_stats(PAGE, "2026-11-01", None)["valeurs"], {})
        with self.assertRaises(ValueError):
            sc.champs_stats(PAGE, "demain", {})

    def test_quota(self):
        self.assertEqual(sc.champs_quota(PAGE, "2026-11-01", 3, 50),
                         {"pageId": PAGE, "jour": "2026-11-01", "utilise": 3, "limite": 50})
        champs = sc.champs_quota(PAGE, "2026-11-01", -3, "5")
        self.assertEqual((champs["utilise"], champs["limite"]), (0, 5))

    def test_file_resultat_ok(self):
        champs, supprimer = sc.champs_file_resultat("published", MAINTENANT, post_id="1_9")
        self.assertEqual(champs, {"status": "published", "traiteLe": MAINTENANT, "postId": "1_9"})
        self.assertEqual(supprimer, ["erreur"])

    def test_file_resultat_erreur(self):
        champs, supprimer = sc.champs_file_resultat("error", MAINTENANT, erreur="x" * 400)
        self.assertEqual((champs["status"], len(champs["erreur"])), ("error", sc.LIMITE_ERREUR))
        self.assertEqual(supprimer, ["postId"])
        with self.assertRaises(ValueError):
            sc.champs_file_resultat("pending", MAINTENANT)


class TestCompatibiliteFirestore(unittest.TestCase):
    def test_noms_de_champs_et_dates_acceptes(self):
        c = {"id": "1_2", "message": "Super", "created_time": "2026-11-01T10:00:00+0000",
             "from": {"name": "Awa K"}, "page_replied": True}
        lots = [sc.champs_avis(PAGE, c, "post"), sc.champs_conversation(PAGE, {"id": "t", "messages": []}),
                sc.champs_stats(PAGE, "2026-11-01", {"reponses": 1}),
                sc.champs_quota(PAGE, "2026-11-01", 1, 50)]
        champs_file, supprimer = sc.champs_file_resultat("error", MAINTENANT, erreur="x")
        for champs in lots:
            ecriture = pr.ecriture_maj("proj-test", "users/U1/inbox/x", champs)
            self.assertEqual(set(ecriture["updateMask"]["fieldPaths"]), set(champs))
            self.assertNotIn("traite", ecriture["updateMask"]["fieldPaths"])
        ecriture = pr.ecriture_maj("proj-test", sc.chemin_file(UID, "abc"), champs_file, supprimer=supprimer)
        self.assertEqual(ecriture["update"]["fields"]["traiteLe"], {"timestampValue": "2026-11-01T10:00:00.000000Z"})
        self.assertEqual(ecriture["updateMask"]["fieldPaths"], ["status", "traiteLe", "erreur", "postId"])


if __name__ == "__main__":
    unittest.main()
