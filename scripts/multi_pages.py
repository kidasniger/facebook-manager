#!/usr/bin/env python3
"""Orchestrateur multi-Pages : un cycle du bot existant (glm_manager) par Page My Page.

- Séquentiel : les globales de glm_manager sont réaffectées Page par Page (voir page_context).
- Isolation : l'échec d'une Page (exception, jeton, limite Facebook) ne bloque jamais les autres ;
  la Page fautive est mise en pause avec un délai croissant.
- Dépôt PUBLIC : pendant le traitement d'une Page, la sortie de glm_manager est jetée (elle contient
  des identifiants de commentaires). Seul un résumé de compteurs est affiché, par étiquette.
- Les autres Pages ne parlent jamais au nom du propriétaire d'origine : profil toujours défini,
  posts automatiques coupés, ton et mots interdits propres à la Page.
Désactivé par défaut : variable MULTI_PAGES=on pour l'activer.
"""
import contextlib
import datetime as dt
import time

import pages_schema as sc
import pages_tokens as pt

QUOTA_IA_DEFAUT = 50   # appels GLM par Page et par jour, si aucun quota n'est fourni


def multi_pages_actif(env):
    """MULTI_PAGES=on active le mode ; tout le reste (absent, off, vide) le désactive."""
    return str((env or {}).get("MULTI_PAGES") or "off").strip().lower() == "on"


class QuotaIA:
    """Compteur d'appels GLM d'une Page pour la journée."""

    def __init__(self, limite=QUOTA_IA_DEFAUT, utilise=0):
        self.limite = int(limite)
        self.utilise = int(utilise)

    @property
    def atteint(self):
        return self.utilise >= self.limite


class EtatPage:
    """Mémoire d'exécution d'une Page (en RAM) : échecs consécutifs et pause."""

    def __init__(self):
        self.echecs = 0
        self.pause_jusqua = 0.0


class _Poubelle:
    def write(self, texte):
        return len(texte)

    def flush(self):
        pass


def cfg_pour_page(cfg, profil):
    """Réglages d'une autre Page : clés techniques du bot (api_url, modele), mais ni le ton, ni
    les mots interdits, ni les posts automatiques du propriétaire d'origine."""
    base = dict(cfg or {})
    profil = profil if isinstance(profil, dict) else {}
    base["posts"] = {"actif": False}
    base["reponses"] = dict(profil["reponses"]) if isinstance(profil.get("reponses"), dict) else {}
    base["moderation"] = dict(profil["moderation"]) if isinstance(profil.get("moderation"), dict) else {}
    base.pop("horaires", None)
    base["actif"] = True
    return base


def _glm_avec_quota(original, quota):
    def glm(cfg, system, user, temperature=0.7, max_tokens=1500):
        if quota.atteint:
            raise RuntimeError("quota IA du jour atteint")
        quota.utilise += 1
        return original(cfg, system, user, temperature, max_tokens)
    return glm


INBOX_MAX = 30   # commentaires récents gardés par Page et par cycle


def collecter_inbox(page_id, posts, limite=INBOX_MAX):
    """Commentaires récents -> [(identifiant de document, champs inbox)]. Champs réduits (schéma)."""
    elements = []
    for post in posts if isinstance(posts, list) else []:
        if not isinstance(post, dict):
            continue
        comments = ((post.get("comments") or {}).get("data")) or []
        for c in comments:
            try:
                elements.append((sc.id_avis(c), sc.champs_avis(page_id, c, post.get("message"))))
            except (ValueError, AttributeError):
                continue
    debut = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    elements.sort(key=lambda e: e[1].get("ts") or debut, reverse=True)
    return elements[:limite]


