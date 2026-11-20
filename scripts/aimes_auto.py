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
    st, res = gm.http(f"{gm.GRAPH}/{gm.PAGE_ID}/feed", {
        "fields": "id,from,created_time", "limit": "25", "access_token": gm.TOKEN,
    })
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
        if isinstance(deja, dict):
            if deja.get("like") in ("ok", "deja") or deja.get("essais", 0) >= ESSAIS_MAX:
                continue                 # terminé ou abandonné après essais
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
        ok = st == 200 and isinstance(res, dict) and \
            (res.get("success") is True or res.get("id"))
        note = ""
        if isinstance(res, dict) and \
                "already" in str((res.get("error") or {}).get("message", "")).lower():
            ok, note = True, "déjà aimée"
        essais = essais_precedents + 1
        entree = {"le": _maintenant(),
                  "like": "ok" if ok else "erreur",
                  "essais": essais}
        if note:
            entree["note"] = note
        if not ok:
            entree["derniere_erreur"] = _erreur(res)
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
               + (f", MP {entree.get('message')}" if message else ""))
    return compteur
