#!/usr/bin/env python3
"""Veille continue Facebook : détecte les commentaires et réponses imbriquées.

Chef d'orchestre unique : c'est la seule tâche qui lit Facebook en continu.
- lit les commentaires avec un cache (voir lecture_cache.py) et surveille le quota d'appels ;
- répond aux commentaires (logique inchangée : glm_manager.gerer_reponses) ;
- écrit inbox/commentaires.json pour l'app (remplace « Surveiller la Page ») ;
- exécute les files d'attente de l'app (publier, programmer, répondre, modifier/supprimer) ;
- met à jour inbox/statistiques.json toutes les STATS_HEURES heures.
FILE_ATTENTE=workflows rend les files d'attente aux anciens workflows (retour arrière).
"""
import datetime as dt
import os
import subprocess
import sys
import time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import glm_manager as gm
import lecture_cache
import lecture_messages
import file_attente
import taches_periodiques
import aimes_auto
import sante
DUREE = int(os.environ.get("DUREE_MINUTES") or 330) * 60
INTERVALLE = max(20, int(os.environ.get("INTERVALLE_SECONDS") or 60))
BALAYAGE_MINUTES = int(os.environ.get("BALAYAGE_MINUTES") or 20)
STATS_HEURES = float(os.environ.get("STATS_HEURES") or 6)
PUSH_CACHE_SECONDES = 300
PAGE_INFO_SECONDES = 600
# Messenger : un appel de liste toutes les MESSAGES_SECONDES (doublé si quota >= 50 %, suspendu >= 75 %).
MESSAGES_SECONDES = max(30, int(os.environ.get("MESSAGES_SECONDES") or 120))
PUSH_MESSAGES_SECONDES = 120
FILE_ATTENTE_ACTIVE = (os.environ.get("FILE_ATTENTE") or "glm") != "workflows"
DRY = gm.DRY
DEBUT = time.time()
CODES_JETON = {102, 190, 463, 467}
# 4, 17, 32, 613 : limites classiques ; 80000 à 80014 : limites « cas d'usage » (80001 = Pages).
CODES_LIMITE = {4, 17, 32, 613} | set(range(80000, 80015))
CODES_BLOCAGE = {368}  # Facebook a temporairement bloqué une action (politique anti-spam)

CHEMINS_PAR_DEFAUT = ("glm/etat.json", "inbox/a_traiter.json", "inbox/commentaires-traites.json",
                      "posts/replies.json", "inbox/commentaires.json", "inbox/statistiques.json",
                      "inbox/messages.json", "posts/messages.json")
CODES_PERMISSION = {10, 200, 299}  # pages_messaging absente ou non approuvée

def sortie_github(cle, valeur):
    chemin = os.environ.get("GITHUB_OUTPUT")
    if chemin:
        with open(chemin, "a", encoding="utf-8") as f: f.write(f"{cle}={valeur}\n")

def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=gm.ROOT)

def enregistrer(message, chemins=None):
    """Commit + push des fichiers de données. Renvoie True si tout est enregistré."""
    if DRY: return True
    git("config", "user.name", "github-actions")
    git("config", "user.email", "actions@users.noreply.github.com")
    for chemin in (chemins or CHEMINS_PAR_DEFAUT):
        if os.path.exists(os.path.join(gm.ROOT, chemin)): git("add", chemin)
    if git("diff", "--cached", "--quiet").returncode == 0: return True
    commit = git("commit", "-m", message)
    if commit.returncode != 0:
        print("Avertissement : commit impossible : " + commit.stderr[-500:]); return False
    for _ in range(3):
        pull = git("pull", "--rebase", "--autostash")
        if pull.returncode == 0 and git("push").returncode == 0: return True
        time.sleep(5)
    print("Avertissement : push impossible, état conservé sur le runner.")
    return False

def synchroniser():
    """Récupère les nouveautés du dépôt de données (éléments ajoutés par l'app)."""
    if DRY: return True
    return git("pull", "--rebase", "--autostash", "-q").returncode == 0