def traiter_page(gm, ctx, cfg, sauver=None, silencieux=True):
    """Un cycle pour UNE Page. Renvoie un bilan de compteurs (aucun identifiant, aucun texte)."""
    profil = ctx.profil if isinstance(ctx.profil, dict) else {}
    quota = ctx.quota if isinstance(ctx.quota, QuotaIA) else QuotaIA()
    cfg_page = cfg_pour_page(cfg, profil)
    ancien_profil, ancien_glm = gm.PROFIL, gm.glm
    poubelle = _Poubelle()
    sortie = contextlib.redirect_stdout(poubelle) if silencieux else contextlib.nullcontext()
    erreur = contextlib.redirect_stderr(poubelle) if silencieux else contextlib.nullcontext()
    bilan = {"etiquette": ctx.etiquette, "statut": "ok", "reponses": 0, "a_traiter": 0, "erreurs": 0}
    try:
        with sortie, erreur, ctx.activer(gm):
            gm.PROFIL = profil
            gm.glm = _glm_avec_quota(ancien_glm, quota)
            gm.NOW = dt.datetime.now(dt.timezone.utc)
            state = gm.jload(gm.P("glm", "etat.json"), {})
            for cle in ("posts", "reponses", "a_traiter"):
                state.setdefault(cle, [])
            chemin_registre = gm.P("inbox", "commentaires-traites.json")
            state["comment_ledger"] = gm.comment_store.load_ledger(chemin_registre, ())
            statut, res = gm.fb_posts()
            if statut != 200:
                err = (res or {}).get("error") or {}
                code = err.get("code")
                try:
                    code = int(code)
                except (TypeError, ValueError):
                    code = None
                if code in pt.CODES_JETON:
                    bilan["statut"] = "jeton"
                elif code in pt.CODES_LIMITE or statut == 429:
                    bilan["statut"] = "limite"
                else:
                    bilan["statut"] = "facebook"
            else:
                ctx.inbox = collecter_inbox(ctx.page_id, res.get("data", []))
                envoyees, humain = gm.gerer_reponses(cfg_page, state, res.get("data", []), sauver=sauver)
                bilan["reponses"] = len(envoyees)
                deja = {str(x.get("comment_id")) for x in state["a_traiter"]}
                nouveaux = [h for h in humain if str(h.get("comment_id")) not in deja]
                bilan["a_traiter"] = len(nouveaux)
                if nouveaux and not gm.DRY:
                    state["a_traiter"] = (state["a_traiter"] + nouveaux)[-100:]
                    for item in nouveaux:
                        gm.comment_store.record(state["comment_ledger"], item["comment_id"], "human",
                                                date=item["date"], reason=item["raison"], source="glm-live")
                    gm.comment_store.save_ledger(state["comment_ledger"], chemin_registre)
                    gm.jsave(gm.P("inbox", "a_traiter.json"), state["a_traiter"])
                state["reponses"] = state["reponses"][-300:]
                if not gm.DRY:
                    gm.jsave(gm.P("glm", "etat.json"), state)
            bilan["erreurs"] = len(gm.erreurs)
    finally:
        gm.PROFIL, gm.glm = ancien_profil, ancien_glm
    bilan["appels"] = ctx.appels_graph
    bilan["quota_pct"] = ctx.usage["pct"]
    bilan["quota_ia_atteint"] = quota.atteint
    return bilan


def pause_apres(bilan, etat):
    """Secondes de pause avant le prochain cycle de cette Page ; met à jour ses échecs."""
    if bilan["statut"] == "ok":
        etat.echecs = 0
        pct = bilan.get("quota_pct", 0)
        return 900 if pct >= 90 else 300 if pct >= 75 else 0
    etat.echecs += 1
    if bilan["statut"] == "jeton":
        return 3600
    if bilan["statut"] == "limite":
        return min(3600, 300 * 2 ** min(etat.echecs - 1, 3))
    return min(1800, 60 * 2 ** min(etat.echecs - 1, 5))


def executer_cycle(gm, contextes, cfg, etats, sauver=None, maintenant=time.time, silencieux=True):
    """Un passage sur toutes les Pages. `etats` (dict) garde la mémoire d'une Page d'un cycle à l'autre.
    Renvoie la liste des bilans (compteurs et étiquettes seulement)."""
    bilans = []
    for ctx in contextes:
        etat = etats.setdefault((ctx.uid, ctx.page_id), EtatPage())
        if maintenant() < etat.pause_jusqua:
            bilans.append({"etiquette": ctx.etiquette, "statut": "pause", "reponses": 0,
                           "a_traiter": 0, "erreurs": 0})
            continue
        try:
            bilan = traiter_page(gm, ctx, cfg, sauver=sauver, silencieux=silencieux)
        except Exception as exc:   # isolation : une Page ne bloque jamais les autres
            bilan = {"etiquette": ctx.etiquette, "statut": "exception", "reponses": 0,
                     "a_traiter": 0, "erreurs": 1, "type": type(exc).__name__}
        etat.pause_jusqua = maintenant() + pause_apres(bilan, etat)
        bilans.append(bilan)
    traitees = [b for b in bilans if b["statut"] != "pause"]
    print(f"Multi-Pages : {len(traitees)} Page(s) traitée(s), "
          f"{sum(b['reponses'] for b in bilans)} réponse(s), "
          f"{sum(1 for b in traitees if b['statut'] != 'ok')} en erreur, "
          f"{len(bilans) - len(traitees)} en pause.", flush=True)
    return bilans
