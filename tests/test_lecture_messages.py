import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import file_attente  # noqa: E402
import lecture_messages  # noqa: E402

PAGE = "111"
GRAPH = "https://graph.facebook.com/v26.0"


class FauxFacebook:
    """Réponses Graph simulées + compteur d'appels."""

    def __init__(self):
        self.convs = []
        self.messages = {}
        self.appels = []
        self.refuser_champs = False

    def __call__(self, url):
        self.appels.append(url)
        chemin = urllib.parse.urlparse(url).path
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        if chemin.endswith(f"/{PAGE}/conversations"):
            if self.refuser_champs and "unread_count" in params["fields"][0]:
                return 400, {"error": {"code": 100, "message": "champ inconnu"}}
            champs = params["fields"][0].split(",")
            return 200, {"data": [{k: v for k, v in c.items() if k in champs} for c in self.convs]}
        if chemin.endswith("/messages"):
            cid = chemin.split("/")[-2]
            return 200, {"data": list(self.messages.get(cid, []))}
        return 404, {"error": {"code": 803, "message": "?"}}


def msg(mid, texte, quand, de, nom="X"):
    return {"id": mid, "message": texte, "created_time": quand, "from": {"id": de, "name": nom}}


def conv(cid, maj, contact="900", nom="Awa"):
    return {"id": cid, "updated_time": maj, "unread_count": 1, "message_count": 2,
            "participants": {"data": [{"id": PAGE, "name": "Page"}, {"id": contact, "name": nom}]}}


def lecteur(fb, **kw):
    return lecture_messages.LecteurMessages(PAGE, "TOKEN", GRAPH, fb, journal=lambda *_: None, **kw)


def test_format_et_ordre_chronologique():
    fb = FauxFacebook()
    fb.convs = [conv("t_1", "2026-11-09T10:00:00+0000")]
    fb.messages["t_1"] = [  # Facebook renvoie le plus récent d'abord
        msg("m2", "Bonjour", "2026-11-09T10:00:00+0000", "900", "Awa"),
        msg("m1", "Salut", "2026-11-09T09:00:00+0000", "900", "Awa"),
    ]
    res = lecteur(fb).lire()
    c = res[0]
    assert [m["message"] for m in c["messages"]] == ["Salut", "Bonjour"]
    assert c["with"] == {"id": "900", "name": "Awa"}
    assert c["repondu"] is False
    assert c["last_user_time"] == "2026-11-09T10:00:00+0000"
    assert all(m["from_page"] is False for m in c["messages"])


def test_repondu_quand_la_page_a_parle_en_dernier():
    fb = FauxFacebook()
    fb.convs = [conv("t_1", "2026-11-09T10:00:00+0000")]
    fb.messages["t_1"] = [
        msg("m2", "Merci", "2026-11-09T10:00:00+0000", PAGE, "Page"),
        msg("m1", "Prix ?", "2026-11-09T09:00:00+0000", "900", "Awa"),
    ]
    c = lecteur(fb).lire()[0]
    assert c["repondu"] is True
    assert c["messages"][-1]["from_page"] is True
    assert c["last_user_time"] == "2026-11-09T09:00:00+0000"


def test_cache_un_seul_appel_sans_activite():
    fb = FauxFacebook()
    fb.convs = [conv("t_1", "2026-11-09T10:00:00+0000"), conv("t_2", "2026-11-09T09:00:00+0000", "901", "Moussa")]
    for cid in ("t_1", "t_2"):
        fb.messages[cid] = [msg("m" + cid, "Salam", "2026-11-09T08:00:00+0000", "900")]
    lec = lecteur(fb)
    lec.lire()
    assert len(fb.appels) == 3          # 1 liste + 2 conversations
    fb.appels.clear()
    res = lec.lire()
    assert len(fb.appels) == 1          # liste seule
    assert lec.stats == {"relues": 0, "reutilisees": 2}
    assert len(res) == 2


def test_seule_la_conversation_modifiee_est_relue():
    fb = FauxFacebook()
    fb.convs = [conv("t_1", "2026-11-09T10:00:00+0000"), conv("t_2", "2026-11-09T09:00:00+0000", "901")]
    for cid in ("t_1", "t_2"):
        fb.messages[cid] = [msg("m" + cid, "Salam", "2026-11-09T08:00:00+0000", "900")]
    lec = lecteur(fb)
    lec.lire()
    fb.appels.clear()
    fb.convs[1]["updated_time"] = "2026-11-09T11:00:00+0000"
    fb.messages["t_2"].insert(0, msg("mnew", "Encore moi", "2026-11-09T11:00:00+0000", "901"))
    res = lec.lire()
    assert len(fb.appels) == 2
    assert lec.stats == {"relues": 1, "reutilisees": 1}
    assert res[0]["id"] == "t_2"        # la plus récente d'abord
    assert res[0]["messages"][-1]["message"] == "Encore moi"