def signature(posts):
    """Empreinte de chaque commentaire, à tous les niveaux de l'arbre."""
    elements = []
    for post in posts:
        for comment in (post.get("comments") or {}).get("data") or []:
            cid = str(comment.get("id") or "")
            if cid:
                elements.append((cid, str(comment.get("parent_id") or ""), int(comment.get("comment_count") or 0)))
    return tuple(sorted(elements))

def silence_actif(now=None):
    """Vrai pendant les heures de silence réglées dans l'appli
    (glm/config.json, clé « horaires » : actif, debut, fin, decalage_min).
    Lu à chaque cycle : un changement dans l'appli s'applique sans attendre
    la session suivante. Les heures sont celles du téléphone de l'utilisateur,
    converties avec le décalage enregistré ; la plage peut passer minuit."""
    h = ((gm.jload(gm.P("glm", "config.json"), {}) or {}).get("horaires") or {})
    if not h.get("actif"):
        return False
    try:
        debut = str(h.get("debut") or "")
        fin = str(h.get("fin") or "")
        if ":" not in debut or ":" not in fin:
            return False
        a = int(debut.split(":")[0]) * 60 + int(debut.split(":")[1])
        b = int(fin.split(":")[0]) * 60 + int(fin.split(":")[1])
        if a == b:
            return False
        decalage = dt.timedelta(minutes=int(h.get("decalage_min") or 0))
        moment = (now or dt.datetime.now(dt.timezone.utc)) + decalage
        n = moment.hour * 60 + moment.minute
        return (a <= n < b) if a < b else (n >= a or n < b)
    except Exception:
        return False


def passe(cfg, state, posts):
    gm.journal.clear(); gm.erreurs.clear()
    gm.NOW = dt.datetime.now(dt.timezone.utc)
    envoyees, humain = gm.gerer_reponses(cfg, state, posts)
    if humain and not DRY:
        state["a_traiter"] = (state["a_traiter"] + humain)[-100:]
        gm.jsave(gm.P("inbox", "a_traiter.json"), state["a_traiter"])
    state["reponses"] = state["reponses"][-300:]
    if not DRY: gm.jsave(gm.P("glm", "etat.json"), state)
    return envoyees, humain

def dormir(secondes):
    reste = DUREE - (time.time() - DEBUT)
    if reste > 0: time.sleep(min(secondes, reste))

def pause_selon_quota(base):
    """Ralentit tout seul quand Facebook annonce que le quota se remplit."""
    pct = gm.USAGE["pct"]
    if pct >= 90: return 900
    if pct >= 75: return max(base, 300)
    if pct >= 50: return max(base, base * 2)
    return base

def pause_apres_erreur(code, statut, echecs):
    if code in CODES_BLOCAGE:
        return 1800
    if code in CODES_LIMITE or statut == 429:
        attente = 300 * (2 ** min(echecs - 1, 3))          # 5, 10, 20, 40 min
        attente = max(attente, int(gm.USAGE["attente_min"] or 0) * 60)  # délai annoncé par Facebook
        return min(attente, 3600)
    return min(300, INTERVALLE * echecs)

def lire_posts(lecteur):
    try:
        return 200, {"data": lecteur.lire()}
    except gm.comment_store.FacebookError as exc:
        gm.log(str(exc))
        return exc.status or 0, {"error": {"message": str(exc), "code": exc.code, "error_subcode": exc.subcode}}
    except RuntimeError as exc:
        gm.log(str(exc))
        return 0, {"error": {"message": str(exc)}}

