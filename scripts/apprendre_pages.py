#!/usr/bin/env python3
"""Job quotidien multi-Pages (parties E et F), lancé par apprendre.yml.

Pour chaque Page active (sauf la Page n°1) :
  E : proposition de profil (lecture des posts + IA) si absente, ancienne ou redemandée ;
      écrite avec etat = "a_valider", jamais appliquée au profil.
  F : au plus 3 questions par jour au propriétaire (champs vides, commentaires sans réponse).
Quota : au plus IA_MAX_JOUR appels IA par Page et par jour, dans la limite du quota du jour ;
comptés dans le champ `apprentissage` du document quota (le compteur du bot live n'est pas touché).
Dépôt PUBLIC : identifiants masqués, sortie jetée pendant le traitement, une seule ligne de compteurs.
Désactivé tant que MULTI_PAGES n'est pas à on.
"""
import contextlib
import datetime as dt
import os

import multi_pages as mp
import pages_budget
import pages_registry as pr
import pages_schema as sc
import pages_sync
import pages_tokens as pt
import profil_page as pp
import profil_proposition as ppr
import questions_page as qp

IA_MAX_JOUR = 2        # proposition + une question
LECTURE_MAX = 30


def requete_page(collection, page_id, limite=LECTURE_MAX):
    """Requête sur un groupe de collections, filtrée par pageId (index de groupe requis)."""
    return {
        "from": [{"collectionId": collection, "allDescendants": True}],
        "where": {"fieldFilter": {"field": {"fieldPath": "pageId"}, "op": "EQUAL",
                                  "value": {"stringValue": str(page_id)}}},
        "limit": int(limite),
    }


def docs_de(fs, collection, uid, page_id):
    """Documents users/{uid}/{collection}/* de la Page (ceux d'un autre utilisateur sont écartés)."""
    marque = f"/documents/users/{uid}/{collection}/"
    return [pr.decoder_champs(d.get("fields")) for d in fs.requete(requete_page(collection, page_id))
            if marque in str(d.get("name") or "")]


class QuotaJob:
    def __init__(self, restant):
        self.restant = max(0, int(restant))
        self.utilise = 0

    def envelopper(self, glm):
        def appel(cfg, system, user, temperature=0.7, max_tokens=1500):
            if self.utilise >= self.restant:
                raise RuntimeError("quota IA atteint")
            self.utilise += 1
            return glm(cfg, system, user, temperature, max_tokens)
        return appel


def traiter_page(gm, fs, ctx, sync, rec, cfg, maintenant):
    """Une Page : met les écritures en file dans `sync`. Renvoie des compteurs."""
    uid, page_id = rec["uid"], rec["page_id"]
    bilan = {"proposition": 0, "questions": 0, "appels_ia": 0}
    jour = sc.jour_utc(maintenant)
    profil = pp.nettoyer(fs.lire_document(sc.chemin_profil(uid, page_id)) or {})
    q = fs.lire_document(sc.chemin_quota(uid, page_id, jour)) or {}
    deja = int(q.get("apprentissage") or 0)
    marge = int(q.get("limite") or mp.QUOTA_IA_DEFAUT) - int(q.get("utilise") or 0) - deja
    quota = QuotaJob(min(IA_MAX_JOUR - deja, marge))
    glm = quota.envelopper(gm.glm)

    if quota.restant and ppr.a_rafraichir(fs.lire_document(pp.chemin_proposition(uid, page_id)), maintenant):
        jeton = pt.lire_jeton_page(ctx, fs, uid, page_id)
        if jeton:
            ok, textes = ppr.lire_posts(lambda url: gm.http(url), gm.GRAPH, jeton, page_id)
            res = ppr.analyser(glm, cfg, textes) if ok else None
            if res:
                sync.ajouter(pp.chemin_proposition(uid, page_id),
                             ppr.champs_proposition(res, maintenant, len(textes)), prioritaire=True)
                bilan["proposition"] = 1

    existantes = docs_de(fs, "questions", uid, page_id)
    commentaires = [d.get("texte") for d in docs_de(fs, "inbox", uid, page_id)
                    if d.get("repondu") is False and d.get("texte")]
    for qid, champs in qp.generer(glm, cfg, page_id, profil, existantes, commentaires, maintenant):
        sync.ajouter(pp.chemin_question(uid, qid), champs)
        bilan["questions"] += 1

    if quota.utilise:
        sync.ajouter(sc.chemin_quota(uid, page_id, jour),
                     {"pageId": str(page_id), "jour": jour, "apprentissage": deja + quota.utilise})
    bilan["appels_ia"] = quota.utilise
    return bilan


def main(env=None):
    env = os.environ if env is None else env
    if not mp.multi_pages_actif(env):
        print("Apprentissage multi-Pages : désactivé (MULTI_PAGES=off).", flush=True)
        return 0
    import glm_manager as gm
    try:
        fs = pr.depuis_env(env)
        ctx = pt.contexte_depuis_env(env, fs, lambda url: gm.http(url))
    except Exception as exc:
        print(f"Apprentissage multi-Pages : démarrage impossible ({type(exc).__name__}).", flush=True)
        return 0
    registre = pr.RegistrePages(fs)
    registre.rafraichir(forcer_complet=True)
    page1 = str(env.get("FB_PAGE_ID") or gm.PAGE_ID or "")
    cfg = gm.jload(gm.P("glm", "config.json"), {})
    sync = pages_sync.Synchro(fs, pages_budget.BudgetEcritures(), dry_run=bool(gm.DRY))
    maintenant = dt.datetime.now(dt.timezone.utc)
    total = {"pages": 0, "proposition": 0, "questions": 0, "appels_ia": 0, "echecs": 0}
    poubelle = mp._Poubelle()
    for rec in registre.actives():
        if rec["page_id"] == page1:
            continue
        ctx.masquer(rec["uid"])
        ctx.masquer(rec["page_id"])
        total["pages"] += 1
        try:
            with contextlib.redirect_stdout(poubelle), contextlib.redirect_stderr(poubelle):
                bilan = traiter_page(gm, fs, ctx, sync, rec, cfg, maintenant)
            for cle, valeur in bilan.items():
                total[cle] += valeur
        except Exception:      # isolation : une Page ne bloque jamais les autres
            total["echecs"] += 1
    envoi = sync.vider()
    print(f"Apprentissage multi-Pages : {total['pages']} Page(s), {total['proposition']} proposition(s), "
          f"{total['questions']} question(s), {total['appels_ia']} appel(s) IA, {total['echecs']} en échec, "
          f"{envoi['envoyes']} écriture(s), {envoi['restants']} en attente.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
