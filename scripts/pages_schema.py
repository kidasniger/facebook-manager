#!/usr/bin/env python3
"""Schéma Firestore des données par Page : chemins, identifiants et champs écrits par le bot.

Tout est sous users/{uid}/ (firestore.rules) ; un document porte l'identifiant de Page en préfixe
({pageId}__{élément}). Le bot n'écrit jamais `traite` (inbox) : il appartient au client.
Aucune donnée inutile : prénom seulement, textes tronqués, jamais d'identifiant de visiteur.
Module pur : aucun accès réseau. Les valeurs datetime sont encodées par pages_registry.encoder_valeur.
"""
import datetime as dt
import hashlib
import json
import re

_UID = re.compile(r"^[A-Za-z0-9]{1,128}$")
_PAGE = re.compile(r"^\d{3,30}$")
_ELEMENT = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_JOUR = re.compile(r"^\d{4}-\d\d-\d\d$")
_CLE_STAT = re.compile(r"^[a-z][a-z0-9_]{0,39}$")

LIMITE_AVIS = 500
LIMITE_MESSAGE = 300
LIMITE_POST = 100
LIMITE_ERREUR = 160
STATUTS_FILE = ("published", "scheduled", "replied", "done", "error")


def _verifier(uid, page_id):
    if not _UID.match(str(uid)) or not _PAGE.match(str(page_id)):
        raise ValueError("identifiants invalides")


def _element(element_id):
    if not _ELEMENT.match(str(element_id)):
        raise ValueError("identifiant d'élément invalide")
    return str(element_id)


def _jour(jour):
    if not _JOUR.match(str(jour)):
        raise ValueError("jour invalide")
    return str(jour)


# ---------- chemins ----------
def chemin_profil(uid, page_id):
    _verifier(uid, page_id)
    return f"users/{uid}/profil/{page_id}"


def chemin_inbox(uid, page_id, element_id):
    _verifier(uid, page_id)
    return f"users/{uid}/inbox/{page_id}__{_element(element_id)}"


def chemin_stats(uid, page_id, jour):
    _verifier(uid, page_id)
    return f"users/{uid}/stats/{page_id}__{_jour(jour)}"


def chemin_quota(uid, page_id, jour):
    _verifier(uid, page_id)
    return f"users/{uid}/quota/{page_id}__{_jour(jour)}"


def chemin_file(uid, element_id):
    if not _UID.match(str(uid)):
        raise ValueError("identifiants invalides")
    return f"users/{uid}/file/{_element(element_id)}"


def id_avis(commentaire):
    return "c-" + _element((commentaire or {}).get("id") or "")


def id_conversation(conv):
    return "m-" + _element((conv or {}).get("id") or "")


# ---------- outils ----------
def jour_utc(moment):
    return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%d")


def tronquer(texte, limite):
    return re.sub(r"\s+", " ", str(texte or "")).strip()[:limite]


def prenom(nom):
    morceaux = tronquer(nom, 80).split(" ")
    return morceaux[0][:40] if morceaux and morceaux[0] else ""


def horodatage(texte):
    """Date Facebook (« 2026-11-01T10:00:00+0000 ») -> datetime UTC, ou None."""
    texte = str(texte or "").strip()
    for lecture in (lambda t: dt.datetime.strptime(t, "%Y-%m-%dT%H:%M:%S%z"),
                    lambda t: dt.datetime.fromisoformat(t.replace("Z", "+00:00"))):
        try:
            d = lecture(texte)
        except ValueError:
            continue
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.astimezone(dt.timezone.utc)
    return None


def empreinte(champs):
    """Empreinte stable d'un document : sert à ne pas réécrire ce qui n'a pas changé."""
    brut = json.dumps(champs, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(brut.encode("utf-8")).hexdigest()


# ---------- champs écrits par le bot ----------
def champs_avis(page_id, commentaire, texte_post=""):
    """Commentaire -> champs inbox. Jamais d'identifiant de visiteur ni de nom complet."""
    c = commentaire or {}
    champs = {
        "pageId": str(page_id),
        "type": "avis",
        "texte": tronquer(c.get("message"), LIMITE_AVIS),
        "nom": prenom((c.get("from") or {}).get("name")),
        "post": tronquer(texte_post, LIMITE_POST),
        "repondu": bool(c.get("page_replied")),
    }
    quand = horodatage(c.get("created_time"))
    if quand:
        champs["ts"] = quand
    return champs


def champs_conversation(page_id, conv):
    """Conversation Messenger -> champs inbox (dernier message seulement)."""
    conv = conv or {}
    messages = conv.get("messages") if isinstance(conv.get("messages"), list) else []
    dernier = messages[-1] if messages and isinstance(messages[-1], dict) else {}
    champs = {
        "pageId": str(page_id),
        "type": "msg",
        "texte": tronquer(dernier.get("message"), LIMITE_MESSAGE),
        "nom": prenom((conv.get("with") or {}).get("name")),
        "repondu": bool(dernier.get("from_page")),
    }
    quand = horodatage(conv.get("updated_time"))
    if quand:
        champs["ts"] = quand
    return champs


def champs_stats(page_id, jour, valeurs):
    """Indicateurs du jour : uniquement des nombres, sous des clés simples."""
    propres = {}
    for cle, valeur in (valeurs or {}).items():
        if _CLE_STAT.match(str(cle)) and isinstance(valeur, (int, float)) and not isinstance(valeur, bool):
            propres[str(cle)] = valeur
    return {"pageId": str(page_id), "jour": _jour(jour), "valeurs": propres}


def champs_quota(page_id, jour, utilise, limite):
    return {"pageId": str(page_id), "jour": _jour(jour),
            "utilise": max(0, int(utilise)), "limite": max(0, int(limite))}


def champs_file_resultat(statut, maintenant, erreur="", post_id=""):
    """Résultat d'un élément de `file` -> (champs à écrire, champs à effacer)."""
    if statut not in STATUTS_FILE:
        raise ValueError("statut inconnu")
    champs = {"status": statut, "traiteLe": maintenant}
    supprimer = []
    for nom, valeur in (("erreur", tronquer(erreur, LIMITE_ERREUR)), ("postId", str(post_id or ""))):
        if valeur:
            champs[nom] = valeur
        else:
            supprimer.append(nom)
    return champs, supprimer
