#!/usr/bin/env python3
"""Cycle multi-Pages complet : registre Firestore -> demandes -> jetons -> bot par Page.

Un appel à Runner.cycle() :
  1. rafraîchit le registre (lectures incrémentales) ;
  2. traite les demandes « connecter » / « deconnecter » (écriture atomique par demande) ;
  3. construit un ContextePage par Page active (jeton de Page lu dans bot_tokens, gardé en mémoire) ;
  4. appelle multi_pages.executer_cycle (isolation par Page).

Dépôt public : seuls des compteurs sont affichés. La Page n°1 (page1_id) n'est jamais servie ici.
Les écritures du bot ne touchent pas `maj` (pas de boucle avec l'appli).
"""
import contextlib
import datetime as dt
import time

import multi_pages as mp
import page_context as pc
import pages_file as pf
import pages_registry as pr
import pages_schema as sc
import pages_tokens as pt

RETARD_SECONDES = 300   # délai avant de retenter une demande en échec temporaire ou refusée
INTERVALLE_BASE = 120   # secondes entre deux cycles ; sert à calculer le ralentissement du budget
LIMITE_FILE_PAGE = 3    # publications par Page et par cycle (limite de débit)
MSG_PAGE = "Page non connectée."
PROFIL_TTL = 900        # le profil d'une Page est relu au plus toutes les 15 min (économie de lectures)


def _http_silencieux(gm, url, data):
    """Appel Facebook de publication, sortie jetée (dépôt public : pas d'adresse ni d'identifiant dans les logs)."""
    poubelle = mp._Poubelle()
    with contextlib.redirect_stdout(poubelle), contextlib.redirect_stderr(poubelle):
        return gm.http(url, data)


