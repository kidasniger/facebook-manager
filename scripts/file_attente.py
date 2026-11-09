"""Exécute les files d'attente de l'app depuis la boucle glm-live.

Remplace, en Python, les quatre workflows qu'on lançait un par un :
  posts/queue.json    (publier.yml)         texte seul
  posts/avance.json   (publier-avance.yml)  photo, vidéo, programmation
  posts/replies.json  (repondre.yml)        réponses à des commentaires
  posts/messages.json (nouveau)             réponses Messenger (uniquement ici, pas d'ancien workflow)
  posts/actions.json  (actions-posts.yml)   modifier / supprimer un post

Garanties :
- Aucun doublon : avant d'appeler Facebook, l'élément est marqué (claimed_at) et enregistré
  dans le dépôt de données. Si la session s'interrompt avant la réponse de Facebook,
  l'élément passe en « error » au redémarrage au lieu d'être renvoyé à l'aveugle.
- Les statuts restent ceux que l'app comprend : pending, published, scheduled, replied, done, error.
- Une pause entre deux envois et un plafond par cycle évitent les rafales.
"""
import datetime as dt
import json
import os
import subprocess
import time
import urllib.parse

FICHIERS = ("posts/queue.json", "posts/avance.json", "posts/replies.json",
            "posts/messages.json", "posts/actions.json")
PAUSE_ENTRE_ENVOIS = 3
MAX_PAR_CYCLE = 5
MAX_MESSAGE_ENCODE = 6500   # le texte voyage dans l'URL : on borne sa taille une fois encodé


def _maintenant():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _erreur(res):
    err = (res or {}).get("error") if isinstance(res, dict) else None
    if isinstance(err, dict):
        return f"code {err.get('code')} : {str(err.get('message') or '')[:160]}"
    brut = res.get("erreur") or res.get("raw") if isinstance(res, dict) else None
    return str(brut or "réponse inattendue")[:160]


def _horodatage(texte):
    d = dt.datetime.fromisoformat(str(texte).strip().replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp())


def _chemin_sur(gm, relatif):
    """Fichier local à téléverser : uniquement à l'intérieur du dépôt de données."""
    racine = os.path.realpath(gm.ROOT)
    chemin = os.path.realpath(os.path.join(racine, relatif))
    if not chemin.startswith(racine + os.sep) or not os.path.isfile(chemin):
        raise ValueError("fichier introuvable dans le dépôt de données")
    return chemin


def _curl_fichier(gm, url, chemin, champs):
    cmd = ["curl", "-s", "-X", "POST", url, "-F", f"source=@{chemin}"]
    for cle, valeur in champs.items():
        cmd += ["--form-string", f"{cle}={valeur}"]
    sortie = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    try:
        return json.loads(sortie.stdout or "{}")
    except ValueError:
        return {"erreur": "réponse illisible"}


# ---------- un traitement par type de file ----------

def _publier_texte(gm, item):
    message = item.get("message") or ""
    if not message.strip():
        return {"status": "error", "erreur": "message vide"}
    st, res = gm.http(f"{gm.GRAPH}/{gm.PAGE_ID}/feed", {"message": message, "access_token": gm.TOKEN})
    pid = res.get("id") if isinstance(res, dict) else None
    if st == 200 and pid:
        return {"status": "published", "post_id": pid}
    return {"status": "error", "erreur": _erreur(res)}


def _publier_avance(gm, item):
    message = item.get("message") or ""
    quand = item.get("publish_at") or ""
    prog = {}
    if quand:
        try:
            prog = {"published": "false", "scheduled_publish_time": str(_horodatage(quand))}
        except ValueError:
            return {"status": "error", "erreur": "date de programmation illisible"}
    base = f"{gm.GRAPH}/{gm.PAGE_ID}"
    img, img_url = item.get("image_path"), item.get("image_url")
    vid, vid_url = item.get("video_path"), item.get("video_url")
    try:
        if img:
            res = _curl_fichier(gm, f"{base}/photos", _chemin_sur(gm, img),
                                {"caption": message, **prog, "access_token": gm.TOKEN})
        elif img_url:
            st, res = gm.http(f"{base}/photos", {"url": img_url, "caption": message, **prog, "access_token": gm.TOKEN})
        elif vid:
            res = _curl_fichier(gm, f"{base}/videos", _chemin_sur(gm, vid),
                                {"description": message, **prog, "access_token": gm.TOKEN})
        elif vid_url:
            st, res = gm.http(f"{base}/videos", {"file_url": vid_url, "description": message, **prog, "access_token": gm.TOKEN})
        else:
            if not message.strip():
                return {"status": "error", "erreur": "message vide"}
            st, res = gm.http(f"{base}/feed", {"message": message, **prog, "access_token": gm.TOKEN})
    except ValueError as exc:
        return {"status": "error", "erreur": str(exc)}
    pid = (res.get("post_id") or res.get("id")) if isinstance(res, dict) else None
    if pid:
        return {"status": "scheduled" if quand else "published", "post_id": pid}
    return {"status": "error", "erreur": _erreur(res)}


