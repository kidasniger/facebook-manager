"""Aime automatiquement les commentaires Facebook (jamais les publications).

Réglé par glm/config.json, bloc « aimes ».
Les commentaires racines et leurs réponses sont fournis par le lecteur/cache
principal. La mémoire séparée évite de confondre les anciens likes de publications.
"""
import datetime as dt
import urllib.parse

MEMOIRE = ("glm", "aimes-commentaires.json")
LIMITE_PAR_CYCLE = 3
ESSAIS_MAX = 3


def _maintenant():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _erreur(res):
    err = (res or {}).get("error") if isinstance(res, dict) else None
    if isinstance(err, dict):
        return f"code {err.get('code')} : {str(err.get('message') or '')[:160]}"
    return str(res)[:160]


def _verifier_like(gm, comment_id):
    """Lit le champ Comment.user_likes, sans confondre un POST accepté et un like visible."""
    params = urllib.parse.urlencode({
        "fields": "id,user_likes",
        "access_token": gm.TOKEN,
    })
    st, res = gm.http(f"{gm.GRAPH}/{comment_id}?{params}", method="GET")
    if st != 200 or not isinstance(res, dict) or res.get("error"):
        return None, _erreur(res)
    if "user_likes" not in res:
        return None, "Facebook n'a pas renvoyé le champ user_likes"
    return res["user_likes"] is True, ""


def _compacte(faits, garde=1000):
    if len(faits) <= garde:
        return faits
    tries = sorted(
        faits.items(),
        key=lambda kv: str((kv[1] or {}).get("le") or ""),
        reverse=True,
    )
    return dict(tries[:garde])


def traiter(gm, sauver, posts=None, dry=False):
    """Aime uniquement les commentaires et réponses des visiteurs."""
    cfg = (gm.jload(gm.P("glm", "config.json"), {}) or {}).get("aimes")
    if not isinstance(cfg, dict) or not cfg.get("actif"):
        return 0
    if posts is None:
        gm.log("Aimes commentaires : aucun fil fourni, aucun like envoyé.")
        return 0

    commentaires = []
    vus = set()
    for post in posts:
        for comment in (post.get("comments") or {}).get("data") or []:
            if not isinstance(comment, dict):
                continue
            cid = str(comment.get("id") or "")
            auteur = str((comment.get("from") or {}).get("id") or "")
            if not cid or not auteur or auteur == str(gm.PAGE_ID) or cid in vus:
                continue
            vus.add(cid)
            commentaires.append(comment)

    memoire = gm.jload(gm.P(*MEMOIRE), {})
    if not isinstance(memoire, dict):
        memoire = {}
    faits = memoire.setdefault("faits", {})
    compteur = 0

    for comment in commentaires:
        if compteur >= LIMITE_PAR_CYCLE:
            break
        cid = str(comment["id"])
        deja = faits.get(cid)
        if isinstance(deja, dict) and deja.get("like") == "ok" and deja.get("verifie"):
            continue
        essais_precedents = int((deja or {}).get("essais") or 0)
        if essais_precedents >= ESSAIS_MAX:
            continue

        # Réconcilier d'abord un succès/pending ancien avant tout nouvel envoi.
        verifie, detail = _verifier_like(gm, cid)
        if verifie is True:
            faits[cid] = {"le": _maintenant(), "like": "ok", "essais": essais_precedents,
                          "verifie": True}
            gm.jsave(gm.P(*MEMOIRE), {"faits": _compacte(faits)})
            sauver("Aimes commentaires : état confirmé", ["glm/aimes-commentaires.json"])
            continue
        if verifie is None:
            gm.log(f"Aimes commentaire {cid} : vérification impossible ({detail}); aucun succès affirmé.")
            continue
        if dry:
            gm.log(f"[TEST] aimes commentaires : le commentaire {cid} serait aimé.")
            continue

        faits[cid] = {"le": _maintenant(), "like": "pending", "essais": essais_precedents}
        gm.jsave(gm.P(*MEMOIRE), {"faits": _compacte(faits)})
        if not sauver("Aimes commentaires : prise en charge", ["glm/aimes-commentaires.json"]):
            gm.log(f"Aimes commentaire {cid} : enregistrement préalable impossible, like annulé.")
            return compteur

        st, res = gm.http(
            f"{gm.GRAPH}/{cid}/likes",
            {"access_token": gm.TOKEN},
        )
        post_ok = st == 200 and isinstance(res, dict) and res.get("success") is True
        verifie_apres, detail_apres = (False, "")
        if post_ok:
            verifie_apres, detail_apres = _verifier_like(gm, cid)
        ok = post_ok and verifie_apres is True
        essais = essais_precedents + 1
        entree = {
            "le": _maintenant(),
            "like": "ok" if ok else "erreur",
            "essais": essais,
        }
        if ok:
            entree["verifie"] = True
        else:
            entree["derniere_erreur"] = (
                f"POST: {_erreur(res)}; vérification: {detail_apres or 'user_likes=false'}"
                if post_ok else _erreur(res)
            )
        faits[cid] = entree
        gm.jsave(gm.P(*MEMOIRE), {"faits": _compacte(faits)})
        sauver("Aimes commentaires : résultat", ["glm/aimes-commentaires.json"])
        compteur += 1
        gm.log(
            f"Aimes commentaire {cid} : {entree['like']}"
            + (", confirmé par Facebook" if ok else
               f", non confirmé ({entree.get('derniere_erreur', 'vérification échouée')})")
        )
    return compteur