class Runner:
    def __init__(self, fs, ctx_tokens, base_dir, page1_id="", quota_ia=mp.QUOTA_IA_DEFAUT,
                 maintenant=time.time, sync=None):
        self.fs = fs
        self.ctx_tokens = ctx_tokens
        self.base_dir = base_dir
        self.page1_id = str(page1_id or "")
        self.quota_ia = int(quota_ia)
        self._now = maintenant
        self.registre = pr.RegistrePages(fs, maintenant=maintenant)
        self.etats = {}      # mémoire d'exécution par Page (pauses, échecs)
        self.jetons = {}     # jetons de Page en clair, en mémoire seulement
        self.quotas = {}     # quota IA du jour par Page
        self.retards = {}    # Page -> heure avant laquelle on ne retente pas la demande
        self.profils = {}    # Page -> (heure de lecture, profil) ; users/{uid}/profil/{pageId}
        self.jour = None
        self.sync = sync     # pages_sync.Synchro (écritures groupées sous budget), ou None
        self.cumuls = {}     # Page -> compteurs cumulés du jour UTC pour les stats
        self.masques = set()  # identifiants déjà masqués dans les logs Actions
        self.dry_pages = set()  # Pages en mode test (profil dryRun) : aucune publication ni réponse réelle
        self.faits = {}      # (uid, élément) -> (chemin, champs, supprimer) : publié, résultat pas encore écrit

    def _quota(self, cle):
        jour = dt.datetime.fromtimestamp(self._now(), dt.timezone.utc).strftime("%Y-%m-%d")
        if jour != self.jour:
            self.jour = jour
            self.quotas.clear()
        return self.quotas.setdefault(cle, mp.QuotaIA(self.quota_ia))

    def traiter_demandes(self):
        bilan = {"demandes": 0, "connectees": 0, "deconnectees": 0, "erreurs": 0, "attente": 0, "refus": 0}
        for rec in self.registre.a_traiter():
            cle = (rec["uid"], rec["page_id"])
            if rec["page_id"] == self.page1_id or self._now() < self.retards.get(cle, 0.0):
                continue
            bilan["demandes"] += 1
            try:
                res = pt.preparer_demande(self.ctx_tokens, rec)
                if res.issue == "ignoree":
                    continue
                if res.issue == "attente":
                    bilan["attente"] += 1
                    self.retards[cle] = self._now() + RETARD_SECONDES
                    continue
                self.fs.commit(res.ecritures)
            except Exception:
                bilan["refus"] += 1
                self.retards[cle] = self._now() + RETARD_SECONDES
                continue
            self.retards.pop(cle, None)
            champs = {"demande": None, "tokenEnc": None}
            if res.issue == "active":
                champs.update(status="active", message=None)
                self.jetons[cle] = res.jeton_page
                bilan["connectees"] += 1
            elif res.issue == "deconnectee":
                champs.update(status="deconnectee", message=None)
                self.jetons.pop(cle, None)
                bilan["deconnectees"] += 1
            else:
                champs.update(status="erreur", message=res.message)
                self.jetons.pop(cle, None)
                bilan["erreurs"] += 1
            self.registre.appliquer_local(rec["uid"], rec["page_id"], champs)
        return bilan

    def _profil(self, cle, rec):
        """Profil de la Page (users/{uid}/profil/{pageId}), gardé en mémoire PROFIL_TTL secondes.
        En cas d'erreur Firestore, on garde l'ancien profil s'il existe."""
        memo = self.profils.get(cle)
        if memo and self._now() - memo[0] < PROFIL_TTL:
            return memo[1]
        try:
            doc = self.fs.lire_document(sc.chemin_profil(rec["uid"], rec["page_id"]))
        except pr.FirestoreError:
            if memo:
                return memo[1]
            doc = None
        profil = {k: v for k, v in doc.items() if v is not None} if isinstance(doc, dict) else {}
        self.profils[cle] = (self._now(), profil)
        return profil

    def contextes(self):
        """(contextes des Pages actives, nombre de Pages actives sans jeton lisible)."""
        contextes, sans_jeton = [], 0
        actives = [r for r in self.registre.actives() if r["page_id"] != self.page1_id]
        for numero, rec in enumerate(actives, start=2):
            cle = (rec["uid"], rec["page_id"])
            jeton = self.jetons.get(cle)
            if not jeton:
                try:
                    jeton = pt.lire_jeton_page(self.ctx_tokens, self.fs, rec["uid"], rec["page_id"])
                except pr.FirestoreError:
                    jeton = None
                if not jeton:
                    sans_jeton += 1
                    continue
                self.jetons[cle] = jeton
            profil = dict(rec["profil"]) if isinstance(rec.get("profil"), dict) else {}
            if isinstance(rec.get("description"), str):
                profil.setdefault("description", rec["description"])
            profil.update(self._profil(cle, rec))   # le document profil prime
            profil.setdefault("nom", str(rec.get("nom") or ""))
            dry = profil.get("dryRun") is True
            (self.dry_pages.add if dry else self.dry_pages.discard)(cle)
            dossier = pc.dossier_page(self.base_dir, rec["uid"], rec["page_id"])
            contextes.append(pc.ContextePage(rec["uid"], rec["page_id"], jeton, dossier,
                                             etiquette=f"Page {numero}", profil=profil,
                                             quota=self._quota(cle), dry_run=dry))
        return contextes, sans_jeton

    def _ralentir(self):
        """Budget d'écritures dépassé : chaque Page attend plus longtemps avant son prochain cycle."""
        if self.sync is None:
            return
        facteur = self.sync.budget.facteur()
        if facteur <= 1.0:
            return
        fin = self._now() + INTERVALLE_BASE * (facteur - 1.0)
        for etat in self.etats.values():
            etat.pause_jusqua = max(etat.pause_jusqua, fin)

    def _masquer_ids(self):
        """Masque dans les logs Actions les identifiants d'utilisateur et de Page (dépôt public)."""
        masquer = getattr(self.ctx_tokens, "masquer", None)
        if not callable(masquer):
            return
        for uid, page_id in list(self.registre.pages):
            for valeur in (uid, page_id):
                if valeur not in self.masques:
                    self.masques.add(valeur)
                    masquer(valeur)

    def traiter_file(self, gm):
        """Collection `file` : publication, photo, programmation demandées par l'appli.
        Compteurs seulement. Un élément déjà publié n'est jamais republié."""
        bilan = {"lus": 0, "publies": 0, "programmes": 0, "erreurs": 0, "retard": 0}
        if self.sync is None:
            return bilan
        try:
            elements = pf.lister_en_attente(self.fs)
        except pr.FirestoreError:
            bilan["erreurs"] += 1
            return bilan
        maintenant = dt.datetime.fromtimestamp(self._now(), dt.timezone.utc)
        actives = {(r["uid"], r["page_id"]) for r in self.registre.actives() if r["page_id"] != self.page1_id}
        par_page = {}
        for el in elements:
            cle_el = (el["uid"], el["element_id"])
            try:
                chemin = sc.chemin_file(el["uid"], el["element_id"])
            except ValueError:
                continue
            if cle_el in self.faits:                       # déjà publié : on retente seulement l'écriture
                _, champs, supprimer = self.faits[cle_el]
                self.sync.ajouter(chemin, champs, supprimer, prioritaire=True)
                continue
            bilan["lus"] += 1
            v = pf.valider(el, maintenant)
            if v["etat"] == "ignore":
                continue
            if v["etat"] == "invalide":
                issue, erreur, post_id = "error", v["erreur"], ""
            else:
                cle_page = (el["uid"], v["page_id"])
                if cle_page not in actives:
                    issue, erreur, post_id = "error", MSG_PAGE, ""
                elif cle_page not in self.jetons or par_page.get(cle_page, 0) >= LIMITE_FILE_PAGE:
                    bilan["retard"] += 1
                    continue
                else:
                    par_page[cle_page] = par_page.get(cle_page, 0) + 1
                    res = pf.publier(lambda url, data: _http_silencieux(gm, url, data), gm.GRAPH,
                                     self.jetons[cle_page], v,
                                     dry_run=gm.DRY or cle_page in self.dry_pages)
                    if res["issue"] in ("retard", "dry_run"):
                        bilan["retard"] += 1
                        continue
                    issue, erreur, post_id = res["issue"], res["erreur"], res["post_id"]
            champs, supprimer = sc.champs_file_resultat(issue, maintenant, erreur=erreur, post_id=post_id)
            self.faits[cle_el] = (chemin, champs, supprimer)
            self.sync.ajouter(chemin, champs, supprimer, prioritaire=True)
            bilan[{"published": "publies", "scheduled": "programmes"}.get(issue, "erreurs")] += 1
        self.sync.vider()
        for cle_el in [c for c, (chemin, _, _) in self.faits.items() if chemin not in self.sync.file]:
            del self.faits[cle_el]                          # résultat écrit : plus rien à retenir
        return bilan

    def ecrire_donnees(self, contextes, bilans):
        """Stats du jour (cumul) et quota IA du jour, par Page, via la synchro. Compteurs seulement."""
        if self.sync is None:
            return {}
        jour = sc.jour_utc(dt.datetime.fromtimestamp(self._now(), dt.timezone.utc))
        for ctx, bilan in zip(contextes, bilans):
            cle = (ctx.uid, ctx.page_id)
            cumul = self.cumuls.get(cle)
            if not cumul or cumul["jour"] != jour:
                cumul = self.cumuls[cle] = {"jour": jour, "reponses": 0, "a_traiter": 0, "erreurs": 0}
            if bilan.get("statut") != "pause":
                for nom in ("reponses", "a_traiter", "erreurs"):
                    cumul[nom] += int(bilan.get(nom) or 0)
            valeurs = {nom: cumul[nom] for nom in ("reponses", "a_traiter", "erreurs")}
            self.sync.ajouter(sc.chemin_stats(ctx.uid, ctx.page_id, jour),
                              sc.champs_stats(ctx.page_id, jour, valeurs))
            for doc_id, champs in getattr(ctx, "inbox", None) or []:
                self.sync.ajouter(sc.chemin_inbox(ctx.uid, ctx.page_id, doc_id), champs)
            quota = ctx.quota
            if quota is not None:
                self.sync.ajouter(sc.chemin_quota(ctx.uid, ctx.page_id, jour),
                                  sc.champs_quota(ctx.page_id, jour, quota.utilise, quota.limite))
        return self.sync.vider()

    def cycle(self, gm, cfg):
        """Un passage complet. Renvoie un dict de compteurs (aucun identifiant)."""
        try:
            self.registre.rafraichir()
        except pr.FirestoreError as exc:
            print(f"Multi-Pages : lecture Firestore impossible (HTTP {exc.status}).", flush=True)
            return {"statut": "firestore", "bilans": []}
        except Exception as exc:
            print(f"Multi-Pages : erreur inattendue ({type(exc).__name__}).", flush=True)
            return {"statut": "exception", "bilans": []}
        self._masquer_ids()
        demandes = self.traiter_demandes()
        contextes, sans_jeton = self.contextes()
        fichier = self.traiter_file(gm)
        bilans = mp.executer_cycle(gm, contextes, cfg, self.etats,
                                   sauver=lambda message, chemins: True, maintenant=self._now)
        self.ecrire_donnees(contextes, bilans)
        self._ralentir()
        r = self.registre.resume()
        print(f"Multi-Pages : registre {r['total']} Page(s), {r.get('active', 0)} active(s), "
              f"{r.get('attente', 0)} en attente ; demandes {demandes['demandes']} "
              f"({demandes['connectees']} connectée(s), {demandes['deconnectees']} déconnectée(s), "
              f"{demandes['erreurs']} en erreur) ; {sans_jeton} sans jeton ; "
              f"Firestore {r['lectures']} lecture(s), {r['ecritures']} écriture(s).", flush=True)
        return {"statut": "ok", "demandes": demandes, "sans_jeton": sans_jeton, "file": fichier, "bilans": bilans}