class CacheApp:
    """Garde inbox/commentaires.json à jour pour l'app, sans inonder le dépôt de commits."""
    def __init__(self):
        self.page = None; self.page_lue = 0.0
        self.modifie = False; self.dernier_push = 0.0

    def actualiser(self, posts):
        if (self.page is None or time.time() - self.page_lue >= PAGE_INFO_SECONDES) and gm.USAGE["pct"] < 75:
            lu = lecture_cache.lire_page(lambda url: gm.http(url), gm.GRAPH, gm.PAGE_ID, gm.TOKEN)
            if lu: self.page = lu
            self.page_lue = time.time()
        quand = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        nouveau = lecture_cache.construire_cache_commentaires(posts, self.page, gm.PAGE_ID, quand)
        chemin = gm.P("inbox", "commentaires.json")
        ancien = gm.jload(chemin, None)
        if ancien is None or lecture_cache.sans_horodatage(ancien) != lecture_cache.sans_horodatage(nouveau):
            gm.jsave(chemin, nouveau)
            self.modifie = True

    def pousser(self, force=False):
        if not self.modifie: return
        if force or time.time() - self.dernier_push >= PUSH_CACHE_SECONDES:
            if enregistrer("Cache commentaires", ["inbox/commentaires.json"]):
                self.modifie = False
            self.dernier_push = time.time()

class MessagesApp:
    """Garde inbox/messages.json à jour pour l'app (Messenger de la Page).
    Isolé : une erreur ici met seulement Messenger en pause, jamais la veille des commentaires."""
    def __init__(self, lecteur):
        self.lecteur = lecteur
        self.dernier = 0.0; self.dernier_push = 0.0
        self.pause_jusqua = 0.0
        self.modifie = False; self.actif = True

    def actualiser(self):
        if not self.actif: return
        maintenant = time.time()
        pct = gm.USAGE["pct"]
        if maintenant < self.pause_jusqua or pct >= 75: return
        if maintenant - self.dernier < MESSAGES_SECONDES * (2 if pct >= 50 else 1): return
        self.dernier = maintenant
        appels_avant = gm.APPELS["graph"]
        try:
            conversations = self.lecteur.lire()
        except gm.comment_store.FacebookError as exc:
            if exc.code in CODES_PERMISSION:
                print(f"Messenger : permission refusée (code {exc.code}). Il faut pages_messaging sur le jeton de Page : "
                      "lecture désactivée jusqu'à la prochaine session.", flush=True)
                self.actif = False
            elif exc.code in CODES_LIMITE or exc.status == 429:
                print("Messenger : limite d'appels signalée par Facebook, pause de 15 min.", flush=True)
                self.pause_jusqua = maintenant + 900
            else:
                print(f"Messenger : lecture impossible (HTTP {exc.status}, code {exc.code}), nouvel essai dans 5 min.", flush=True)
                self.pause_jusqua = maintenant + 300
            return
        quand = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        nouveau = lecture_messages.construire_cache_messages(conversations, gm.PAGE_ID, quand)
        chemin = gm.P("inbox", "messages.json")
        ancien = gm.jload(chemin, None)
        if ancien is None or lecture_messages.sans_horodatage(ancien) != lecture_messages.sans_horodatage(nouveau):
            gm.jsave(chemin, nouveau)
            self.modifie = True
        s = self.lecteur.stats
        print(f"Messenger : {len(conversations)} conversation(s) — {s['relues']} relue(s), {s['reutilisees']} en cache — "
              f"appels Facebook : {gm.APPELS['graph'] - appels_avant} — quota utilisé : {gm.USAGE['pct']} %", flush=True)

    def pousser(self, force=False):
        if not self.modifie: return
        if force or time.time() - self.dernier_push >= PUSH_MESSAGES_SECONDES:
            if enregistrer("Cache messages", ["inbox/messages.json"]):
                self.modifie = False
            self.dernier_push = time.time()

def configuration_effective(cfg, auto_reponses=None):
    """Applique le verrou GitHub AUTO_REPONSES sans désactiver les autres tâches du bot."""
    resultat = dict(cfg) if isinstance(cfg, dict) else {}
    verrou = str(auto_reponses if auto_reponses is not None else os.environ.get("AUTO_REPONSES", "on")).strip().lower()
    if verrou == "off":
        reponses = resultat.get("reponses")
        reponses = dict(reponses) if isinstance(reponses, dict) else {}
        reponses["actif"] = False
        resultat["reponses"] = reponses
    return resultat


