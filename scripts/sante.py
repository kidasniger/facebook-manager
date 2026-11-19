#!/usr/bin/env python3
"""Rapport de santé du bot : bot/sante.json, lu par l'appli de contrôle.

Écrit chaque cycle de la boucle glm-live, poussé au plus toutes les
PUSH_MINUTES minutes (et en fin de session, ou pendant une panne pour que
l'appli voie l'erreur) : heure du dernier passage, quota Facebook annoncé
par l'API, nombre d'appels, erreurs récentes (commentaires, lecture,
files d'attente de l'appli) et état des files.

L'appli de contrôle lit ce fichier en lecture seule — elle n'écrit jamais
dans ce dépôt.
"""
import datetime as dt
import time

FICHIER = "bot/sante.json"
PUSH_MINUTES = 20        # rythme de commit du rapport (l'appli tolère 30 min de silence)
MAX_ERREURS = 10
FICHIERS_FILE = ("posts/queue.json", "posts/avance.json", "posts/replies.json",
                 "posts/messages.json", "posts/actions.json")

_dernier_push = 0.0


def _maintenant():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _file_attente(gm):
    """Files de l'appli : éléments en attente + erreurs récentes (traite_le)."""
    en_attente = 0
    erreurs = []
    for chemin in FICHIERS_FILE:
        data = gm.jload(gm.P(*chemin.split("/")), [])
        if not isinstance(data, list):
            continue
        for item in data:
            if not isinstance(item, dict):
                continue
            st = item.get("status")
            if st == "pending":
                en_attente += 1
            elif st == "error":
                erreurs.append({
                    "at": str(item.get("traite_le") or ""),
                    "origine": chemin.split("/")[-1],
                    "detail": f"{item.get('action') or 'action'} — "
                              f"{str(item.get('erreur') or 'erreur')[:160]}",
                })
    erreurs.sort(key=lambda e: e.get("at") or "")
    return en_attente, erreurs[-MAX_ERREURS:]


def maj(gm, session=None, echecs=0, extra=()):
    """Met à jour bot/sante.json avec l'état courant. Ne pousse pas.

    - gm.erreurs : erreurs des commentaires du cycle courant (vidées à chaque passe) ;
    - extra : erreurs hors commentaires (lecture Facebook, erreur de boucle) ;
    - les erreurs de file viennent des fichiers eux-mêmes (traite_le).
    La fusion est dédoublonnée par (origine, détail) — une erreur qui persiste
    met à jour son heure au lieu de s'empiler.
    """
    en_attente, err_file = _file_attente(gm)
    prec = gm.jload(gm.P(*FICHIER.split("/")), {})
    fusion = {}
    for e in list(prec.get("erreurs") or []) + err_file:
        if isinstance(e, dict) and e.get("detail"):
            fusion[(str(e.get("origine") or ""), str(e.get("detail")))] = dict(e)
    for s in list(gm.erreurs or []) + list(extra or []):
        cle = ("commentaires", str(s)[:160])
        fusion[cle] = {"at": _maintenant(), "origine": "commentaires",
                       "detail": cle[1]}
    erreurs = sorted(fusion.values(), key=lambda e: e.get("at") or "")[-MAX_ERREURS:]
    doc = {
        "mis_a_jour": _maintenant(),
        "session": dict(session or {}),
        "quota": {"pct": int(gm.USAGE.get("pct") or 0),
                  "attente_min": int(gm.USAGE.get("attente_min") or 0)},
        "appels_graph": int(gm.APPELS.get("graph") or 0),
        "echecs_consecutifs": int(echecs or 0),
        "file": {"en_attente": en_attente},
        "erreurs": erreurs,
    }
    gm.jsave(gm.P(*FICHIER.split("/")), doc)
    return doc


def pousser(gm, enregistrer, force=False):
    """Pousse le rapport de santé au plus une fois par PUSH_MINUTES minutes.

    enregistrer ne commite que si le contenu a changé (diff git) : un appel
    régulier est donc sans coût quand rien n'a bougé.
    """
    global _dernier_push
    if not force and time.time() - _dernier_push < PUSH_MINUTES * 60:
        return False
    _dernier_push = time.time()
    return bool(enregistrer("Santé du bot", [FICHIER]))