def _repondre(gm, item):
    cid, message = item.get("comment_id"), item.get("message") or ""
    if not cid or not message.strip():
        return {"status": "error", "erreur": "commentaire ou message manquant"}
    st, res = gm.http(f"{gm.GRAPH}/{cid}/comments", {"message": message, "access_token": gm.TOKEN})
    rid = res.get("id") if isinstance(res, dict) else None
    if st == 200 and rid:
        return {"status": "replied", "reply_id": rid}
    return {"status": "error", "erreur": _erreur(res)}


def _erreur_message(res):
    """Messages d'erreur compréhensibles pour les cas courants de l'envoi Messenger."""
    err = (res or {}).get("error") if isinstance(res, dict) else None
    if isinstance(err, dict):
        if err.get("code") == 10 and err.get("error_subcode") == 2018278:
            return "fenêtre de 24 h dépassée : la personne doit d'abord réécrire"
        if err.get("code") in (10, 200):
            return "permission refusée (pages_messaging ou fenêtre de 24 h)"
        if err.get("code") == 551:
            return "destinataire indisponible"
    return _erreur(res)


def _repondre_message(gm, item):
    destinataire, message = str(item.get("recipient_id") or ""), item.get("message") or ""
    if not destinataire or not message.strip():
        return {"status": "error", "erreur": "destinataire ou message manquant"}
    # Contenu dans l'URL, jeton dans le corps : ne dépend pas de la façon dont gm.http encode le corps.
    params = urllib.parse.urlencode({
        "recipient": json.dumps({"id": destinataire}),
        "message": json.dumps({"text": message}, ensure_ascii=False),
        "messaging_type": "RESPONSE",
    })
    if len(params) > MAX_MESSAGE_ENCODE:
        return {"status": "error", "erreur": "message trop long"}
    st, res = gm.http(f"{gm.GRAPH}/{gm.PAGE_ID}/messages?{params}", {"access_token": gm.TOKEN})
    mid = res.get("message_id") if isinstance(res, dict) else None
    if st == 200 and mid:
        return {"status": "replied", "message_id": mid}
    return {"status": "error", "erreur": _erreur_message(res)}


def _agir(gm, item):
    action, pid = item.get("action"), item.get("post_id")
    if not pid:
        return {"status": "error", "erreur": "post_id manquant"}
    if action == "delete":
        st, res = gm.http(f"{gm.GRAPH}/{pid}", {"access_token": gm.TOKEN}, method="DELETE")
    elif action == "edit":
        st, res = gm.http(f"{gm.GRAPH}/{pid}", {"message": item.get("message") or "", "access_token": gm.TOKEN})
    else:
        return {"status": "error", "erreur": "action inconnue"}
    if isinstance(res, dict) and res.get("success") is True:
        return {"status": "done"}
    return {"status": "error", "erreur": _erreur(res)}


TRAITEMENTS = (
    ("posts/queue.json", _publier_texte),
    ("posts/avance.json", _publier_avance),
    ("posts/replies.json", _repondre),
    ("posts/messages.json", _repondre_message),
    ("posts/actions.json", _agir),
)


# ---------- orchestration ----------

def recuperer_interrompus(gm, sauver):
    """À appeler une fois au démarrage d'une session : tout élément « pending » déjà marqué
    appartient à une session qui s'est arrêtée avant la réponse de Facebook. On ne le renvoie
    pas (risque de doublon) : on le signale en erreur, l'app permet de le remettre en attente."""
    touches = []
    for chemin in FICHIERS:
        data = gm.jload(gm.P(*chemin.split("/")), [])
        if not isinstance(data, list):
            continue
        change = False
        for item in data:
            if isinstance(item, dict) and item.get("status") == "pending" and item.get("claimed_at"):
                item.pop("claimed_at", None)
                item["status"] = "error"
                item["erreur"] = "interrompu avant confirmation : vérifier la Page avant de renvoyer"
                change = True
        if change:
            gm.jsave(gm.P(*chemin.split("/")), data)
            touches.append(chemin)
    if touches:
        sauver("File d'attente : éléments interrompus signalés", touches)
    return touches


def traiter(gm, sauver, dry=False, maxi=MAX_PAR_CYCLE, pause=PAUSE_ENTRE_ENVOIS):
    """Exécute au plus `maxi` éléments en attente. Renvoie le nombre d'éléments traités."""
    faits = 0
    for chemin, fonction in TRAITEMENTS:
        absolu = gm.P(*chemin.split("/"))
        data = gm.jload(absolu, [])
        if not isinstance(data, list):
            continue
        for item in data:
            if not isinstance(item, dict) or item.get("status") != "pending":
                continue
            if faits >= maxi:
                return faits
            if dry:
                gm.log(f"[TEST] {chemin} : un élément serait traité.")
                continue
            item["claimed_at"] = _maintenant()
            gm.jsave(absolu, data)
            sauver("File d'attente : prise en charge", [chemin])
            try:
                resultat = fonction(gm, item)
            except Exception as exc:  # jamais de plantage de la boucle pour un élément
                resultat = {"status": "error", "erreur": type(exc).__name__}
            item.pop("claimed_at", None)
            item.update(resultat)
            item["traite_le"] = _maintenant()
            gm.jsave(absolu, data)
            sauver(f"File d'attente : {chemin.split('/')[-1]} -> {resultat['status']}", [chemin])
            gm.log(f"File d'attente {chemin} : {resultat['status']}"
                   + (f" ({resultat['erreur']})" if resultat.get("erreur") else ""))
            faits += 1
            time.sleep(pause)
    return faits
