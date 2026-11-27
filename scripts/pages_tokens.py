#!/usr/bin/env python3
"""Connexion / déconnexion des Pages My Page : échange des jetons Facebook.

Flux d'une demande « connecter » (document users/{uid}/pages/{pageId}) :
  1. déchiffrer tokenEnc (jeton utilisateur court) avec la clé privée du bot ;
  2. court -> long (oauth/access_token, FB_APP_ID + FB_APP_SECRET) ;
  3. long -> jeton de Page (me/accounts) ;
  4. ranger le jeton de Page, chiffré avec MYPAGE_STORE_KEY, dans bot_tokens/{uid}__{pageId}
     (collection fermée aux clients par firestore.rules) ;
  5. status = active, et suppression de demande, tokenEnc et message.

Ce module ne fait AUCUNE écriture Firestore : il renvoie la liste d'écritures à commiter
(atomique : jeton stocké + statut). Dépôt public : aucun log, aucun message d'erreur ne
contient de jeton, d'identifiant ou de contenu ; les messages visibles par l'utilisateur
sont fixes, courts et sans donnée sensible.
"""
import dataclasses
import datetime as dt
import re
import time
import urllib.parse

import comment_store
import pages_crypto
import pages_registry as pr

COLLECTION_JETONS = "bot_tokens"

CODES_JETON = {102, 190, 460, 463, 467}
CODES_PERMISSION = {10, 200, 283, 299}
CODES_LIMITE = {4, 17, 32, 341, 613} | set(range(80000, 80015))
CODES_TEMPORAIRES = {1, 2, 101, 368}

MESSAGES = {
    "jeton": "Jeton Facebook expiré ou invalide. Reconnectez la Page.",
    "permission": "Permission Facebook manquante. Reconnectez la Page en acceptant tout.",
    "limite": "Limite Facebook atteinte, nouvel essai automatique.",
    "temporaire": "Facebook indisponible, nouvel essai automatique.",
    "introuvable": "Page introuvable dans ce compte Facebook.",
    "illisible": "Demande illisible. Reconnectez la Page.",
    "incomplete": "Demande incomplète. Reconnectez la Page.",
}

_UID = re.compile(r"^[A-Za-z0-9]{1,128}$")
_PAGE = re.compile(r"^\d{3,30}$")


class ErreurJeton(Exception):
    """Erreur d'échange. `definitive` : inutile de réessayer, l'utilisateur doit reconnecter."""

    def __init__(self, message, definitive):
        super().__init__(message)
        self.message = message
        self.definitive = definitive


@dataclasses.dataclass
class Contexte:
    projet: str
    graph: str
    app_id: str
    app_secret: str = dataclasses.field(repr=False)
    cle_privee: object = dataclasses.field(repr=False)
    cle_stockage: bytes = dataclasses.field(repr=False)
    get_json: object = None            # get_json(url) -> (statut, JSON), comme gm.http
    maintenant: object = lambda: dt.datetime.now(dt.timezone.utc)
    dormir: object = time.sleep
    masquer: object = pages_crypto.masquer


@dataclasses.dataclass
class Resultat:
    issue: str                          # active | erreur | deconnectee | attente | ignoree
    ecritures: list
    message: str = ""
    jeton_page: str = dataclasses.field(default="", repr=False)   # en mémoire seulement


def contexte_depuis_env(env, fs, get_json):
    def requis(nom):
        valeur = (env.get(nom) or "").strip()
        if not valeur:
            raise ValueError(f"{nom} absent")
        return valeur
    return Contexte(
        projet=fs.projet,
        graph="https://graph.facebook.com/" + (env.get("FB_GRAPH_VERSION") or "v26.0"),
        app_id=requis("FB_APP_ID"),
        app_secret=requis("FB_APP_SECRET"),
        cle_privee=pages_crypto.charger_cle_privee(requis("MYPAGE_RSA_PRIVATE_KEY")),
        cle_stockage=pages_crypto.charger_cle_stockage(requis("MYPAGE_STORE_KEY")),
        get_json=get_json,
    )


def chemin_page(uid, page_id):
    return f"users/{uid}/pages/{page_id}"


def chemin_jeton(uid, page_id):
    return f"{COLLECTION_JETONS}/{uid}__{page_id}"


def _entier(valeur):
    try:
        return int(valeur)
    except (TypeError, ValueError):
        return None


def classer_erreur(statut, code):
    """Transforme une erreur Facebook en ErreurJeton (message fixe, sans donnée sensible)."""
    code = _entier(code)
    if code in CODES_JETON:
        return ErreurJeton(MESSAGES["jeton"], True)
    if code in CODES_PERMISSION:
        return ErreurJeton(MESSAGES["permission"], True)
    if code in CODES_LIMITE or statut == 429:
        return ErreurJeton(MESSAGES["limite"], False)
    if code in CODES_TEMPORAIRES or not statut or statut >= 500:
        return ErreurJeton(MESSAGES["temporaire"], False)
    return ErreurJeton(f"Facebook a refusé la demande (code {code if code is not None else statut}).", True)


