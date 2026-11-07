#!/usr/bin/env python3
"""Veille continue Facebook : détecte les commentaires et réponses imbriquées."""
import datetime as dt
import os
import subprocess
import sys
import time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import glm_manager as gm
DUREE = int(os.environ.get("DUREE_MINUTES") or 330) * 60
INTERVALLE = max(20, int(os.environ.get("INTERVALLE_SECONDS") or 60))
DRY = gm.DRY
DEBUT = time.time()
CODES_JETON = {102, 190, 463, 467}
CODES_LIMITE = {4, 17, 32, 613}

def sortie_github(cle, valeur):
    chemin = os.environ.get("GITHUB_OUTPUT")
    if chemin:
        with open(chemin, "a", encoding="utf-8") as f: f.write(f"{cle}={valeur}\n")

def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=gm.ROOT)

def enregistrer(message):
    if DRY: return
    git("config", "user.name", "github-actions")
    git("config", "user.email", "actions@users.noreply.github.com")
    for chemin in ("glm/etat.json", "inbox/a_traiter.json", "inbox/commentaires-traites.json", "posts/replies.json"):
        if os.path.exists(os.path.join(gm.ROOT, chemin)): git("add", chemin)
    if git("diff", "--cached", "--quiet").returncode == 0: return
    commit = git("commit", "-m", message)
    if commit.returncode != 0:
        print("Avertissement : commit impossible : " + commit.stderr[-500:]); return
    for _ in range(3):
        pull = git("pull", "--rebase", "--autostash")
        if pull.returncode == 0 and git("push").returncode == 0: return
        time.sleep(5)
    print("Avertissement : push impossible, état conservé sur le runner.")

def signature(posts):
    """Empreinte de chaque commentaire, à tous les niveaux de l'arbre."""
    elements = []
    for post in posts:
        for comment in (post.get("comments") or {}).get("data") or []:
            cid = str(comment.get("id") or "")
            if cid:
                elements.append((cid, str(comment.get("parent_id") or ""), int(comment.get("comment_count") or 0)))
    return tuple(sorted(elements))

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

def main():
    cfg = gm.jload(gm.P("glm", "config.json"), {})
    if not cfg.get("actif", True):
        print('GLM désactivé (glm/config.json, "actif": false).'); sortie_github("relance", "non"); return 0
    if not (gm.PAGE_ID and gm.TOKEN):
        print("FB_PAGE_ID ou FB_PAGE_TOKEN manquant."); sortie_github("relance", "non"); return 1
    state = gm.jload(gm.P("glm", "etat.json"), {})
    for cle in ("posts", "reponses", "a_traiter"): state.setdefault(cle, [])
    state["comment_ledger"] = gm.comment_store.load_ledger(gm.P("inbox", "commentaires-traites.json"), (gm.P("inbox", "auto-reponses.json"), gm.P("glm", "etat.json")))
    print(f"Veille démarrée ({'TEST' if DRY else 'RÉEL'}) : {DUREE // 60} min, contrôle toutes les {INTERVALLE} s.")
    echecs = 0; total_reponses = 0
    while time.time() - DEBUT < DUREE:
        try:
            statut, res = gm.fb_posts()
            if statut != 200:
                err = (res or {}).get("error") or {}; code = err.get("code")
                detail = str(err.get("message") or err.get("erreur") or "réponse Facebook invalide")[:300]
                print(f"ERREUR FACEBOOK — lecture des publications/commentaires : HTTP {statut}, code {code}, sous-code {err.get('error_subcode')} — {detail}", flush=True)
                if code in CODES_JETON:
                    print("Jeton Facebook invalide ou expiré : renouvellement nécessaire."); sortie_github("relance", "non"); return 1
                echecs += 1
                dormir(300 if code in CODES_LIMITE else min(300, INTERVALLE * echecs))
                continue
            echecs = 0
            posts = res.get("data", [])
            nombre = sum(len((post.get("comments") or {}).get("data") or []) for post in posts)
            print(f"Analyse complète : {len(posts)} publication(s), {nombre} commentaire(s)/sous-commentaire(s) récupéré(s).", flush=True)
            # Chaque cycle réexamine tout l'arbre. Le registre partagé écarte les succès
            # déjà traités ; les erreurs ne sont pas inscrites au registre et seront retentées.
            envoyees, humain = passe(cfg, state, posts)
            total_reponses += len(envoyees)
            print(f"-> {len(envoyees)} réponse(s) envoyée(s), {len(humain)} à traiter par un humain, {len(gm.erreurs)} erreur(s) à retenter.", flush=True)
            if envoyees or humain:
                enregistrer("GLM : réponses aux commentaires")
            elif gm.erreurs:
                # Persister les succès éventuels et les éléments humains même si un autre commentaire a échoué.
                enregistrer("GLM : état partiel des commentaires")
        except Exception as e:
            echecs += 1
            print(f"ERREUR BOUCLE ({type(e).__name__}) : {str(e)[:300]}", flush=True)
            dormir(min(300, INTERVALLE * echecs))
            continue
        dormir(INTERVALLE)
    enregistrer("GLM : fin de session")
    print(f"Session terminée : {total_reponses} réponse(s) envoyée(s).")
    sortie_github("relance", "oui"); return 0

if __name__ == "__main__": sys.exit(main())
