#!/usr/bin/env python3
"""Contexte d'une Page My Page : permet de réutiliser le bot existant (glm_manager) pour
plusieurs Pages sans le réécrire.

glm_manager garde sa configuration dans des globales de module (TOKEN, PAGE_ID, ROOT, DRY)
et des compteurs (USAGE, APPELS, journal, erreurs). Le traitement étant séquentiel, on les
réaffecte le temps d'une Page puis on restaure tout, même en cas d'exception.

Dépôt public : aucun identifiant de Page, jeton ni texte d'erreur n'est conservé ici.
Les journaux n'utilisent que `etiquette` (ex. « Page 3 »).
"""
import contextlib
import os
import re

_UID = re.compile(r"^[A-Za-z0-9]{1,128}$")
_PAGE = re.compile(r"^\d{3,30}$")


def dossier_page(base, uid, page_id, creer=True):
    """Dossier de données d'une Page, à l'intérieur de `base` (aucun risque de sortie du dossier)."""
    if not _UID.match(str(uid)) or not _PAGE.match(str(page_id)):
        raise ValueError("identifiants de Page invalides")
    chemin = os.path.join(os.path.abspath(base), f"{uid}__{page_id}")
    if creer:
        os.makedirs(chemin, exist_ok=True)
    return chemin


class ContextePage:
    _actif = None   # un seul contexte actif à la fois (les globales de gm sont partagées)

    def __init__(self, uid, page_id, token, dossier, etiquette="Page", profil=None, quota=None,
                 dry_run=False):
        if not _UID.match(str(uid)) or not _PAGE.match(str(page_id)):
            raise ValueError("identifiants de Page invalides")
        if not isinstance(token, str) or not token:
            raise ValueError("jeton de Page absent")
        self.uid = str(uid)
        self.page_id = str(page_id)
        self._token = token
        self.dossier = dossier
        self.etiquette = str(etiquette)
        self.profil = profil
        self.quota = quota
        self.dry_run = bool(dry_run)
        # Rempli à la sortie de activer() : compteurs seulement.
        self.appels_graph = 0
        self.usage = {"pct": 0, "attente_min": 0}
        self.nb_erreurs = 0

    def __repr__(self):
        return f"<ContextePage {self.etiquette}>"

    @contextlib.contextmanager
    def activer(self, gm):
        """Installe cette Page dans gm ; restaure l'état précédent à la sortie."""
        if ContextePage._actif is not None:
            raise RuntimeError("un contexte de Page est déjà actif")
        ContextePage._actif = self
        avant = {nom: getattr(gm, nom) for nom in ("TOKEN", "PAGE_ID", "ROOT", "DRY", "NOW")}
        usage_avant, appels_avant = dict(gm.USAGE), dict(gm.APPELS)
        journal_avant, erreurs_avant = list(gm.journal), list(gm.erreurs)
        try:
            os.makedirs(self.dossier, exist_ok=True)
            gm.TOKEN, gm.PAGE_ID, gm.ROOT, gm.DRY = self._token, self.page_id, self.dossier, self.dry_run
            gm.USAGE.clear()
            gm.USAGE.update(pct=0, attente_min=0)
            gm.APPELS.clear()
            gm.APPELS["graph"] = 0
            gm.journal.clear()
            gm.erreurs.clear()
            yield self
        finally:
            self.appels_graph = int(gm.APPELS.get("graph") or 0)
            self.usage = {"pct": int(gm.USAGE.get("pct") or 0),
                          "attente_min": int(gm.USAGE.get("attente_min") or 0)}
            self.nb_erreurs = len(gm.erreurs)
            for nom, valeur in avant.items():
                setattr(gm, nom, valeur)
            gm.USAGE.clear()
            gm.USAGE.update(usage_avant)
            gm.APPELS.clear()
            gm.APPELS.update(appels_avant)
            gm.journal[:] = journal_avant
            gm.erreurs[:] = erreurs_avant
            ContextePage._actif = None