def echanger_long(ctx, jeton_court):
    url = f"{ctx.graph}/oauth/access_token?" + urllib.parse.urlencode({
        "grant_type": "fb_exchange_token",
        "client_id": ctx.app_id,
        "client_secret": ctx.app_secret,
        "fb_exchange_token": jeton_court,
    })
    statut, data = ctx.get_json(url)
    if statut == 200 and isinstance(data, dict):
        jeton = data.get("access_token")
        if isinstance(jeton, str) and jeton:
            return jeton
        raise ErreurJeton(MESSAGES["temporaire"], False)
    erreur = data.get("error") if isinstance(data, dict) else None
    raise classer_erreur(statut, erreur.get("code") if isinstance(erreur, dict) else None)


def jeton_de_page(ctx, jeton_long, page_id):
    url = f"{ctx.graph}/me/accounts?" + urllib.parse.urlencode({
        "fields": "id,access_token", "limit": 100, "access_token": jeton_long})
    try:
        comptes = comment_store.fetch_all(url, ctx.get_json, sleep=ctx.dormir)
    except comment_store.FacebookError as exc:
        raise classer_erreur(exc.status, exc.code) from None
    except RuntimeError:
        raise ErreurJeton(MESSAGES["temporaire"], False) from None
    for compte in comptes:
        if isinstance(compte, dict) and str(compte.get("id")) == str(page_id):
            jeton = compte.get("access_token")
            if isinstance(jeton, str) and jeton:
                return jeton
            raise ErreurJeton(MESSAGES["permission"], True)
    raise ErreurJeton(MESSAGES["introuvable"], True)


def _ecriture_statut(ctx, rec, status, message):
    champs = {"status": status}
    supprimer = ["demande", "tokenEnc"]
    if message:
        champs["message"] = message
    else:
        supprimer.append("message")
    ecriture = pr.ecriture_maj(ctx.projet, chemin_page(rec["uid"], rec["page_id"]),
                               champs, supprimer=supprimer, existe=True)
    version = rec.get("_update_time")
    if version:
        # Refus si l'appli a modifié le document depuis sa lecture (nouvelle demande, par exemple).
        ecriture["currentDocument"] = {"updateTime": version}
    return ecriture


def _echec(ctx, rec, message):
    return Resultat("erreur", [
        _ecriture_statut(ctx, rec, "erreur", message),
        pr.ecriture_suppression(ctx.projet, chemin_jeton(rec["uid"], rec["page_id"])),
    ], message)


def preparer_demande(ctx, rec):
    """Traite une demande de l'appli. Renvoie un Resultat ; l'appelant commite les écritures."""
    uid, page_id = str(rec.get("uid") or ""), str(rec.get("page_id") or "")
    if not _UID.match(uid) or not _PAGE.match(page_id):
        return Resultat("ignoree", [])
    demande = rec.get("demande")
    if demande == "deconnecter":
        return Resultat("deconnectee", [
            _ecriture_statut(ctx, rec, "deconnectee", ""),
            pr.ecriture_suppression(ctx.projet, chemin_jeton(uid, page_id)),
        ])
    if demande != "connecter":
        return Resultat("ignoree", [])
    try:
        court = pages_crypto.dechiffrer_jeton(rec.get("tokenEnc"), ctx.cle_privee)
    except pages_crypto.ErreurChiffrement:
        return _echec(ctx, rec, MESSAGES["illisible" if rec.get("tokenEnc") else "incomplete"])
    ctx.masquer(court)
    try:
        long_ = echanger_long(ctx, court)
        ctx.masquer(long_)
        jeton_page = jeton_de_page(ctx, long_, page_id)
        ctx.masquer(jeton_page)
    except ErreurJeton as exc:
        if exc.definitive:
            return _echec(ctx, rec, exc.message)
        return Resultat("attente", [], exc.message)
    stocke = pages_crypto.chiffrer_stockage(jeton_page, ctx.cle_stockage, uid, page_id)
    return Resultat("active", [
        pr.ecriture_maj(ctx.projet, chemin_jeton(uid, page_id),
                        {"tokenEnc": stocke, "maj": ctx.maintenant()}),
        _ecriture_statut(ctx, rec, "active", ""),
    ], jeton_page=jeton_page)


def lire_jeton_page(ctx, fs, uid, page_id):
    """Jeton de Page en clair depuis bot_tokens (None s'il est absent ou illisible)."""
    if not _UID.match(str(uid)) or not _PAGE.match(str(page_id)):
        return None
    doc = fs.lire_document(chemin_jeton(uid, page_id))
    if not doc or not doc.get("tokenEnc"):
        return None
    try:
        jeton = pages_crypto.dechiffrer_stockage(doc["tokenEnc"], ctx.cle_stockage, uid, page_id)
    except pages_crypto.ErreurChiffrement:
        return None
    ctx.masquer(jeton)
    return jeton
