#!/usr/bin/env python3
"""Éléments de la collection `file` (publication, photo, programmation demandées par l'appli).

Module sans Firestore : valider() lit un document décodé, publier() appelle Facebook via une
fonction injectée envoyer(url, data) -> (status, payload) (en prod : glm_manager.http).

Format d'un élément (créé par l'appli, jamais modifié par elle) :
  pageId (chiffres), type ("post" | "photo"), message (<= 5000), photoUrl (https, pour "photo"),
  scheduledAt (facultatif, ISO) ; `status` est écrit par le bot seulement.

Dépôt PUBLIC : aucun texte utilisateur ni message d'erreur Facebook n'est renvoyé, seulement
des issues et des messages fixes.
"""
import datetime as dt
import re

import pages_registry as pr
import pages_schema as sc
import pages_tokens as pt

MESSAGE_MAX = 5000
PROG_MIN = 600               # Facebook : 10 minutes minimum
PROG_MAX = 30 * 86400        # Facebook : 30 jours maximum
TYPES = ("post", "photo")
_PAGE = re.compile(r"^\d{3,30}$")
_HTTPS = re.compile(r"^https://[^\s]{4,1000}$")

MESSAGES = {
    "page": "Page invalide.",
    "type": "Type de publication inconnu.",
    "vide": "Message vide.",
    "long": "Message trop long.",
    "photo": "Adresse de la photo invalide.",
    "date": "Date de programmation invalide (entre 10 minutes et 30 jours).",
    "jeton": "Jeton expiré : reconnectez la Page.",
    "refuse": "Publication refusée par Facebook.",
}


def valider(doc, maintenant):
    """Renvoie {"etat": "ignore"|"invalide"|"ok", ...}.
    ok -> type, page_id, message, photo_url, programme (timestamp Unix ou None) ;
    invalide -> erreur (message fixe)."""
    if not isinstance(doc, dict):
        return {"etat": "ignore"}
    if doc.get("status") in sc.STATUTS_FILE:
        return {"etat": "ignore"}                 # déjà traité
    page_id = str(doc.get("pageId") or "")
    if not _PAGE.match(page_id):
        return {"etat": "invalide", "erreur": MESSAGES["page"]}
    genre = doc.get("type")
    if genre not in TYPES:
        return {"etat": "invalide", "erreur": MESSAGES["type"]}
    message = str(doc.get("message") or "").strip()
    if len(message) > MESSAGE_MAX:
        return {"etat": "invalide", "erreur": MESSAGES["long"]}
    photo = str(doc.get("photoUrl") or "")
    if genre == "post" and not message:
        return {"etat": "invalide", "erreur": MESSAGES["vide"]}
    if genre == "photo" and not _HTTPS.match(photo):
        return {"etat": "invalide", "erreur": MESSAGES["photo"]}
    programme = None
    if doc.get("scheduledAt"):
        quand = sc.horodatage(doc.get("scheduledAt"))
        ecart = (quand - maintenant).total_seconds() if quand else None
        if ecart is None or not PROG_MIN <= ecart <= PROG_MAX:
            return {"etat": "invalide", "erreur": MESSAGES["date"]}
        programme = int(quand.timestamp())
    return {"etat": "ok", "type": genre, "page_id": page_id, "message": message,
            "photo_url": photo if genre == "photo" else "", "programme": programme}


def publier(envoyer, graph, token, demande, dry_run=False):
    """Publie une demande valide. Renvoie {"issue", "post_id", "erreur"}.
    issue : published | scheduled | error | retard (à retenter plus tard) | dry_run."""
    if dry_run:
        return {"issue": "dry_run", "post_id": "", "erreur": ""}
    data = {"access_token": token}
    if demande["type"] == "post":
        url = f"{graph}/{demande['page_id']}/feed"
        data["message"] = demande["message"]
    else:
        url = f"{graph}/{demande['page_id']}/photos"
        data["url"] = demande["photo_url"]
        if demande["message"]:
            data["caption"] = demande["message"]
    if demande["programme"]:
        data["published"] = "false"
        data["scheduled_publish_time"] = str(demande["programme"])
    try:
        statut, reponse = envoyer(url, data)
    except Exception:
        return {"issue": "retard", "post_id": "", "erreur": ""}    # panne réseau : on retentera
    reponse = reponse if isinstance(reponse, dict) else {}
    identifiant = str(reponse.get("post_id") or reponse.get("id") or "")
    if statut == 200 and identifiant:
        issue = "scheduled" if demande["programme"] else "published"
        return {"issue": issue, "post_id": identifiant, "erreur": ""}
    code = (reponse.get("error") or {}).get("code")
    try:
        code = int(code)
    except (TypeError, ValueError):
        code = None
    if code in pt.CODES_LIMITE or statut == 429 or (isinstance(statut, int) and statut >= 500):
        return {"issue": "retard", "post_id": "", "erreur": ""}
    if code in pt.CODES_JETON:
        return {"issue": "error", "post_id": "", "erreur": MESSAGES["jeton"]}
    return {"issue": "error", "post_id": "", "erreur": MESSAGES["refuse"]}


# ---------- lecture de la collection `file` ----------
_NOM_FILE = re.compile(r"/documents/users/([^/]+)/file/([^/]+)$")
LIMITE_LECTURE = 50   # éléments lus par cycle (chaque document lu compte dans le quota gratuit)


def requete_en_attente(limite=LIMITE_LECTURE):
    """Requête Firestore : seuls les éléments status == "pending", tous utilisateurs confondus."""
    return {
        "from": [{"collectionId": "file", "allDescendants": True}],
        "where": {"fieldFilter": {"field": {"fieldPath": "status"}, "op": "EQUAL",
                                  "value": {"stringValue": "pending"}}},
        "limit": int(limite),
    }


def decoder_element(doc):
    """Document brut -> dict avec uid et element_id, ou None si ce n'est pas users/*/file/*."""
    m = _NOM_FILE.search(str((doc or {}).get("name") or ""))
    if not m:
        return None
    rec = pr.decoder_champs(doc.get("fields"))
    rec["uid"], rec["element_id"] = m.group(1), m.group(2)
    return rec


def lister_en_attente(fs, limite=LIMITE_LECTURE):
    return [e for e in map(decoder_element, fs.requete(requete_en_attente(limite))) if e]