def test_limite_de_lectures_par_passe():
    fb = FauxFacebook()
    fb.convs = [conv(f"t_{i}", f"2026-11-09T10:{i:02d}:00+0000", str(900 + i)) for i in range(5)]
    for c in fb.convs:
        fb.messages[c["id"]] = [msg("m" + c["id"], "Salam", "2026-11-09T08:00:00+0000", "900")]
    lec = lecteur(fb, max_lectures=2)
    assert len(lec.lire()) == 2
    assert len(lec.lire()) == 4
    assert len(lec.lire()) == 5


def test_champs_refuses_retour_au_minimum():
    fb = FauxFacebook()
    fb.refuser_champs = True
    fb.convs = [conv("t_1", "2026-11-09T10:00:00+0000")]
    fb.messages["t_1"] = [msg("m1", "Salam", "2026-11-09T08:00:00+0000", "900")]
    res = lecteur(fb).lire()
    assert len(res) == 1
    assert res[0]["unread_count"] == 0   # champ absent -> 0


def test_erreur_remontee_avec_son_code():
    def refuse(url):
        return 403, {"error": {"code": 10, "message": "permission"}}
    try:
        lecteur(refuse).lire()
        assert False, "une erreur était attendue"
    except lecture_messages.comment_store.FacebookError as exc:
        assert exc.code == 10


def test_message_sans_texte_et_troncature():
    fb = FauxFacebook()
    fb.convs = [conv("t_1", "2026-11-09T10:00:00+0000")]
    fb.messages["t_1"] = [
        {"id": "m2", "created_time": "2026-11-09T10:00:00+0000", "from": {"id": "900", "name": "Awa"}},
        msg("m1", "a" * 5000, "2026-11-09T09:00:00+0000", "900"),
    ]
    c = lecteur(fb).lire()[0]
    assert c["messages"][0]["message"] == "a" * lecture_messages.MAX_TEXTE
    assert c["messages"][1]["message"] == ""


def test_sans_horodatage():
    a = lecture_messages.construire_cache_messages([], PAGE, "t1")
    b = lecture_messages.construire_cache_messages([], PAGE, "t2")
    assert lecture_messages.sans_horodatage(a) == lecture_messages.sans_horodatage(b)


# ---------- file d'attente ----------

class FauxGm:
    GRAPH = GRAPH
    PAGE_ID = PAGE
    TOKEN = "TOKEN"

    def __init__(self, reponse):
        self.reponse = reponse
        self.appels = []

    def http(self, url, data=None, method="POST"):
        self.appels.append((url, data))
        return self.reponse


def test_envoi_message_ok():
    gm = FauxGm((200, {"recipient_id": "900", "message_id": "m_abc"}))
    res = file_attente._repondre_message(gm, {"recipient_id": "900", "message": "Bonjour é"})
    assert res == {"status": "replied", "message_id": "m_abc"}
    url, data = gm.appels[0]
    assert "TOKEN" not in url and data == {"access_token": "TOKEN"}
    assert urllib.parse.urlparse(url).path.endswith(f"/{PAGE}/messages")
    p = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert json.loads(p["recipient"][0]) == {"id": "900"}
    assert json.loads(p["message"][0]) == {"text": "Bonjour é"}
    assert p["messaging_type"] == ["RESPONSE"]


def test_envoi_message_fenetre_24h():
    gm = FauxGm((400, {"error": {"code": 10, "error_subcode": 2018278, "message": "x"}}))
    res = file_attente._repondre_message(gm, {"recipient_id": "900", "message": "Bonjour"})
    assert res["status"] == "error" and "24 h" in res["erreur"]


def test_envoi_message_champs_manquants_ou_trop_long():
    gm = FauxGm((200, {}))
    assert file_attente._repondre_message(gm, {"message": "x"})["status"] == "error"
    assert file_attente._repondre_message(gm, {"recipient_id": "9", "message": "  "})["status"] == "error"
    assert file_attente._repondre_message(gm, {"recipient_id": "9", "message": "é" * 2000})["erreur"] == "message trop long"
    assert gm.appels == []


def test_la_file_messages_est_declaree():
    assert "posts/messages.json" in file_attente.FICHIERS
    assert "posts/messages.json" in [c for c, _ in file_attente.TRAITEMENTS]