def main():
    cfg = configuration_effective(gm.jload(gm.P("glm", "config.json"), {}))
    if not cfg.get("actif", True):
        print('GLM désactivé (glm/config.json, "actif": false).'); sortie_github("relance", "non"); return 0
    if not (gm.PAGE_ID and gm.TOKEN):
        print("FB_PAGE_ID ou FB_PAGE_TOKEN manquant."); sortie_github("relance", "non"); return 1
    state = gm.jload(gm.P("glm", "etat.json"), {})
    for cle in ("posts", "reponses", "a_traiter"): state.setdefault(cle, [])
    state["comment_ledger"] = gm.comment_store.load_ledger(gm.P("inbox", "commentaires-traites.json"), (gm.P("inbox", "auto-reponses.json"), gm.P("glm", "etat.json")))
    lecteur = lecture_cache.LecteurCache(gm.PAGE_ID, gm.TOKEN, gm.GRAPH, lambda url: gm.http(url),
                                         balayage_minutes=BALAYAGE_MINUTES, journal=gm.log)
    cache_app = CacheApp()
    messages_app = MessagesApp(lecture_messages.LecteurMessages(
        gm.PAGE_ID, gm.TOKEN, gm.GRAPH, lambda url: gm.http(url), journal=gm.log))
    print(f"Veille démarrée ({'TEST' if DRY else 'RÉEL'}) : {DUREE // 60} min, contrôle toutes les {INTERVALLE} s, "
          f"balayage complet toutes les {BALAYAGE_MINUTES} min, "
          f"files d'attente {'gérées ici' if FILE_ATTENTE_ACTIVE else 'laissées aux workflows'}.")
    if FILE_ATTENTE_ACTIVE and not DRY:
        file_attente.recuperer_interrompus(gm, enregistrer)
    stats_a_jour = taches_periodiques.statistiques_a_jour(gm, STATS_HEURES)
    derniere_stats = time.time() if stats_a_jour else 0.0
    session_sante = {
        "debut": dt.datetime.fromtimestamp(DEBUT, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duree_min": DUREE // 60,
    }
    echecs = 0; total_reponses = 0; cycles = 0; silence_precedent = None
    while time.time() - DEBUT < DUREE:
        try:
            cycles += 1
            session_sante["cycles"] = cycles
            session_sante["reponses"] = total_reponses
            appels_avant = gm.APPELS["graph"]
            statut, res = lire_posts(lecteur)
            if statut != 200:
                err = (res or {}).get("error") or {}; code = err.get("code")
                detail = str(err.get("message") or err.get("erreur") or "réponse Facebook invalide")[:300]
                print(f"ERREUR FACEBOOK — lecture des publications/commentaires : HTTP {statut}, code {code}, sous-code {err.get('error_subcode')} — {detail}", flush=True)
                if code in CODES_JETON:
                    print("Jeton Facebook invalide ou expiré : renouvellement nécessaire.")
                    sante.maj(gm, session=session_sante, echecs=echecs, extra=(detail,))
                    sante.pousser(gm, enregistrer, force=True)
                    sortie_github("relance", "non"); return 1
                echecs += 1
                sante.maj(gm, session=session_sante, echecs=echecs, extra=(detail,))
                sante.pousser(gm, enregistrer)
                attente = pause_apres_erreur(code, statut, echecs)
                if attente >= 300: print(f"Pause de {attente // 60} min avant de réessayer (limite ou blocage signalé par Facebook).", flush=True)
                dormir(attente)
                continue
            echecs = 0
            posts = res.get("data", [])
            nombre = sum(len((post.get("comments") or {}).get("data") or []) for post in posts)
            s = lecteur.stats
            print(f"Analyse : {len(posts)} publication(s), {nombre} commentaire(s)/sous-commentaire(s) — "
                  f"{s['relus']} relue(s), {s['reutilises']} en cache{' (balayage complet)' if s['balayage'] else ''} — "
                  f"appels Facebook : {gm.APPELS['graph'] - appels_avant} — quota utilisé : {gm.USAGE['pct']} %", flush=True)
            # Chaque cycle réexamine tout l'arbre. Le registre partagé écarte les succès
            # déjà traités ; les erreurs ne sont pas inscrites au registre et seront retentées.
            # Réponses automatiques : suspendues pendant les heures de silence
            # réglées dans l'appli. Le reste du cycle continue (caches, messages,
            # files d'attente, statistiques) et la santé signale l'état normal.
            en_silence = silence_actif()
            if en_silence != silence_precedent:
                print(
                    "Heures de silence : les réponses automatiques sont suspendues "
                    "jusqu'à la fin de la plage (caches et envois en file continuent)."
                    if en_silence
                    else "Fin des heures de silence : les réponses automatiques reprennent.",
                    flush=True,
                )
                silence_precedent = en_silence
            envoyees, humain = ([], []) if en_silence else passe(cfg, state, posts)
            total_reponses += len(envoyees)
            print(f"-> {len(envoyees)} réponse(s) envoyée(s), {len(humain)} à traiter par un humain, {len(gm.erreurs)} erreur(s) à retenter.", flush=True)
            if envoyees or humain:
                enregistrer("GLM : réponses aux commentaires")
            elif gm.erreurs:
                # Persister les succès éventuels et les éléments humains même si un autre commentaire a échoué.
                enregistrer("GLM : état partiel des commentaires")
            cache_app.actualiser(posts)
            cache_app.pousser()
            try:
                messages_app.actualiser()
                messages_app.pousser()
            except Exception as exc:  # Messenger ne doit jamais bloquer les commentaires ni les files d'attente
                print(f"Messenger : erreur inattendue ({type(exc).__name__}), ignorée.", flush=True)
            if FILE_ATTENTE_ACTIVE:
                if not synchroniser(): print("Avertissement : synchronisation du dépôt de données impossible.", flush=True)
                traites = file_attente.traiter(gm, enregistrer, dry=DRY)
                if traites: print(f"-> {traites} élément(s) de la file d'attente traité(s).", flush=True)
                aimes = aimes_auto.traiter(gm, enregistrer, dry=DRY)
                if aimes: print(f"-> {aimes} publication(s) aimée(s) automatiquement.", flush=True)
            sante.maj(gm, session=session_sante, echecs=echecs)
            sante.pousser(gm, enregistrer)
            if time.time() - derniere_stats >= STATS_HEURES * 3600 and gm.USAGE["pct"] < 75:
                if not DRY:
                    lus, voulus = taches_periodiques.ecrire_statistiques(gm)
                    print(f"Statistiques : {lus} indicateur(s) lu(s) sur {voulus}.", flush=True)
                    enregistrer("Statistiques", ["inbox/statistiques.json"])
                derniere_stats = time.time()
        except Exception as e:
            echecs += 1
            print(f"ERREUR BOUCLE ({type(e).__name__}) : {str(e)[:300]}", flush=True)
            sante.maj(gm, session=session_sante, echecs=echecs,
                      extra=(f"{type(e).__name__}: {str(e)[:160]}",))
            sante.pousser(gm, enregistrer)
            dormir(min(300, INTERVALLE * echecs))
            continue
        attente = pause_selon_quota(INTERVALLE)
        if attente > INTERVALLE: print(f"Quota à {gm.USAGE['pct']} % : cycle suivant dans {attente} s.", flush=True)
        dormir(attente)
    cache_app.pousser(force=True)
    messages_app.pousser(force=True)
    sante.maj(gm, session=session_sante, echecs=echecs)
    sante.pousser(gm, enregistrer, force=True)
    enregistrer("GLM : fin de session")
    print(f"Session terminée : {total_reponses} réponse(s) envoyée(s).")
    sortie_github("relance", "oui"); return 0

if __name__ == "__main__": sys.exit(main())
