#!/usr/bin/env python3
"""Alimente le « cerveau » : lit les kidas.md de GitHub et analyse les performances des posts.

Écrit connaissances/projets.md et connaissances/performance.md, lus par ChatGPT et par GLM.
Seuls les dépôts PUBLICS sont lus : rien de privé ne doit être copié dans ces fichiers.
"""
import collections
import datetime as dt
import json
import os
import urllib.error
import urllib.request

# Données dans le dépôt privé cloné dans data/ (ou DATA_DIR).
ROOT = os.path.abspath(os.environ.get("DATA_DIR") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))
USER = os.environ.get("GITHUB_USER_CIBLE") or "kidasniger"
MAX_PAR_FICHIER = 1500
MAX_TOTAL = 9000
NOMS = ("kidas.md", "KIDAS.md")


def get(url, token=None):
    h = {"User-Agent": "facebook-manager", "Accept": "application/vnd.github+json"}
    if token:
        h["Authorization"] = "Bearer " + token
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r:
            return r.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError):
        return None


def lister_depots(fetch=get, token=None):
    txt = fetch(f"https://api.github.com/users/{USER}/repos?per_page=100&sort=pushed", token)
    try:
        data = json.loads(txt or "[]")
    except ValueError:
        return []
    return [d for d in data if isinstance(d, dict) and not d.get("fork") and not d.get("archived") and not d.get("private")]


def lire_raw(depot, branche, noms, fetch):
    for nom in noms:
        for b in dict.fromkeys([branche or "main", "main", "master"]):
            t = fetch(f"https://raw.githubusercontent.com/{USER}/{depot}/{b}/{nom}")
            if t and t.strip():
                return t.strip()
    return None


def construire_projets(fetch=get, token=None):
    depots = lister_depots(fetch, token)
    blocs, total = [], 0
    profil = lire_raw(USER, None, ("README.md", "readme.md"), fetch)
    if profil:
        blocs.append("## Profil GitHub (README du compte)\n\n" + profil[:MAX_PAR_FICHIER])
    manquants = []
    for d in depots:
        nom = d.get("name")
        if not nom or nom == USER:
            continue
        txt = lire_raw(nom, d.get("default_branch"), NOMS, fetch)
        if not txt:
            manquants.append(nom)
            continue
        bloc = f"## {nom}\n\n" + txt[:MAX_PAR_FICHIER]
        if total + len(bloc) > MAX_TOTAL:
            break
        blocs.append(bloc)
        total += len(bloc)
    tete = ("# Connaissances sur mes projets (généré automatiquement depuis les kidas.md de GitHub)\n\n"
            "Source de vérité pour parler de mes projets. Ne rien inventer en dehors de ce contenu.\n")
    pied = ("\n\n---\nDépôts publics sans kidas.md : " + ", ".join(manquants)) if manquants else ""
    return tete + "\n" + "\n\n".join(blocs) + pied + "\n", len(depots), len(blocs)


def score(p):
    return int(p.get("likes") or 0) + 3 * int(p.get("commentaires") or 0) + 5 * int(p.get("partages") or 0)


def court(p, n=160):
    t = (p.get("texte") or "").replace("\n", " ").strip()
    return (t[:n] + "…") if len(t) > n else (t or "(photo/vidéo sans texte)")


def construire_performance(posts):
    posts = [p for p in posts if isinstance(p, dict)]
    n = len(posts)
    L = ["# Performance des posts (généré automatiquement depuis archive/posts-2026.json)", "",
         "Score = J'aime + 3 × commentaires + 5 × partages (les partages comptent le plus).", ""]
    if n == 0:
        return "\n".join(L + ["Aucune donnée pour l'instant."]) + "\n"
    classes = sorted(posts, key=score, reverse=True)
    moy = sum(score(p) for p in posts) / n
    L.append(f"{n} posts analysés, score moyen {moy:.1f}.")
    if n < 20:
        L.append("⚠️ Peu de données : ce sont des tendances, pas des certitudes.")
    L += ["", "## Meilleurs posts (à imiter : accroche, format, sujet)"]
    for p in classes[:3]:
        L.append(f"- score {score(p)} ({p.get('likes', 0)} J'aime, {p.get('commentaires', 0)} com., {p.get('partages', 0)} partages) : {court(p)}")
    L += ["", "## Moins bons posts (à éviter de reproduire tel quel)"]
    for p in classes[-3:][::-1]:
        L.append(f"- score {score(p)} : {court(p)}")
    par_type = collections.defaultdict(list)
    for p in posts:
        par_type[p.get("type") or "inconnu"].append(score(p))
    L += ["", "## Score moyen par type de publication"]
    for t, v in sorted(par_type.items(), key=lambda kv: -sum(kv[1]) / len(kv[1])):
        L.append(f"- {t} : {sum(v) / len(v):.1f} ({len(v)} posts)")
    jours = collections.defaultdict(list)
    for p in posts:
        try:
            d = dt.datetime.strptime(p["date"][:19], "%Y-%m-%dT%H:%M:%S")
            jours[(d.strftime("%A"), d.hour)].append(score(p))
        except (KeyError, ValueError, TypeError):
            pass
    fiables = {k: v for k, v in jours.items() if len(v) >= 2}
    if n >= 30 and fiables:
        L += ["", "## Créneaux (UTC) classés par score moyen"]
        for (j, h), v in sorted(fiables.items(), key=lambda kv: -sum(kv[1]) / len(kv[1]))[:3]:
            L.append(f"- {j} vers {h} h : {sum(v) / len(v):.1f} ({len(v)} posts)")
    L += ["", "## Consigne", "Reprends ce qui marche (partages > commentaires > J'aime), varie les sujets, ne recopie jamais un ancien post."]
    return "\n".join(L) + "\n"


def ecrire(chemin, texte):
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin, "w", encoding="utf-8") as f:
        f.write(texte)


def main():
    token = os.environ.get("GITHUB_TOKEN")
    projets, nd, nb = construire_projets(get, token)
    if nd == 0:
        print("Aucun dépôt public lu (réseau ou limite GitHub) : projets.md conservé tel quel.")
    else:
        ecrire(os.path.join(ROOT, "connaissances", "projets.md"), projets)
        print(f"projets.md : {nb} kidas.md lus sur {nd} dépôts publics.")
    try:
        with open(os.path.join(ROOT, "archive", "posts-2026.json"), encoding="utf-8") as f:
            posts = json.load(f)
    except (OSError, ValueError):
        posts = []
    ecrire(os.path.join(ROOT, "connaissances", "performance.md"), construire_performance(posts))
    print(f"performance.md : {len(posts)} posts analysés.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
