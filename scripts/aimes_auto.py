"""Auto-like des publications et message privé facultatif à l'auteur.

Réglé par glm/config.json, bloc « aimes » :
  {"actif": true, "message": "texte facultatif envoyé par Messenger"}

À chaque cycle de glm-live (mémoire dans glm/aimes.json) :
- liste les dernières publications de la Page (feed, 25 dernières),
- like celles jamais traitées, au plus LIMITE_PAR_CYCLE par cycle,
  les plus anciennes d'abord,
- si « message » est configuré, envoie un MP Messenger à l'auteur — jamais à la
  Page elle-même ; les échecs (fenêtre de 24 h, permission) sont journalisés
  sans casser la boucle.

Un like en erreur est retenté au plus ESSAIS_MAX cycles avant abandon.
"""
import datetime as dt
import urllib.parse

import file_attente

MEMOIRE = ("glm", "aimes.json")
LIMITE_PAR_CYCLE = 3
ESSAIS_MAX = 3


def _maintenant():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _erreur(res):
    err = (res or {}).get("error") if isinstance(res, dict) else None
    if isinstance(err, dict):
        return f"code {err.get('code')} : {str(err.get('message') or '')[:160]}"
    return str(res)[:160]


def _verifier_like(gm, pid):
    """Vérifie côté Graph API que la Page a réellement aimé la publication."""
    params = urllib.parse.urlencode({"summary": "true", "access_token": gm.TOKEN})
    st, res = gm.http(f"{gm.GRAPH}/{pid}/likes?{params}", method="GET")
    if st != 200 or not isinstance(res, dict) or res.get("error"):
        return None, _erreur(res)
    summary = res.get("summary")
    if not isinstance(summary, dict) or "has_liked" not in summary:
        return None, "Facebook n'a pas renvoyé summary.has_liked"
    return bool(summary["has_liked"]), ""


def _compacte(faits, garde=300):
    """Mémoire bornée : on garde les entrées les plus récentes."""
    if len(faits) <= garde:
        return faits
    tries = sorted(faits.items(),
                   key=lambda kv: str((kv[1] or {}).get("le") or ""),
                   reverse=True)
    return dict(tries[:garde])


def traiter(gm, sauver, dry=False):
    """Like les nouvelles publications (+ MP facultatif). Renvoie le nombre traité."""
    cfg = (gm.jload(gm.P("glm", "config.json"), {}) or {}).get("aimes")
    if not isinstance(cfg, dict) or not cfg.get("actif"):
        return 0
    message = str(cfg.get("message") or "").strip()
    # Lecture du fil = GET. Passer les paramètres dans le corps ferait
    # interpréter l'appel comme une création de publication vide (erreur 197).
    params = urllib.parse.urlencode({
        "fields": "id,from,created_time", "limit": "25", "access_token": gm.TOKEN,
    })
    st, res = gm.http(f"{gm.GRAPH}/{gm.PAGE_ID}/feed?{params}", method="GET")
    if st != 200 or not isinstance(res, dict) or res.get("error"):
        gm.log(f"Aimes : lecture du fil impossible ({_erreur(res)})")
        return 0
    posts = [p for p in res.get("data") or [] if isinstance(p, dict) and p.get("id")]
    memoire = gm.jload(gm.P(*MEMOIRE), {})
    if not isinstance(memoire, dict):
        memoire = {}
    faits = memoire.setdefault("faits", {})
    compteur = 0
    for p in reversed(posts):            # les plus anciennes d'abord
        if compteur >= LIMITE_PAR_CYCLE:
            break                        # plafond par cycle : pas de rafale
        pid = p["id"]
        deja = faits.get(pid)
        if isinstance(deja, dict) and deja.get("like") in ("ok", "deja"):
            if deja.get("verifie"):
                continue
            # Les anciennes entrées « ok » n'étaient pas vérifiées côté Facebook.
            verifie, detail = _verifier_like(gm, pid)
            if verifie is True:
                deja["verifie"] = True
                faits[pid] = deja
                gm.jsave(gm.P(*MEMOIRE), {"faits": _compacte(faits)})
                sauver("Aimes : confirmation Facebook", ["glm/aimes.json"])
                continue
            if verifie is None:
                gm.log(f"Aimes {pid} : vérification impossible ({detail}); aucun succès affirmé.")
                continue
            deja["like"] = "a_verifier"
            faits[pid] = deja
        if isinstance(deja, dict) and deja.get("essais", 0) >= ESSAIS_MAX:
            continue
        if dry:
            gm.log(f"[TEST] aimes : la publication {pid} serait aimée.")
            continue
        essais_precedents = int((deja or {}).get("essais") or 0)
        faits[pid] = {"le": _maintenant(), "like": "pending", "essais": essais_precedents}
        gm.jsave(gm.P(*MEMOIRE), {"faits": _compacte(faits)})
        if not sauver("Aimes : prise en charge", ["glm/aimes.json"]):
            gm.log(f"Aimes {pid} : enregistrement préalable impossible, like annulé.")
            return compteur
        st, res = gm.http(f"{gm.GRAPH}/{pid}/likes", {"access_token": gm.TOKEN})
        post_ok = st == 200 and isinstance(res, dict) and \
            (res.get("success") is True or res.get("id"))
        note = ""
        if isinstance(res, dict) and \
                "already" in str((res.get("error") or {}).get("message", "")).lower():
            post_ok, note = True, "déjà aimée selon la réponse API"
        verifie, detail = (False, "")
        if post_ok:
            verifie, detail = _verifier_like(gm, pid)
        ok = post_ok and verifie is True
        essais = essais_precedents + 1
        entree = {"le": _maintenant(),
                  "like": "ok" if ok else "erreur",
                  "essais": essais}
        if ok:
            entree["verifie"] = True
        if note:
            entree["note"] = note
        if not ok:
            entree["derniere_erreur"] = (
                f"POST: {_erreur(res)}; vérification: {detail or 'has_liked=false'}"
                if post_ok else _erreur(res)
            )
        compteur += 1
        # message privé facultatif à l'auteur (jamais à la Page elle-même)
        if ok and message:
            auteur = p.get("from") if isinstance(p.get("from"), dict) else {}
            if str(auteur.get("id") or "") and auteur["id"] != gm.PAGE_ID:
                rep = file_attente._repondre_message(
                    gm, {"recipient_id": auteur["id"], "message": message})
                entree["message"] = rep.get("status")
                if rep.get("erreur"):
                    entree["message_erreur"] = rep["erreur"]
            else:
                entree["message"] = "ignoré (auteur inconnu ou Page elle-même)"
        if ok or essais >= ESSAIS_MAX:
            faits[pid] = entree          # terminé (ok) ou abandonné (3 échecs)
        else:
            faits[pid] = {               # en erreur : retenté au prochain cycle
                "le": entree["le"],
                "like": "erreur",
                "essais": essais,
                "derniere_erreur": entree["derniere_erreur"],
            }
        gm.jsave(gm.P(*MEMOIRE), {"faits": _compacte(faits)})
        sauver("Aimes : publication traitée", ["glm/aimes.json"])
        gm.log(f"Aimes {pid} : like {entree['like']}"
               + (", confirmé par Facebook" if ok else f", non confirmé ({entree.get('derniere_erreur', 'vérification échouée')})")
               + (f", MP {entree.get('message')}" if message else ""))
    return compteur
